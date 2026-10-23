"""Public input behavior shared by local callers and the JSONL terminal client."""

from __future__ import annotations

import asyncio
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from vulnclaw.agent.context import SessionState
from vulnclaw.agent.exec_gate import get_execution_gate, reset_execution_gate
from vulnclaw.agent.execution_context import current_execution
from vulnclaw.application import ApplicationSession
from vulnclaw.config.schema import VulnClawConfig
from vulnclaw.input_service import COMMANDS, InputRequest, parse_input
from vulnclaw.session_backend import BackendSession
from vulnclaw.session_protocol import JsonlWriter, decode_client_message
from vulnclaw.task_service import TaskCreateRequest, task_request_from_payload


class Runtime:
    def __init__(self):
        self.config = VulnClawConfig()
        self.config.session.auto_save = False
        self.agent = SimpleNamespace(
            session_state=SessionState(), _subagent_ctx=SimpleNamespace(depth=0)
        )
        self.agent.apply_task_constraints = lambda value: setattr(
            self.agent.session_state,
            "task_constraints",
            value,
        )
        self.agent.reset_context = lambda: setattr(self.agent, "session_state", SessionState())
        self.mcp_manager = SimpleNamespace(
            list_available_tools=lambda: ["fetch"],
            _preinit_chrome_devtools=AsyncMock(),
        )
        self.mcp_started = 0
        self.stop = AsyncMock()
        self.calls = []

        async def chat(text, *, target=None, **kwargs):
            self.calls.append((text, target, get_execution_gate(self.config).mode, kwargs["task_constraints"]))
            self.agent.session_state.target = target
            return SimpleNamespace(output="answer")

        self.agent.chat = chat


@pytest.fixture(autouse=True)
def gate():
    reset_execution_gate()
    yield
    reset_execution_gate()


async def open_application(runtime, **bootstrap):
    session = ApplicationSession(lambda *_args, **_kwargs: None, runtime_factory=lambda: runtime)
    await session.handle("initialize", request_id="initialize", payload={"bootstrap": bootstrap})
    return session


@pytest.mark.parametrize(
    ("alias", "arguments"),
    [
        ("recon", "--allow-actions recon"),
        ("scan", "--allow-actions recon,scan"),
        ("exploit", "--allow-actions recon,scan,exploit"),
        ("persistent", "--continuous"),
    ],
)
def test_aliases_and_run_have_identical_normalized_requests(alias, arguments):
    raw = parse_input(InputRequest(text=f"/{alias} example.test")).task
    canonical = parse_input(InputRequest(text=f"/run example.test {arguments}")).task
    assert task_request_from_payload(raw) == task_request_from_payload(canonical)
    assert TaskCreateRequest(command=alias, target="example.test") == task_request_from_payload(raw)


def test_alias_cannot_expand_its_action_limit():
    with pytest.raises(ValueError, match="only permits"):
        task_request_from_payload(
            parse_input(
                InputRequest(
                    text="/recon example.test --allow-actions exploit",
                )
            ).task
        )


def test_shell_quoting_and_cycle_zero_are_preserved():
    request = task_request_from_payload(
        parse_input(
            InputRequest(
                text='/run example.test --continuous --max-cycles 0 --prompt "使用技能 web-pentest"',
            )
        ).task
    )
    assert request.prompt == "使用技能 web-pentest"
    assert request.options.continuous is True
    assert request.options.max_cycles == 0
    with pytest.raises(ValueError, match="require --continuous"):
        TaskCreateRequest(command="run", target="example.test", options={"max_cycles": 2})


@pytest.mark.parametrize("text", ["/skill web-pentest", "/ctf-web", "/not-a-command"])
def test_skill_slash_launches_are_not_commands(text):
    with pytest.raises(ValueError, match="Unknown command"):
        parse_input(InputRequest(text=text))
    assert "skill" not in COMMANDS


@pytest.mark.parametrize("text", ["clear", "report", "target example.test", "status"])
def test_bare_words_are_natural_language(text):
    assert parse_input(InputRequest(text=text)).kind == "conversation"


async def test_natural_language_keeps_scope_and_creates_no_run(monkeypatch):
    runtime = Runtime()
    session = await open_application(runtime, target="example.test", only_port=443)
    execute = AsyncMock(side_effect=AssertionError("conversation must not create a run"))
    monkeypatch.setattr("vulnclaw.application.execute_task", execute)
    result = await session.submit_input(InputRequest(text="解释当前结果"))
    assert result["output"] == "answer"
    assert runtime.calls[0][1] == "example.test"
    assert runtime.calls[0][3].allowed_ports == [443]
    assert session.active_task is None
    execute.assert_not_awaited()
    await session.close()


async def test_read_only_is_the_backend_owned_permission():
    runtime = Runtime()
    session = await open_application(runtime)
    assert session.permission_mode == "ask"
    await session.submit_input(InputRequest(text="/permission full_access"))
    await session.submit_input(InputRequest(text="/permission read_only"))
    await session.submit_input(InputRequest(text="制定下一步计划"))
    assert runtime.calls[0][2] == "read_only"
    assert session.permission_mode == "read_only"
    for text in ("/run example.test", "/codescan .", "/report"):
        with pytest.raises(ValueError, match="Read-Only"):
            await session.submit_input(InputRequest(text=text))
    with pytest.raises(ValueError, match="Unknown command"):
        await session.submit_input(InputRequest(text="/mode yolo"))
    await session.close()


async def test_clear_preserves_context_and_new_resets_business_state():
    runtime = Runtime()
    session = await open_application(runtime, target="example.test", only_port=443)
    old_state = runtime.agent.session_state
    assert (await session.submit_input(InputRequest(text="/clear")))["operation"] == "clear"
    assert runtime.agent.session_state is old_state
    session.conversation.last_auto_input = "prior engagement"
    await session.submit_input(InputRequest(text="/new"))
    assert runtime.agent.session_state is not old_state
    assert not session.conversation.target
    assert not session.conversation.last_auto_input
    assert runtime.agent.session_state.task_constraints.allowed_ports == [443]
    await session.close()


async def test_report_requirement_survives_continuation_and_uses_semantic_kind(monkeypatch):
    runtime = Runtime()
    session = await open_application(runtime)
    export = AsyncMock(return_value={"kind": "replay", "path": "replay.md"})
    monkeypatch.setattr("vulnclaw.report_service.export_report", export)
    completed = False

    async def solve(text, *, target, **kwargs):
        runtime.agent.session_state.target = target
        if not completed:
            await current_execution().report({"action": "required", "kind": "replay"})
        return SimpleNamespace(completed=completed, output="done" if completed else "working")

    runtime.agent.solve = solve
    first = await session.submit_input(InputRequest(text="pentest https://example.test"))
    assert first["reports"] == []
    export.assert_not_awaited()
    completed = True
    second = await session.submit_input(InputRequest(text="继续"))
    assert second["reports"] == [{"kind": "replay", "path": "replay.md"}]
    assert export.await_args.kwargs["kind"] == "replay"
    await session.submit_input(InputRequest(text="解释刚才的结果"))
    assert export.await_count == 1
    await session.close()


@pytest.mark.parametrize(
    ("preference", "default", "expected"),
    [
        ("default", True, 1),
        ("default", False, 0),
        ("required", False, 1),
        ("suppress", True, 0),
    ],
)
async def test_report_preferences_override_defaults(monkeypatch, preference, default, expected):
    runtime = Runtime()
    runtime.config.session.conversation_auto_report = default
    session = await open_application(runtime)
    export = AsyncMock(return_value={"path": "report.md", "kind": "security"})
    monkeypatch.setattr("vulnclaw.report_service.export_report", export)

    async def solve(text, *, target, **kwargs):
        runtime.agent.session_state.target = target
        await current_execution().report({"action": preference, "kind": "security"})
        return SimpleNamespace(completed=True)

    runtime.agent.solve = solve
    await session.submit_input(InputRequest(text="pentest https://example.test"))
    assert export.await_count == expected
    await session.close()


async def test_immediate_report_uses_existing_results_without_another_task(monkeypatch):
    runtime = Runtime()
    session = await open_application(runtime, target="example.test")
    export = AsyncMock(return_value={"kind": "security", "path": "existing.md"})
    monkeypatch.setattr("vulnclaw.report_service.export_report", export)
    runtime.agent.solve = AsyncMock(side_effect=AssertionError("must not retest"))

    async def chat(text, **kwargs):
        await current_execution().report({"action": "export", "kind": "security"})
        return SimpleNamespace(output="exported")

    runtime.agent.chat = chat
    result = await session.submit_input(InputRequest(text="把已有结果生成报告"))
    assert result["reports"][0]["path"] == "existing.md"
    runtime.agent.solve.assert_not_awaited()
    await session.close()


async def test_report_failure_keeps_completed_work(monkeypatch):
    runtime = Runtime()
    session = await open_application(runtime)
    monkeypatch.setattr(
        "vulnclaw.report_service.export_report", AsyncMock(side_effect=OSError("disk full"))
    )
    runtime.agent.solve = AsyncMock(return_value=SimpleNamespace(completed=True))
    result = await session.submit_input(InputRequest(text="pentest https://example.test"))
    assert result["completed"] is True
    assert result["report_error"] == "disk full"
    assert session.active_task is None
    await session.close()


async def test_jsonl_turn_can_be_cancelled_and_followed_by_another_turn():
    runtime = Runtime()
    stream = io.StringIO()
    backend = BackendSession(JsonlWriter(stream), runtime_factory=lambda: runtime)

    async def send(kind, request_id, task_id=None, **payload):
        message = {
            "protocol_version": 2,
            "type": kind,
            "request_id": request_id,
            "payload": payload,
        }
        if task_id:
            message["task_id"] = task_id
        await backend.handle(decode_client_message(json.dumps(message)))

    await send("initialize", "init")
    entered = asyncio.Event()

    async def blocking(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    original = runtime.agent.chat
    runtime.agent.chat = blocking
    await send("submit_input", "r1", "turn-1", text="hello")
    await entered.wait()
    with pytest.raises(ValueError, match="already active"):
        await send("submit_input", "busy", "turn-busy", text="another")
    await send("cancel_task", "cancel", "turn-1")
    await backend.wait_for_idle()
    runtime.agent.chat = original
    await send("submit_input", "r2", "turn-2", text="hello again")
    await backend.wait_for_idle()
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [event["type"] for event in events if event.get("task_id") == "turn-1"] == [
        "message", "message", "turn_started",
        "turn_cancelled",
    ]
    assert [event["type"] for event in events if event.get("task_id") == "turn-2"] == [
        "message", "message", "turn_started",
        "turn_completed",
    ]
    await backend.close()
    runtime.stop.assert_awaited_once()


async def test_continuous_run_uses_the_same_engine_budget_and_constraints():
    from vulnclaw.agent.core import AgentCore
    from vulnclaw.task_service import prepare_task, run_task_action

    config = VulnClawConfig()
    config.session.engine = "rounds"
    agent = AgentCore(config)
    calls = []

    async def solve(text, **kwargs):
        calls.append(kwargs)
        agent.session_state.agent_state.completed = len(calls) == 2

    agent.solve = solve
    agent._generate_attack_summary = AsyncMock(return_value="")
    request = TaskCreateRequest(
        command="run",
        target="https://example.test",
        options={
            "continuous": True,
            "engine": "solve",
            "max_cycles": 4,
            "max_steps": 17,
            "max_tool_rounds": 3,
            "auto_report": False,
            "allow_actions": ["recon"],
        },
    )
    cycles = await run_task_action(agent, prepare_task(request))
    assert len(cycles) == len(calls) == 2
    assert all(call["max_steps"] == 17 and call["max_tool_rounds"] == 3 for call in calls)
    assert all(call["task_constraints"].allowed_actions == ["recon"] for call in calls)


def test_alias_defaults_intersect_the_session_action_limit():
    request = task_request_from_payload(
        {"command": "scan", "target": "example.test"},
        defaults={"allow_actions": ["recon", "scan", "exploit"]},
    )
    assert request.options.allow_actions == ["recon", "scan"]
    with pytest.raises(ValueError, match="no actions"):
        task_request_from_payload(
            {"command": "scan", "target": "example.test"},
            defaults={"allow_actions": ["exploit"]},
        )


async def test_natural_language_scope_restrictions_are_kept_and_cannot_widen_scope():
    runtime = Runtime()
    session = await open_application(runtime, target="example.test", only_port=443)
    await session.submit_input(InputRequest(text="仅测试 443 端口，不要利用"))
    constraints = runtime.calls[-1][3]
    assert constraints.allowed_ports == [443]
    assert constraints.blocked_actions == ["exploit"]
    with pytest.raises(ValueError, match="do not overlap"):
        await session.submit_input(InputRequest(text="仅测试 8080 端口"))
    assert len(runtime.calls) == 1
    await session.close()


async def test_self_discovering_skill_seed_is_not_an_implicit_host_boundary(monkeypatch):
    monkeypatch.setattr(
        "vulnclaw.skills.loader.load_skill_by_name", lambda _: {"requires_target": False}
    )
    runtime = Runtime()
    session = await open_application(runtime)
    await session.submit_input(
        InputRequest(text="使用技能 hackerone，https://hackerone.com/example")
    )
    assert runtime.calls[-1][3].allowed_hosts == []
    await session.close()


