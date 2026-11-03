from __future__ import annotations

import errno
import json
from pathlib import Path

import pytest

from vulnclaw.agent.core import AgentCore
from vulnclaw.config.schema import VulnClawConfig
from vulnclaw.evidence import (
    CapturedObservation,
    EvidenceScope,
    EvidenceStore,
    PersistenceErrorLimitExceeded,
    read_persistence_health,
)
from vulnclaw.evidence.models import ObservationExecution, ObservationTool, new_evidence_id
from vulnclaw.orchestrator import run_agent_task
from vulnclaw.run_context import load_run_context


def _agent(tmp_path, error_limit=5):
    config = VulnClawConfig()
    config.session.output_dir = tmp_path / "output"
    config.session.runs_dir = tmp_path / "runs"
    config.session.experience_enabled = False
    config.session.persistence_error_limit = error_limit
    return AgentCore(config)


def _capture(store, text="observed body"):
    return CapturedObservation(
        evidence_id=new_evidence_id(),
        run_id=store.run_id,
        target="https://example.test",
        tool=ObservationTool(name="fetch", arguments={"url": "https://example.test/"}),
        execution=ObservationExecution(outcome="succeeded"),
        text=text,
        summary="fetch succeeded",
    )


@pytest.mark.asyncio
async def test_orchestrator_binds_store_and_checkpoints_watermark(tmp_path):
    agent = _agent(tmp_path)
    committed = {}

    async def runner(shared):
        store = shared.evidence_store
        assert store is not None
        captured = _capture(store)
        store.commit_observation(captured, scope=shared.evidence_scope)
        shared.session_state.agent_state.remember_observation(
            store.describe(captured.evidence_id, scope=shared.evidence_scope)
        )
        committed["id"] = captured.evidence_id
        committed["run_dir"] = store.run_dir

    result = await run_agent_task(
        agent=agent,
        command="run",
        target="https://example.test",
        resume=False,
        runner=runner,
    )

    assert result.status == "completed"
    run_dir = Path(result.summary["run_dir"])
    assert run_dir == committed["run_dir"]
    state = json.loads((run_dir / "targets" / result.run_context.targets[0].target_id / "state" / "current.json").read_text())
    agent_state = state["agent_state"]
    assert agent_state["checkpoint_commit_seq"] == 1
    assert agent_state["evidence"][0]["id"] == committed["id"]
    assert "content" not in agent_state["evidence"][0]
    assert agent_state["persistence_health"]["failure_total"] == 0

    reader = EvidenceStore(run_dir, run_id=result.run_context.run_id, writable=False)
    try:
        text = reader.view(
            committed["id"],
            scope=EvidenceScope(run_id=result.run_context.run_id, task_id="main", read_all=True),
        ).text
        assert text == "observed body"
    finally:
        reader.close()


@pytest.mark.asyncio
async def test_resume_keeps_run_id_counter_and_reads_earlier_evidence(tmp_path):
    first_agent = _agent(tmp_path)
    first_ids = []

    async def first_runner(shared):
        store = shared.evidence_store
        captured = _capture(store, "before restart")
        store.commit_observation(captured, scope=shared.evidence_scope)
        first_ids.append(captured.evidence_id)

    first = await run_agent_task(
        agent=first_agent, command="run", target="https://example.test",
        resume=False, runner=first_runner,
    )
    run_id = first.run_context.run_id
    run_name = first.summary["run_name"]
    run_dir = Path(first.summary["run_dir"])

    second_agent = _agent(tmp_path)
    seen = {}

    async def second_runner(shared):
        store = shared.evidence_store
        assert store.run_id == run_id
        seen["text"] = store.view(
            first_ids[0], scope=shared.evidence_scope
        ).text
        captured = _capture(store, "after restart")
        outcome = store.commit_observation(captured, scope=shared.evidence_scope)
        seen["seq"] = outcome.descriptor.commit_seq

    second = await run_agent_task(
        agent=second_agent, command="run", target="https://example.test",
        resume=True, resume_run_name=run_name, runner=second_runner,
    )

    assert second.run_context.run_dir == run_dir
    assert second.run_context.run_id == run_id
    assert seen["text"] == "before restart"
    assert seen["seq"] == 2


@pytest.mark.asyncio
async def test_restored_error_budget_stops_before_running(tmp_path):
    warm = _agent(tmp_path)
    first = await run_agent_task(
        agent=warm, command="run", target="https://example.test",
        resume=False, runner=lambda shared: _noop(),
    )
    run_dir = Path(first.summary["run_dir"])
    run_name = first.summary["run_name"]

    health_path = run_dir / "evidence" / "health.json"
    health = json.loads(health_path.read_text()) if health_path.exists() else {
        "schema_version": 1, "run_id": first.run_context.run_id,
    }
    health["failure_total"] = 6
    health["failures_by_stage"] = {"payload": 6}
    health_path.write_text(json.dumps(health))

    agent = _agent(tmp_path, error_limit=5)
    called = []

    async def runner(shared):
        called.append(True)

    with pytest.raises(PersistenceErrorLimitExceeded):
        await run_agent_task(
            agent=agent, command="run", target="https://example.test",
            resume=True, resume_run_name=run_name, runner=runner,
        )
    assert called == []
    assert agent.evidence_store is None


@pytest.mark.asyncio
async def test_legacy_flat_run_is_moved_on_writable_resume(tmp_path):
    warm = _agent(tmp_path)
    first = await run_agent_task(
        agent=warm, command="run", target="https://example.test",
        resume=False, runner=lambda shared: _noop(),
    )
    original_run_dir = Path(first.summary["run_dir"])
    run_id = first.run_context.run_id
    runs = tmp_path / "runs"

    flat = runs / "old-flat-name"
    import shutil

    shutil.move(str(original_run_dir), flat)
    shutil.rmtree(original_run_dir.parent, ignore_errors=True)
    manifest = json.loads((flat / "run.json").read_text())
    manifest["schema_version"] = 1
    manifest["run_name"] = "old-flat-name"
    (flat / "run.json").write_text(json.dumps(manifest))

    agent = _agent(tmp_path)
    resumed = await run_agent_task(
        agent=agent, command="run", target="https://example.test",
        resume=True, resume_run_name="old-flat-name", runner=lambda shared: _noop(),
    )

    moved = resumed.run_context.run_dir
    assert not flat.exists()
    # The migration restores the canonical target/time location derived from the
    # original created_at and run id.
    assert moved.exists() and moved == original_run_dir
    assert resumed.run_context.run_id == run_id
    assert resumed.run_context.manifest["schema_version"] == 2
    assert resumed.run_context.created_at == first.run_context.created_at
    assert load_run_context("old-flat-name", runs_dir=runs).run_dir == moved


@pytest.mark.asyncio
async def test_pending_evidence_is_flushed_before_completion(tmp_path, monkeypatch):
    agent = _agent(tmp_path)
    pending_id = {}

    async def runner(shared):
        store = shared.evidence_store
        original = store._write_payload

        def fail(path, data):
            raise OSError(errno.ENOSPC, "full")

        monkeypatch.setattr(store, "_write_payload", fail)
        captured = _capture(store, "retry at completion")
        outcome = store.commit_observation(captured, scope=shared.evidence_scope)
        assert outcome.durability == "pending"
        pending_id["id"] = captured.evidence_id
        monkeypatch.setattr(store, "_write_payload", original)

    result = await run_agent_task(
        agent=agent, command="run", target="https://example.test",
        resume=False, runner=runner,
    )
    assert result.status == "completed"
    reader = EvidenceStore(
        Path(result.summary["run_dir"]), run_id=result.run_context.run_id, writable=False
    )
    try:
        assert reader.view(
            pending_id["id"],
            scope=EvidenceScope(run_id=result.run_context.run_id, task_id="main", read_all=True),
        ).text == "retry at completion"
    finally:
        reader.close()
    mirror = read_persistence_health(Path(result.summary["run_dir"]), run_id=result.run_context.run_id)
    assert mirror is not None and mirror.failure_total == 1


async def _noop():
    return None
