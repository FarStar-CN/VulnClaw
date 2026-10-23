"""Dedicated engines retain their business parameters and application ownership."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from vulnclaw.agent.context import SessionState
from vulnclaw.application import ApplicationStreamSink
from vulnclaw.config.schema import VulnClawConfig
from vulnclaw.orchestrator import OrchestratorRunResult
from vulnclaw.target_state.store import SessionRestoreResult
from vulnclaw.task_operations import NetworkScanRequest, execute_network_scan, execute_solve
from vulnclaw.task_service import TaskCreateRequest


def application():
    config = VulnClawConfig()
    state = SessionState()
    agent = SimpleNamespace(
        session_state=state,
        solve=AsyncMock(),
        auto_pentest=AsyncMock(),
        apply_task_constraints=lambda constraints: setattr(state, "task_constraints", constraints),
    )
    app = SimpleNamespace(
        agent=agent, config=config, runtime=SimpleNamespace(agent=agent), current_constraints={}
    )
    calls = []

    async def orchestrated(**kwargs):
        calls.append(kwargs)
        await kwargs["runner"](agent)
        return OrchestratorRunResult(SessionRestoreResult(target=kwargs["target"]), {})

    app.run_orchestrated = orchestrated
    return (
        app,
        calls,
        ApplicationStreamSink(lambda *_args, **_fields: None, "task", show_thinking=False),
    )


async def test_solve_uses_explicit_goal_budgets_scope_and_exact_run():
    app, calls, sink = application()
    request = TaskCreateRequest(
        command="run",
        target="https://synthetic.test",
        resume_run_name="saved-run",
        repair=True,
        options={"only_port": 443, "max_steps": 37, "max_tool_rounds": 8},
    )
    result = await execute_solve(app, sink, request, "synthetic success condition")
    kwargs = app.agent.solve.call_args.kwargs
    assert kwargs["goal"] == "synthetic success condition"
    assert kwargs["max_steps"] == 37
    assert kwargs["max_tool_rounds"] == 8
    assert kwargs["task_constraints"].allowed_ports == [443]
    assert callable(kwargs["on_event"])
    assert calls[0]["resume_run_name"] == "saved-run"
    assert calls[0]["repair"] is True
    assert calls[0]["command"] == "solve"
    assert result["command"] == "solve"


async def test_network_scan_keeps_cidr_and_safe_action_boundary():
    app, calls, sink = application()
    request = NetworkScanRequest(
        task=TaskCreateRequest(command="run", target="192.0.2.0/24", options={"ports": "80,443"}),
        profile="stealth",
        max_rounds=7,
    )
    result = await execute_network_scan(app, sink, request)
    kwargs = app.agent.auto_pentest.call_args.kwargs
    assert kwargs["target"] == "192.0.2.0/24"
    assert kwargs["max_rounds"] == 7
    constraints = kwargs["task_constraints"]
    assert constraints.allowed_hosts == []
    assert set(constraints.allowed_actions) == {"recon", "scan"}
    prompt = app.agent.auto_pentest.call_args.args[0]
    assert "profile=stealth" in prompt
    assert "80,443" in prompt
    assert calls[0]["command"] == "network-scan"
    assert result["network_profile"] == "stealth"


async def test_network_scan_parallel_parameters_reach_existing_supervisor(monkeypatch):
    app, calls, sink = application()
    supervisor = AsyncMock()
    monkeypatch.setattr("vulnclaw.agent.parallel_agents.run_parallel_pentest", supervisor)
    request = NetworkScanRequest(
        task=TaskCreateRequest(command="run", target="192.0.2.1"),
        parallel_agents=3,
        parallel_depth=2,
        worker_rounds=4,
        surface_limit=12,
    )
    await execute_network_scan(app, sink, request)
    assert not app.agent.auto_pentest.called
    kwargs = supervisor.call_args.kwargs
    assert kwargs["max_agents"] == 3
    assert kwargs["max_depth"] == 2
    assert kwargs["worker_rounds"] == 4
    assert kwargs["surface_limit"] == 12


@pytest.mark.parametrize("options", [{"engine": "team"}, {"max_steps": 15}, {"continuous": True}])
def test_network_input_rejects_options_it_would_not_execute(options):
    with pytest.raises(ValueError, match="unsupported network task options"):
        NetworkScanRequest(
            task=TaskCreateRequest(command="run", target="192.0.2.1", options=options)
        )
