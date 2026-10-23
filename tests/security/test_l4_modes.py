"""Unified permissions, including read-only tool enforcement."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from vulnclaw.agent.builtin_tools import execute_mcp_tool
from vulnclaw.agent.core import AgentCore
from vulnclaw.agent.exec_gate import (
    ExecutionGate,
    GateRequest,
    get_execution_gate,
    reset_execution_gate,
)
from vulnclaw.agent.execution_context import READ_ONLY_TOOLS
from vulnclaw.application import ApplicationSession
from vulnclaw.config.schema import VulnClawConfig
from vulnclaw.input_service import InputRequest


@pytest.fixture(autouse=True)
def fresh_gate():
    reset_execution_gate()
    yield
    reset_execution_gate()


async def test_permissions_are_set_through_shared_input():
    config = VulnClawConfig()
    agent = AgentCore(config)
    session = ApplicationSession(lambda *_args, **_kwargs: None)
    session.initialized = True
    session.runtime = SimpleNamespace(config=config, agent=agent)
    for policy in ("read_only", "ask", "auto_review", "full_access"):
        result = await session.submit_input(InputRequest(text=f"/permission {policy}"))
        assert result["result"]["mode"] == policy
        assert session.permission_mode == policy
        assert "execution_mode" not in session.state_snapshot()
    with pytest.raises(ValueError, match="mode must be one of"):
        await session.submit_input(InputRequest(text="/permission yolo"))
    assert session.permission_mode == "full_access"


def test_read_only_offers_supported_reads():
    agent = AgentCore(VulnClawConfig())
    get_execution_gate(agent.config).set_mode("read_only")
    names = {tool["function"]["name"] for tool in agent._build_openai_tools()}
    assert names
    assert names <= READ_ONLY_TOOLS
    assert {"evidence_view", "shell_command"} <= names


async def test_read_only_blocks_mutating_and_unknown_model_supplied_calls():
    mcp = SimpleNamespace(call_tool=AsyncMock(side_effect=AssertionError("must not execute")))
    agent = AgentCore(VulnClawConfig(), mcp)
    get_execution_gate(agent.config).set_mode("read_only")
    for name, arguments in [
        ("fetch", {"url": "https://example.test"}),
        ("shell_command", {"command": "touch changed.txt"}),
        ("python_execute", {"code": "open('changed.txt','w').close()"}),
        ("conversation_report", {"action": "export"}),
        ("agent_run", {"goal": "write a file"}),
    ]:
        result = await execute_mcp_tool(agent, name, arguments)
        assert "Read-Only" in result
    mcp.call_tool.assert_not_awaited()


@pytest.mark.parametrize("streaming", [False, True])
async def test_read_only_model_requests_keep_only_reads_through_stream_fallback(streaming):
    from vulnclaw.agent.llm_client import call_llm, call_llm_stream

    agent = AgentCore(VulnClawConfig())
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        if kwargs.get("stream"):
            raise ValueError("streaming not supported")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="A plan without execution",
                        tool_calls=[],
                    )
                )
            ]
        )

    agent._get_client = lambda: SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=create,
            )
        )
    )
    get_execution_gate(agent.config).set_mode("read_only")
    output = await (
        call_llm_stream(agent, "Analyze evidence") if streaming else call_llm(agent, "Analyze evidence")
    )
    assert output == "A plan without execution"
    assert requests
    assert all(request.get("tools") for request in requests)
    assert all(
        tool["function"]["name"] in READ_ONLY_TOOLS
        for request in requests for tool in request["tools"]
    )


async def test_read_only_reads_files_and_evidence_without_approval(tmp_path):
    config = VulnClawConfig()
    config.safety.permission_mode = "read_only"
    agent = AgentCore(config)
    gate = get_execution_gate(config)
    channel = SimpleNamespace(request_approval=AsyncMock())
    gate.install_channel(channel)
    source = tmp_path / "source.txt"
    source.write_text("read-only synthetic evidence", encoding="utf-8")
    result = await execute_mcp_tool(agent, "shell_command", {
        "command": "cat source.txt", "workdir": str(tmp_path),
    })
    assert "read-only synthetic evidence" in result
    evidence = await execute_mcp_tool(agent, "evidence_list", {})
    assert isinstance(evidence, str)
    channel.request_approval.assert_not_awaited()
    assert source.read_text() == "read-only synthetic evidence"


@pytest.mark.parametrize("kind,command", [
    ("shell", "touch changed.txt"),
    ("shell", "cat source.txt > changed.txt"),
    ("shell", "find . -delete"),
    ("shell", "diff --output=changed.txt a b"),
    ("shell", "python -c 'pass'"),
    ("shell", "/tmp/cat source.txt"),
    ("shell", "curl https://example.test"),
    ("python", "print('hello')"),
    ("poc", "synthetic probe"),
])
async def test_read_only_refuses_unknown_and_mutating_actions_even_if_trusted(kind, command):
    gate = ExecutionGate(mode="read_only", trusted_commands=(("touch",), ("curl",)))
    channel = SimpleNamespace(request_approval=AsyncMock(return_value="approve"))
    gate.install_channel(channel)
    outcome = await gate.authorize(GateRequest(kind=kind, display=command))
    assert not outcome.approved
    assert outcome.status == "read_only"
    channel.request_approval.assert_not_awaited()


async def test_entering_read_only_settles_pending_approval_without_executing():
    gate = ExecutionGate()
    opened = asyncio.Event()

    async def request(view):
        opened.set()
        return await gate.wait_decision(view.request_hash)

    closed = AsyncMock()
    gate.install_channel(SimpleNamespace(request_approval=request, notify_closed=closed))
    pending = asyncio.create_task(gate.authorize(GateRequest(kind="python", display="pass")))
    await asyncio.wait_for(opened.wait(), 1)
    gate.set_mode("read_only")
    outcome = await asyncio.wait_for(pending, 1)
    assert not outcome.approved
    assert outcome.status == "denied"
    assert not gate._pending_by_hash
    closed.assert_awaited_once()


async def test_read_only_rejects_tasks_and_reports_at_shared_services(tmp_path):
    from vulnclaw.report_service import generate_report_from_file
    from vulnclaw.task_service import TaskCreateRequest, execute_task, prepare_task

    config = VulnClawConfig()
    get_execution_gate(config).set_mode("read_only")
    agent = AgentCore(config)
    task = prepare_task(TaskCreateRequest(command="codescan", target=str(tmp_path)))
    with pytest.raises(ValueError, match="Read-Only"):
        await execute_task(agent, task)
    with pytest.raises(ValueError, match="Read-Only"):
        await generate_report_from_file(str(tmp_path / "not-read.json"), config=config)
    assert list(tmp_path.iterdir()) == []
