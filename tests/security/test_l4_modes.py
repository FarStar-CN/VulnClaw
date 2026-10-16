"""Shared execution mode and permission controls, including Plan tool enforcement."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from vulnclaw.agent.builtin_tools import execute_mcp_tool
from vulnclaw.agent.core import AgentCore
from vulnclaw.agent.exec_gate import reset_execution_gate
from vulnclaw.agent.execution_context import ExecutionContext, execution_context
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
    for policy in ("ask", "auto_review", "full_access"):
        result = await session.submit_input(InputRequest(text=f"/permission {policy}"))
        assert result["result"]["mode"] == policy
        assert session.permission_mode == policy
        assert session.mode == "agent"
    with pytest.raises(ValueError, match="mode must be one of"):
        await session.submit_input(InputRequest(text="/permission yolo"))
    assert session.permission_mode == "full_access"


def test_plan_does_not_offer_tools():
    agent = AgentCore(VulnClawConfig())
    with execution_context(ExecutionContext(mode="plan")):
        assert agent._build_openai_tools() == []
    assert agent._build_openai_tools()


async def test_plan_blocks_even_model_supplied_tool_calls():
    mcp = SimpleNamespace(call_tool=AsyncMock(side_effect=AssertionError("must not execute")))
    agent = AgentCore(VulnClawConfig(), mcp)
    with execution_context(ExecutionContext(mode="plan")):
        result = await execute_mcp_tool(agent, "fetch", {"url": "https://example.test"})
        assert "Plan mode" in result
        result = await execute_mcp_tool(agent, "shell_command", {"command": "echo test"})
        assert "Plan mode" in result
        result = await execute_mcp_tool(agent, "conversation_report", {"action": "export"})
        assert "Plan mode" in result
    mcp.call_tool.assert_not_awaited()


@pytest.mark.parametrize("streaming", [False, True])
async def test_plan_model_requests_stay_tool_free_through_stream_fallback(streaming):
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
    with execution_context(ExecutionContext(mode="plan")):
        output = await (
            call_llm_stream(agent, "Plan only") if streaming else call_llm(agent, "Plan only")
        )
    assert output == "A plan without execution"
    assert requests
    assert all(not request.get("tools") for request in requests)
