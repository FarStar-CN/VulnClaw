"""Exercise real headless processes against a deterministic loopback LLM endpoint."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import signal
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from jsonschema import Draft202012Validator

from vulnclaw.config.schema import BUILTIN_MCP_SERVERS, VulnClawConfig

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = Draft202012Validator(
    json.loads((ROOT / "protocol/session-v2.schema.json").read_text(encoding="utf-8"))
)


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)
        text = json.dumps(body.get("messages", []), ensure_ascii=False)
        has_tool = any(message["role"] == "tool" for message in body.get("messages", []))
        tool = "FIXTURE_TOOL" in text and not has_tool
        answer = (
            "ASK_USER: Which target credentials are authorized?"
            if "FIXTURE_QUESTION" in text and "fixture-answer" not in text
            else "FINAL: fixture-complete 中文"
        )
        if "FIXTURE_WAIT" in text:
            self.server.waiting.set()
            self.server.release.wait(timeout=20)
        if "FIXTURE_RETRY_WAIT" in text:
            self.send_response(503)
            self.send_header("Retry-After", "120")
            self.send_header("Content-Length", "0")
            self.end_headers()
            self.server.waiting.set()
            return
        call = {
            "id": "call-fixture",
            "type": "function",
            "function": {
                "name": "python_execute",
                "arguments": json.dumps({"code": "print('fixture-tool-ok')"}),
            },
        }
        message = {"role": "assistant", "content": None if tool else answer}
        if tool:
            message["tool_calls"] = [call]
        self.send_response(200)
        self.send_header(
            "Content-Type", "text/event-stream" if body.get("stream") else "application/json"
        )
        self.end_headers()
        try:
            if body.get("stream"):
                delta = dict(message)
                if tool:
                    delta["tool_calls"] = [dict(call, index=0)]
                chunk = {
                    "id": "chatcmpl-fixture",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "fixture-model",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                }
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                self.wfile.flush()
                if "FIXTURE_SSE_WAIT" in text:
                    self.server.waiting.set()
                    self.server.release.wait(timeout=20)
                self.wfile.write(b"data: [DONE]\n\n")
            else:
                self.wfile.write(
                    json.dumps(
                        {
                            "id": "chatcmpl-fixture",
                            "object": "chat.completion",
                            "created": 1,
                            "model": "fixture-model",
                            "choices": [
                                {
                                    "index": 0,
                                    "message": message,
                                    "finish_reason": "tool_calls" if tool else "stop",
                                }
                            ],
                            "usage": {
                                "prompt_tokens": 1,
                                "completion_tokens": 1,
                                "total_tokens": 2,
                            },
                        }
                    ).encode()
                )
        except (BrokenPipeError, ConnectionResetError):
            # Cancellation deliberately disconnects an in-flight streaming request.
            pass


@pytest.fixture(scope="module")
def endpoint():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    server.requests = []
    server.waiting = threading.Event()
    server.release = threading.Event()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.release.set()
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture
def machine(tmp_path, endpoint):
    config = VulnClawConfig()
    config.llm.api_key = "fixture-only-key"
    config.llm.provider = "custom"
    config.llm.model = "fixture-model"
    config.llm.base_url = f"http://127.0.0.1:{endpoint.server_port}/v1"
    config.session.auto_save = False
    config.session.experience_enabled = False
    config.session.output_dir = tmp_path / "reports"
    config.session.runs_dir = str(tmp_path / "runs")
    config.session.solve_auto_report = False
    payload = config.model_dump(mode="json")
    payload["mcp"]["servers"] = {name: {"enabled": False} for name in BUILTIN_MCP_SERVERS}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("VULNCLAW_")}
    env.update(
        VULNCLAW_CONFIG_DIR=str(tmp_path),
        VULNCLAW_HOME=str(tmp_path),
        PYTHONPATH=str(ROOT),
        NO_PROXY="127.0.0.1,localhost",
    )
    endpoint.requests.clear()

    def invoke(*args, input=None):
        result = subprocess.run(
            [sys.executable, "-m", "vulnclaw", *args],
            input=input,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=tmp_path,
            timeout=20,
        )
        events = [json.loads(line) for line in result.stdout.splitlines()]
        assert events, result.stderr
        for event in events:
            VALIDATOR.validate(event)
        assert events[-1]["type"] == "command_result"
        assert sum(event["type"] == "command_result" for event in events) == 1
        assert result.returncode == events[-1]["exit_code"]
        assert "\x1b" not in result.stdout
        assert "fixture-only-key" not in result.stdout
        return result, events

    return SimpleNamespace(invoke=invoke, env=env, root=tmp_path, config=payload, path=path)


@pytest.mark.parametrize("format", ["json", "jsonl"])
def test_conversation_process_uses_real_sdk_and_machine_output(machine, endpoint, format):
    result, events = machine.invoke("ask", "Explain fixture greeting", "--format", format)
    assert result.returncode == 0, result.stderr
    assert "fixture-complete 中文" in events[-1]["result"]["output"]
    assert len(endpoint.requests) == 1
    assert (len(events) > 1) is (format == "jsonl")
    assert endpoint.requests[0]["stream"] is True


def test_separate_ask_processes_start_separate_conversations(machine, endpoint):
    machine.invoke("ask", "Explain fixture-previous-turn-123")
    machine.invoke("ask", "Explain another topic")
    assert "fixture-previous-turn-123" not in json.dumps(endpoint.requests[-1]["messages"])


@pytest.mark.parametrize("command", ["/theme", "/settings", "/clear", "/quit"])
def test_terminal_only_commands_fail_without_opening_a_terminal(machine, command):
    result, events = machine.invoke("ask", command)
    assert result.returncode == 1
    assert events[-1]["status"] == "failed"


@pytest.mark.parametrize(
    "args",
    [
        ("init",),
        ("config", "list"),
        ("config", "get", "session.language"),
        ("config", "provider", "--list"),
        ("doctor",),
        ("doctor", "--probe-llm"),
        ("kb", "status"),
        ("kb", "update"),
        ("plugins", "list"),
        ("experience", "list"),
    ],
)
def test_management_commands_complete_without_a_terminal(machine, args):
    result, events = machine.invoke(*args)
    assert result.returncode == 0, events[-1]
    assert events[-1]["status"] == "completed"


def test_configuration_changes_persist_across_processes(machine):
    machine.invoke("config", "set", "session.language", "en")
    _, events = machine.invoke("config", "get", "session.language")
    assert events[-1]["result"]["value"] == "en"


def test_persistent_process_stops_after_one_configured_cycle(machine):
    _, events = machine.invoke(
        "persistent",
        "http://127.0.0.1",
        "--no-resume",
        "--rounds",
        "1",
        "--cycles",
        "1",
        "--max-steps",
        "1",
        "--no-report",
        "--format",
        "jsonl",
    )
    assert events[-1]["status"] == "completed", events[-1]
    assert len(list((machine.root / "runs").glob("*/run.json"))) == 1


def test_root_resume_process_continues_exact_saved_run(machine):
    _, initial = machine.invoke(
        "run",
        "http://127.0.0.1",
        "--no-resume",
        "--max-steps",
        "1",
        "--no-report",
        "--run-name",
        "headless-saved",
    )
    _, resumed = machine.invoke(
        "--resume",
        "headless-saved",
        "--runs-dir",
        str(machine.root / "runs"),
    )
    assert resumed[-1]["status"] == "completed", resumed[-1]
    assert resumed[-1]["result"]["run"]["name"] == initial[-1]["result"]["run"]["name"]
    assert len(list((machine.root / "runs").glob("*/run.json"))) == 1


@pytest.mark.parametrize("command", ["run", "recon", "scan", "exploit", "solve"])
def test_task_process_reaches_shared_engine_and_writes_resumable_run(machine, command):
    result, events = machine.invoke(
        command, "http://127.0.0.1", "--no-resume", "--max-steps", "1", "--format", "jsonl"
    )
    final = events[-1]
    assert result.returncode == 0, final
    directory = Path(final["result"]["run"]["directory"])
    manifest = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert (directory / "summary.json").is_file()
    if command != "solve":
        assert all(Path(path).is_file() for path in final["result"]["report"].values())


@pytest.mark.parametrize("format", ["json", "jsonl"])
def test_stdin_task_process_preserves_scope_and_explicit_budgets(machine, format):
    _, events = machine.invoke(
        "run",
        "--input-json",
        "-",
        "--format",
        format,
        input=json.dumps(
            {
                "target": "http://127.0.0.1:8080",
                "resume": False,
                "options": {"max_steps": 1, "allowed_ports": [8080], "auto_report": False},
            }
        ),
    )
    final = events[-1]
    assert final["status"] == "completed", final
    directory = Path(final["result"]["run"]["directory"])
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    assert summary["profile"]["max_steps"] == 1
    state_path = next(directory.glob("targets/*/state/current.json"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["task_constraints"]["allowed_ports"] == [8080]


@pytest.mark.parametrize(
    "marker,status", [("FIXTURE_TOOL", "requires_approval"), ("FIXTURE_QUESTION", "requires_input")]
)
def test_required_interaction_process_terminates_and_saves_checkpoint(machine, marker, status):
    _, events = machine.invoke(
        "run",
        "http://127.0.0.1",
        "--no-resume",
        "--max-steps",
        "1",
        "--no-report",
        "--prompt",
        marker,
        "--format",
        "jsonl",
    )
    final = events[-1]
    assert final["status"] == status, final
    assert final["exit_code"] == 1
    assert final["result"]["interaction"]
    manifests = list((machine.root / "runs").glob("*/run.json"))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text())["status"] == "interrupted"
    if marker == "FIXTURE_TOOL":
        assert any(event["type"] == "approval_required" for event in events)
        assert not any("fixture-tool-ok\n" == event.get("result") for event in events)


def test_explicit_execution_policy_runs_local_python_and_streams_tool_records(machine):
    machine.config["safety"]["permission_mode"] = "full_access"
    machine.path.write_text(yaml.safe_dump(machine.config), encoding="utf-8")
    _, events = machine.invoke(
        "run",
        "http://127.0.0.1",
        "--no-resume",
        "--max-steps",
        "2",
        "--no-report",
        "--prompt",
        "FIXTURE_TOOL",
        "--format",
        "jsonl",
    )
    assert events[-1]["status"] == "completed", events[-1]
    assert any(event["type"] == "tool_call" for event in events)
    assert any(
        event["type"] == "tool_result" and "fixture-tool-ok" in event.get("result", "")
        for event in events
    )


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
@pytest.mark.parametrize("marker", ["FIXTURE_WAIT", "FIXTURE_SSE_WAIT", "FIXTURE_RETRY_WAIT"])
@pytest.mark.skipif(os.name != "posix", reason="POSIX process signal cancellation")
def test_signal_cancels_real_process_and_flushes_final_result(machine, endpoint, signum, marker):
    _cancel_process(machine, endpoint, signum, ["ask", f"Explain {marker}"])


def _cancel_process(machine, endpoint, signum, args):
    endpoint.waiting.clear()
    endpoint.release.clear()
    process = subprocess.Popen(
        [sys.executable, "-m", "vulnclaw", *args, "--format", "jsonl"],
        env=machine.env,
        cwd=machine.root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        assert endpoint.waiting.wait(timeout=10)
        process.send_signal(signum)
        try:
            stdout, stderr = process.communicate(timeout=3)
        except subprocess.TimeoutExpired as exc:
            partial = [json.loads(line) for line in exc.output.decode("utf-8").splitlines()]
            assert partial[-1]["type"] == "command_result"
            assert partial[-1]["status"] == "cancelled"
            raise
        events = [json.loads(line) for line in stdout.splitlines()]
        for event in events:
            VALIDATOR.validate(event)
        assert process.returncode == 1, stderr
        assert events[-1]["type"] == "command_result"
        assert events[-1]["status"] == "cancelled"
        assert sum(event["type"] == "command_result" for event in events) == 1
    finally:
        endpoint.release.set()
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


@pytest.mark.parametrize("command", ["verify", "report"])
@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
@pytest.mark.skipif(os.name != "posix", reason="POSIX process signal cancellation")
def test_signal_cancels_blocked_audit_and_report(machine, endpoint, command, signum):
    from vulnclaw.agent.context import SessionState
    from vulnclaw.findings.intake import submit_finding

    state = SessionState(target="http://127.0.0.1/FIXTURE_WAIT")
    if command == "verify":
        config = VulnClawConfig()
        config.session.output_dir = machine.root / "reports"
        state.agent_state.remember_tool_result(
            tool="fetch",
            arguments={"url": state.target},
            target=state.target,
            output="Recorded fixture response",
        )
        agent = SimpleNamespace(
            config=config, context=SimpleNamespace(state=state), session_state=state
        )
        submit_finding(
            agent,
            {
                "title": "Fixture candidate",
                "description": "Recorded candidate",
                "vuln_type": "Other",
                "severity": "Low",
                "endpoint": state.target,
                "evidence_ids": ["e001"],
            },
        )
    path = state.save(machine.root / "session.json")
    _cancel_process(machine, endpoint, signum, [command, str(path)])
    assert endpoint.requests[-1].get("stream", False) is False


@pytest.mark.skipif(os.name != "posix", reason="POSIX process signal cancellation")
def test_cancelled_run_saves_checkpoint_that_can_be_resumed(machine, endpoint):
    _cancel_process(
        machine,
        endpoint,
        signal.SIGTERM,
        [
            "run",
            "http://127.0.0.1",
            "--no-resume",
            "--max-steps",
            "1",
            "--no-report",
            "--run-name",
            "cancelled-fixture",
            "--prompt",
            "FIXTURE_WAIT",
        ],
    )
    manifest = next((machine.root / "runs").glob("*/run.json"))
    assert json.loads(manifest.read_text())["status"] == "interrupted"
    _, events = machine.invoke(
        "--resume", "cancelled-fixture", "--runs-dir", str(machine.root / "runs")
    )
    assert events[-1]["status"] == "completed"


@pytest.mark.parametrize("operation", ["nonstream", "structured", "attack_summary"])
async def test_agent_nonstream_requests_cancel_with_server_still_blocked(
    machine, endpoint, monkeypatch, operation
):
    from vulnclaw.agent.core import AgentCore
    from vulnclaw.agent.llm_client import call_llm
    from vulnclaw.agent.solver import structured_call

    config = VulnClawConfig.model_validate({**machine.config, "mcp": {"servers": {}}})
    monkeypatch.setattr(AgentCore, "_report_kb_status", lambda self: None)
    agent = AgentCore(config)
    agent.session_state.target = "FIXTURE_WAIT"
    agent.session_state.executed_steps.append("FIXTURE_WAIT")
    endpoint.waiting.clear()
    endpoint.release.clear()
    if operation == "nonstream":
        request = call_llm(agent, "FIXTURE_WAIT")
    elif operation == "structured":
        request = structured_call(agent, "FIXTURE_WAIT")
    else:
        request = agent._generate_attack_summary()
    task = asyncio.create_task(request)
    try:
        assert await asyncio.to_thread(endpoint.waiting.wait, 10)
        client = agent._client
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        await agent.aclose()
        assert client.is_closed()
        assert endpoint.requests[-1].get("stream", False) is False
    finally:
        endpoint.release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await agent.aclose()


@pytest.fixture
def backend(machine):
    process = subprocess.Popen(
        [sys.executable, "-m", "vulnclaw.session_backend"],
        env=machine.env,
        cwd=machine.root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    received = queue.Queue()
    observed = []
    reader = threading.Thread(
        target=lambda: [received.put(json.loads(line)) for line in process.stdout], daemon=True
    )
    reader.start()

    def send(kind, identifier, *, task_id=None, **payload):
        message = {"protocol_version": 2, "type": kind, "request_id": identifier}
        if kind in {"submit_input", "start_task", "cancel_task"}:
            message["task_id"] = task_id or identifier
        message["payload"] = payload
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def until(kind, identifier=None):
        while True:
            event = received.get(timeout=15)
            VALIDATOR.validate(event)
            observed.append(event)
            if event["type"] == kind and (
                identifier is None or event.get("request_id") == identifier
            ):
                return event
            assert event["type"] not in {"error", "task_failed", "turn_failed"}, event

    send("initialize", "init")
    ready = until("ready", "init")
    yield SimpleNamespace(process=process, send=send, until=until, ready=ready, observed=observed)
    if process.poll() is None:
        send("shutdown", "shutdown")
        # Closing stdin also releases the transport's blocking readline after shutdown.
        process.stdin.close()
        try:
            process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    reader.join(timeout=5)
    process.stdout.close()


def test_protocol_process_keeps_history_and_scope_between_turns(backend, endpoint):
    backend.send("submit_input", "scope", text="/scope --allowed-ports 8080")
    control = backend.until("control_result", "scope")
    assert control["result"]["scope"]["allowed_ports"] == [8080]
    for identifier, text in [("first", "Explain fixture-memory-123"), ("second", "Explain again")]:
        backend.send("submit_input", identifier, text=text)
        assert backend.until("turn_completed", identifier)["result"]["output"]
    assert "fixture-memory-123" in json.dumps(endpoint.requests[-1]["messages"])
    backend.send("get_state", "state")
    state = backend.until("state", "state")["state"]
    assert state["task_constraints"]["allowed_ports"] == [8080]
    assert len([message for message in state["messages"] if message["role"] == "user"]) == 2
    assert backend.process.pid == backend.ready["backend"]["pid"]


def test_protocol_process_accepts_question_answer_in_same_session(backend):
    backend.send(
        "start_task",
        "question",
        task={
            "command": "run",
            "target": "http://127.0.0.1",
            "resume": False,
            "prompt": "FIXTURE_QUESTION",
            "options": {"max_steps": 1, "auto_report": False},
        },
    )
    state = backend.until("task_completed", "question")["state"]
    assert state["task"]["waiting_for_input"] is True
    backend.send("submit_input", "answer", text="fixture-answer authorized credentials")
    answer = backend.until("turn_completed", "answer")
    assert answer["state"]["task"]["waiting_for_input"] is False


def test_protocol_cancel_keeps_process_available_for_later_input(backend, endpoint):
    endpoint.waiting.clear()
    endpoint.release.clear()
    try:
        backend.send("submit_input", "active", text="Explain FIXTURE_WAIT")
        assert endpoint.waiting.wait(timeout=10)
        backend.send("cancel_task", "cancel", task_id="active")
        cancelled = backend.until("turn_cancelled", "cancel")
        assert cancelled["state"]["task"]["active"] is False
    finally:
        endpoint.release.set()
    backend.send("submit_input", "later", text="Explain fixture later")
    assert backend.until("turn_completed", "later")["result"]["output"]


@pytest.mark.parametrize("decision", ["approve", "deny"])
def test_protocol_process_resolves_execution_approval(backend, decision):
    backend.send(
        "start_task",
        "tool",
        task={
            "command": "run",
            "target": "http://127.0.0.1",
            "resume": False,
            "prompt": "FIXTURE_TOOL",
            "options": {"max_steps": 2, "auto_report": False},
        },
    )
    approval = backend.until("approval_required")
    backend.send(
        "control",
        "decision",
        operation="execution.approval.resolve",
        arguments={
            "request_hash": approval["request_hash"],
            "decision": decision,
        },
    )
    assert backend.until("control_result", "decision")["result"]["status"] == "resolved"
    result = backend.until("task_completed", "tool")
    executed = any(
        event["type"] == "tool_result" and "fixture-tool-ok\n" in event.get("result", "")
        for event in backend.observed
    )
    assert executed is (decision == "approve")
    assert result["state"]["permission_mode"] == "ask"


def test_protocol_rejects_invalid_decision_and_stale_hash_without_resolving_pending_approval(
    backend,
):
    backend.send(
        "start_task",
        "tool",
        task={
            "command": "run",
            "target": "http://127.0.0.1",
            "resume": False,
            "prompt": "FIXTURE_TOOL",
            "options": {"max_steps": 2, "auto_report": False},
        },
    )
    approval = backend.until("approval_required")
    backend.send(
        "control",
        "invalid",
        operation="execution.approval.resolve",
        arguments={
            "request_hash": approval["request_hash"],
            "decision": "yes",
        },
    )
    assert backend.until("error", "invalid")["code"] == "invalid_control"
    backend.send(
        "control",
        "stale",
        operation="execution.approval.resolve",
        arguments={
            "request_hash": "expired-fixture-hash",
            "decision": "approve",
        },
    )
    assert backend.until("control_result", "stale")["result"]["status"] == "unknown_request"
    backend.send(
        "control",
        "valid",
        operation="execution.approval.resolve",
        arguments={
            "request_hash": approval["request_hash"],
            "decision": "deny",
        },
    )
    assert backend.until("control_result", "valid")["result"]["status"] == "resolved"
    backend.until("task_completed", "tool")
    assert not any(
        event["type"] == "tool_result" and "fixture-tool-ok\n" in event.get("result", "")
        for event in backend.observed
    )


def test_protocol_process_exposes_session_controls_without_terminal_runtime(backend):
    commands = [
        ("/help scope", "session.help", "commands"),
        ("/permission read_only", "session.permission.set", "mode"),
        ("/permission ask", "session.permission.set", "mode"),
        ("/target http://127.0.0.1", "session.target.set", "target"),
        ("/scope --allowed-ports 8080", "session.scope.update", "scope"),
        ("/scope reset", "session.scope.reset", "scope"),
        ("/think off", "session.think", "show_thinking"),
        ("/config", "config.document", "config"),
        ("/language en", "language.set", "language"),
        ("/experience list --status all", "experience.list", "message"),
        ("/compact", "session.compact", "message"),
        ("/new", "session.new", "message"),
    ]
    for index, (text, operation, key) in enumerate(commands):
        identifier = f"control-{index}"
        backend.send("submit_input", identifier, text=text)
        control = backend.until("control_result", identifier)
        assert control["operation"] == operation
        assert key in control["result"]
