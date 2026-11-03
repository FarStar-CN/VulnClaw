from __future__ import annotations

import os

import pytest

from vulnclaw.agent.context import SessionState
from vulnclaw.agent.core import AgentCore
from vulnclaw.config.schema import VulnClawConfig
from vulnclaw.run_context import (
    atomic_write_json,
    create_run_context,
    load_run_context_from_dir,
)
from vulnclaw.targets import parse_target

TARGET = "https://audit.example.test"


@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    import vulnclaw.run_context as runs
    import vulnclaw.target_state.store as state_store

    monkeypatch.setattr(runs, "ensure_dirs", lambda: None)
    monkeypatch.setattr(state_store, "TARGETS_DIR", tmp_path / "target-index")


def agent_for(tmp_path):
    config = VulnClawConfig()
    config.session.output_dir = tmp_path / "output"
    config.session.runs_dir = tmp_path / "runs"
    config.session.experience_enabled = False
    agent = AgentCore(config)
    agent.session_state.target = TARGET
    return agent


def context_for(tmp_path, *, legacy=False):
    context = create_run_context(
        command="run",
        targets=[parse_target(TARGET)],
        runs_dir=tmp_path / "runs",
        run_name="audit-run",
    )
    atomic_write_json(context.state_path(), SessionState(target=TARGET).model_dump(mode="json"))
    if legacy:
        flat = context.runs_root / "audit-run"
        os.rename(context.run_dir, flat)
        manifest = dict(context.manifest, schema_version=1)
        atomic_write_json(flat / "run.json", manifest)
        context = load_run_context_from_dir(flat, runs_root=context.runs_root)
    return context


def test_binding_run_preserves_existing_long_term_memory(tmp_path):
    agent = agent_for(tmp_path)
    agent.context.memory_store.save("audit-key", "remembered value")
    try:
        agent.bind_run_context(context_for(tmp_path))
        assert agent.context.memory_store.retrieve("audit-key") == "remembered value"
    finally:
        agent.release_run_binding()


def test_rebinding_same_run_restores_archived_message_scope(tmp_path):
    context = context_for(tmp_path)
    first = agent_for(tmp_path)
    second = agent_for(tmp_path)
    try:
        first.bind_run_context(context)
        first.context._archive_messages(
            [{"role": "user", "content": "audit-archive-token"}],
            kind="history",
        )
        assert first.context.search_cold_memory("audit-archive-token")
        first.release_run_binding()
        second.bind_run_context(context)
        assert second.context.search_cold_memory("audit-archive-token")
    finally:
        first.release_run_binding()
        second.release_run_binding()
