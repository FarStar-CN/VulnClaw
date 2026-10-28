"""Shared real-application fixtures for machine command integration tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from vulnclaw.agent.core import AgentCore
from vulnclaw.agent.exec_gate import reset_execution_gate
from vulnclaw.application import ApplicationRuntime
from vulnclaw.config.schema import VulnClawConfig


@pytest.fixture
def headless_runtime(monkeypatch, tmp_path):
    import vulnclaw.config.settings as settings
    import vulnclaw.target_state.store as store

    reset_execution_gate()
    config = VulnClawConfig()
    config.llm.api_key = "synthetic-test-key"
    config.session.output_dir = tmp_path / "reports"
    config.session.auto_save = False
    config.session.runs_dir = str(tmp_path / "runs")
    monkeypatch.setenv("VULNCLAW_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(settings, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(settings, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(settings, "load_config", lambda: config)
    monkeypatch.setattr(store, "TARGETS_DIR", tmp_path / "targets")
    monkeypatch.setattr("vulnclaw.cli.tasks.load_config", lambda: config)
    monkeypatch.setattr(AgentCore, "_report_kb_status", lambda self: None)
    monkeypatch.setattr(
        "vulnclaw.orchestrator._schedule_completed_run_distillation", lambda *_args: None
    )
    monkeypatch.setattr(
        "vulnclaw.report.generator._generate_attack_summary_from_session",
        AsyncMock(side_effect=lambda _state: "synthetic summary"),
    )
    agent = AgentCore(config)
    agent.solve = AsyncMock(return_value=SimpleNamespace(completed=False, steps=1))
    agent.auto_pentest = AsyncMock(return_value=[])
    agent.chat = AsyncMock(return_value=SimpleNamespace(output="synthetic answer"))
    agent._generate_attack_summary = AsyncMock(return_value="synthetic summary")
    manager = SimpleNamespace(astop_all=AsyncMock(), wait_for_startup=AsyncMock())
    runtimes = []

    def create():
        runtime = ApplicationRuntime(config, manager, agent)
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr("vulnclaw.application.create_runtime", create)
    yield SimpleNamespace(
        config=config, agent=agent, manager=manager, runtimes=runtimes, root=tmp_path
    )
    reset_execution_gate()
