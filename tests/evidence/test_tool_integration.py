from __future__ import annotations

import errno
import json
from types import SimpleNamespace

import pytest

from vulnclaw.agent.builtin_tools import execute_evidence_tool
from vulnclaw.agent.core import AgentCore
from vulnclaw.agent.tool_call_manager import handle_tool_calls_with_results
from vulnclaw.config.schema import VulnClawConfig
from vulnclaw.evidence import PersistenceErrorLimitExceeded
from vulnclaw.run_context import create_run_context
from vulnclaw.targets import parse_target


def _message(*calls):
    return SimpleNamespace(
        tool_calls=[
            SimpleNamespace(
                id=call_id,
                function=SimpleNamespace(name=name, arguments=json.dumps(args)),
            )
            for call_id, name, args in calls
        ]
    )


@pytest.fixture
def bound_agent(tmp_path):
    config = VulnClawConfig()
    config.session.output_dir = tmp_path / "output"
    config.session.solve_max_steps = 1
    config.subagent.enabled = False
    agent = AgentCore(config)
    context = create_run_context(
        command="test",
        targets=[parse_target("https://example.test")],
        runs_dir=tmp_path / "runs",
        run_name="tool-integration",
    )
    store = agent.bind_run_context(context)
    yield agent, context, store
    agent.release_run_binding()


@pytest.mark.asyncio
async def test_tool_result_is_committed_and_queryable_after_cache_eviction(bound_agent, monkeypatch):
    agent, context, store = bound_agent
    marker = "TAIL_MARKER_evidence"
    body = "A" * 9000 + "\n" + marker

    async def executor(tool, args):
        return body

    monkeypatch.setattr(agent, "_execute_mcp_tool", executor)
    results, _ = await handle_tool_calls_with_results(
        agent, _message(("c0", "fetch", {"url": "https://example.test/"}))
    )
    evidence_id = results[0]["evidence_id"]
    assert evidence_id.startswith("ev_")
    assert "durability=committed" in results[0]["content"]

    # Evict every in-memory descriptor; persisted evidence must remain readable.
    agent.session_state.agent_state.evidence.clear()

    view = execute_evidence_tool(
        agent, "evidence_view", {"evidence_id": evidence_id, "offset": 9000, "limit": 100}
    )
    assert marker in view
    listing = execute_evidence_tool(agent, "evidence_list", {"limit": 10})
    assert evidence_id in listing
    search = execute_evidence_tool(agent, "evidence_search", {"query": marker})
    assert evidence_id in search
    assert store.view(evidence_id, scope=agent.evidence_scope).text == body

    # The run-owned traffic store is also bound to this run.
    from vulnclaw.agent.builtin_tools import resolve_traffic_store

    assert str(resolve_traffic_store(agent).base_dir).startswith(str(context.run_dir))


@pytest.mark.asyncio
async def test_two_identical_tool_results_are_two_observations(bound_agent, monkeypatch):
    agent, _context, _store = bound_agent

    async def executor(tool, args):
        return "same body"

    monkeypatch.setattr(agent, "_execute_mcp_tool", executor)
    first, _ = await handle_tool_calls_with_results(
        agent, _message(("c0", "fetch", {"url": "https://example.test/"}))
    )
    second, _ = await handle_tool_calls_with_results(
        agent, _message(("c1", "fetch", {"url": "https://example.test/"}))
    )
    assert first[0]["evidence_id"] != second[0]["evidence_id"]


@pytest.mark.asyncio
async def test_tool_execution_error_is_saved_without_consuming_save_budget(bound_agent, monkeypatch):
    agent, _context, store = bound_agent

    async def executor(tool, args):
        raise TimeoutError("synthetic timeout")

    monkeypatch.setattr(agent, "_execute_mcp_tool", executor)
    results, _ = await handle_tool_calls_with_results(
        agent, _message(("c0", "fetch", {"url": "https://example.test/"}))
    )
    assert "execution_error" in results[0]
    observation = store.observation(results[0]["evidence_id"], scope=agent.evidence_scope)
    assert observation.execution.outcome == "timeout"
    assert store.health.failure_total == 0


@pytest.mark.asyncio
async def test_save_failure_below_limit_returns_pending_and_sixth_stops(bound_agent, monkeypatch):
    agent, _context, store = bound_agent
    store.error_limit = 5

    async def executor(tool, args):
        return "body"

    monkeypatch.setattr(agent, "_execute_mcp_tool", executor)

    def fail(path, data):
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(store, "_write_payload", fail)

    for index in range(5):
        results, _ = await handle_tool_calls_with_results(
            agent, _message((f"c{index}", "fetch", {"url": "https://example.test/"}))
        )
        assert "durability=pending" in results[0]["content"]
        assert store.health.failure_total == index + 1

    with pytest.raises(PersistenceErrorLimitExceeded):
        await handle_tool_calls_with_results(
            agent, _message(("c-six", "fetch", {"url": "https://example.test/"}))
        )
    assert store.health.failure_total == 6
