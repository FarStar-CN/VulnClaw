"""Behavior shared by in-process terminal callers and the JSONL adapter."""

from __future__ import annotations

import asyncio
import io
import json
import signal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import vulnclaw.application as application
from vulnclaw.agent.context import SessionState
from vulnclaw.agent.exec_gate import reset_execution_gate
from vulnclaw.application import ApplicationError, ApplicationSession
from vulnclaw.config.schema import VulnClawConfig
from vulnclaw.conversation import ConversationState
from vulnclaw.orchestrator import OrchestratorRunResult
from vulnclaw.session_backend import BackendSession
from vulnclaw.session_protocol import JsonlWriter, decode_client_message
from vulnclaw.target_state.store import SessionRestoreResult
from vulnclaw.task_service import TaskCreateRequest, TaskExecution, TaskOptions, prepare_task


class Runtime:
    def __init__(self):
        self.config = VulnClawConfig()
        self.config.llm.api_key = "test-key"
        state = SessionState()
        self.agent = SimpleNamespace(
            config=self.config, session_state=state,
            apply_task_constraints=lambda constraints: setattr(state, "task_constraints", constraints),
            apply_config=AsyncMock(side_effect=lambda config: setattr(self.agent, "config", config)),
        )
        self.mcp_manager = SimpleNamespace(
            wait_for_startup=AsyncMock(),
            apply_config=AsyncMock(),
            registry=SimpleNamespace(get_all_servers=lambda: {}),
        )
        self.stop_calls = 0
        self.mcp_started = 0

    async def stop(self):
        self.stop_calls += 1


@pytest.fixture(autouse=True)
def restore_process_state():
    previous = signal.getsignal(signal.SIGINT)
    reset_execution_gate()
    yield
    signal.signal(signal.SIGINT, previous)
    reset_execution_gate()


async def open_session(runtime):
    session = ApplicationSession(lambda *_args, **_fields: None, runtime_factory=lambda: runtime)
    await session.handle("initialize", request_id="init")
    return session


def completed(runtime, action_result=None):
    return TaskExecution(
        OrchestratorRunResult(
            SessionRestoreResult(target=runtime.agent.session_state.target),
            {"findings_count": 0},
        ),
        action_result,
    )


@pytest.mark.parametrize("command", ["recon", "scan", "run"])
async def test_local_and_jsonl_tasks_use_the_same_execution(command, monkeypatch):
    calls = []
    runtimes = []

    async def create(config=None):
        runtime = Runtime()

        async def action(prompt, *, target, task_constraints, stream_sink=None, **_options):
            calls.append((prompt, target, task_constraints.model_dump()))
            if stream_sink is not None:
                stream_sink.on_content_token("observed")
            return SimpleNamespace(output="observed")

        runtime.agent.chat = action
        runtime.agent.solve = action
        runtimes.append(runtime)
        return runtime

    async def orchestrate(*, agent, runner, **_arguments):
        await runner(agent)
        return OrchestratorRunResult(
            SessionRestoreResult(target=agent.session_state.target), {"findings_count": 0},
        )

    monkeypatch.setattr(application, "create_runtime", create)
    monkeypatch.setattr("vulnclaw.task_service.run_agent_task", orchestrate)
    report = AsyncMock(return_value={
        "path": "report.md", "findings_json": "findings.json", "findings_sarif": "findings.sarif",
    })
    monkeypatch.setattr(application, "generate_report_for_run", report)
    request = TaskCreateRequest(
        command=command, target="https://example.test", resume=False,
        options=TaskOptions(only_port=443),
    )
    task = prepare_task(request)

    local = ApplicationSession(lambda *_args, **_fields: None, runtime_factory=create)
    await local.handle("initialize", request_id="local-init")
    local_result = await local.run_task(task)
    await local.close()
    report.assert_not_awaited()

    stream = io.StringIO()
    backend = BackendSession(JsonlWriter(stream))
    await backend.handle(decode_client_message(json.dumps({
        "protocol_version": 2, "type": "initialize", "request_id": "init", "payload": {},
    })))
    await backend.handle(decode_client_message(json.dumps({
        "protocol_version": 2, "type": "start_task", "request_id": "start", "task_id": "t1",
        "payload": {"task": request.model_dump(mode="json", exclude_none=True)},
    })))
    await backend.wait_for_idle()
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    results = [event["result"] for event in events if event["type"] == "task_completed"]
    assert len(results) == 1, events
    result = results[0]

    assert calls[0] == calls[1]
    assert calls[0][2]["allowed_ports"] == [443]
    assert result["summary"] == local_result.run.summary
    assert result["status"] == local_result.run.status
    assert any(event["type"] == "message" and event.get("text") == "observed" for event in events)
    assert report.await_count == 1
    await backend.close()
    await backend.close()
    assert [runtime.stop_calls for runtime in runtimes] == [1, 1]


async def test_cancel_local_task_releases_session_for_next_task(monkeypatch):
    runtime = Runtime()
    session = await open_session(runtime)
    entered = asyncio.Event()

    async def blocking(*_args, **_kwargs):
        entered.set()
        await asyncio.Future()

    monkeypatch.setattr(application, "execute_task", blocking)
    task = TaskCreateRequest(command="recon", target="example.test")
    operation = asyncio.create_task(session.run_task(task))
    await entered.wait()
    assert session.state_snapshot()["task"]["active"] is True
    with pytest.raises(ApplicationError, match="already active"):
        await session.run_task(task)
    await session.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert session.state_snapshot()["task"]["active"] is False

    result = completed(runtime)
    monkeypatch.setattr(application, "execute_task", AsyncMock(return_value=result))
    assert await session.run_task(task) is result
    assert session.state_snapshot()["last_run"]["status"] == "completed"
    await session.close()
    await session.close()
    assert runtime.stop_calls == 1


async def test_failed_local_task_releases_session(monkeypatch):
    runtime = Runtime()
    session = await open_session(runtime)
    monkeypatch.setattr(application, "execute_task", AsyncMock(side_effect=ValueError("failed")))
    with pytest.raises(ValueError, match="failed"):
        await session.run_task(TaskCreateRequest(command="recon", target="example.test"))
    assert session.active_task is None
    await session.close()
    assert runtime.stop_calls == 1


async def test_close_cancels_active_local_task_before_stopping_runtime(monkeypatch):
    runtime = Runtime()
    session = await open_session(runtime)
    entered = asyncio.Event()
    cleaned = asyncio.Event()

    async def blocking(*_args, **_kwargs):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            assert runtime.stop_calls == 0
            cleaned.set()

    monkeypatch.setattr(application, "execute_task", blocking)
    operation = asyncio.create_task(session.run_task(
        TaskCreateRequest(command="recon", target="example.test"),
    ))
    await entered.wait()
    await session.close()
    assert operation.cancelled()
    assert cleaned.is_set()
    assert runtime.stop_calls == 1


async def test_conversation_uses_current_config_without_task_orchestration(monkeypatch):
    runtime = Runtime()
    session = await open_session(runtime)
    runtime.agent.chat = AsyncMock(return_value=SimpleNamespace(output="answer"))
    execute = AsyncMock(side_effect=AssertionError("conversation created a task run"))
    monkeypatch.setattr(application, "execute_task", execute)

    config = VulnClawConfig()
    config.llm.model = "replacement-model"
    await session.apply_config(config)
    assert session.config is runtime.agent.config is config
    from vulnclaw.input_service import InputRequest

    result = await session.submit_input(InputRequest(text="hello"))
    assert result["output"] == "answer"
    assert runtime.agent.chat.await_args.args == ("hello",)
    assert runtime.agent.chat.await_args.kwargs["target"] is None
    execute.assert_not_awaited()
    await session.close()


async def test_failed_runtime_initialization_closes_started_mcp(monkeypatch):
    manager = SimpleNamespace(start_enabled_servers=lambda **_options: 1, astop_all=AsyncMock())
    monkeypatch.setattr("vulnclaw.mcp.lifecycle.MCPLifecycleManager", lambda _config: manager)

    def fail(*_args):
        raise RuntimeError("agent initialization failed")

    monkeypatch.setattr("vulnclaw.agent.core.AgentCore", fail)
    with pytest.raises(RuntimeError, match="agent initialization failed"):
        await application.create_runtime(VulnClawConfig())
    manager.astop_all.assert_awaited_once()


def test_conversation_target_switch_and_continue_preserve_existing_semantics():
    agent = SimpleNamespace(reset_context=lambda: resets.append(True))
    resets = []
    conversation = ConversationState(
        agent, target="https://old.test", auto_mode=True,
        last_auto_input="recon https://old.test",
    )
    continuation = conversation.prepare_turn("yes")
    assert continuation.auto is True
    assert "recon https://old.test" in continuation.text
    checkin = conversation.prepare_turn("ready to begin bug hunting?")
    assert checkin.auto is False
    assert conversation.auto_mode is True
    switched = conversation.prepare_turn("recon https://new.test")
    assert switched.previous_target == "https://old.test"
    assert conversation.target == "https://new.test"
    assert conversation.phase == "Recon"
    assert conversation.auto_mode is False
    assert resets == [True]


@pytest.mark.parametrize("failed_turn", [False, True])
async def test_backend_reuses_one_loop_across_turns_and_closes_runtime(failed_turn):
    runtime = Runtime()
    loops = []
    stream = io.StringIO()
    calls = 0

    async def create():
        loops.append(asyncio.get_running_loop())
        return runtime

    async def chat(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        loops.append(asyncio.get_running_loop())
        if calls == 2 and failed_turn:
            raise RuntimeError("synthetic turn failure")
        return SimpleNamespace(target=None, phase=None, output="answer")

    async def stop():
        loops.append(asyncio.get_running_loop())
        runtime.stop_calls += 1

    runtime.agent.chat = chat
    runtime.stop = stop
    backend = BackendSession(JsonlWriter(stream), runtime_factory=create)
    for kind, identifier, payload in [
        ("initialize", "init", {}),
        ("submit_input", "one", {"text": "hello"}),
        ("submit_input", "two", {"text": "hello again"}),
    ]:
        request = {"protocol_version": 2, "type": kind, "request_id": identifier, "payload": payload}
        if kind == "submit_input":
            request["task_id"] = identifier
        await backend.handle(decode_client_message(json.dumps(request)))
        await backend.wait_for_idle()
    await backend.close()
    assert len(loops) == 4
    assert all(loop is asyncio.get_running_loop() for loop in loops)
    assert runtime.stop_calls == 1
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert sum(event["type"] == "turn_failed" for event in events) == int(failed_turn)
    assert sum(event["type"] == "turn_completed" for event in events) == 2 - int(failed_turn)
