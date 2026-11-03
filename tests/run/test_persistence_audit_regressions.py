from __future__ import annotations

import asyncio
import errno
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from vulnclaw.agent.context import SessionState, VulnerabilityFinding
from vulnclaw.agent.core import AgentCore
from vulnclaw.config.schema import VulnClawConfig
from vulnclaw.evidence import (
    CapturedObservation,
    EvidenceStore,
    EvidenceWriterBusyError,
    PersistenceErrorLimitExceeded,
    new_evidence_id,
)
from vulnclaw.findings.evidence import load_snapshots, snapshot_records
from vulnclaw.findings.types import FindingAssessment, finding_input_digest, require_assessed
from vulnclaw.orchestrator import run_agent_task
from vulnclaw.run_context import (
    atomic_write_json,
    create_run_context,
    ensure_writable_run_layout,
    load_run_context_from_dir,
    repair_run_context,
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


def capture(store, text="original tool response"):
    return CapturedObservation(
        evidence_id=new_evidence_id(), run_id=store.run_id, target=TARGET, text=text
    )


@pytest.mark.asyncio
async def test_regular_run_verifies_its_relative_finding_snapshots(tmp_path, monkeypatch):
    import vulnclaw.findings.verification as verification

    agent = agent_for(tmp_path)
    review = AsyncMock(
        return_value=FindingAssessment(
            outcome="suspected_high",
            method="evidence_review",
            complete=True,
            reason="synthetic offline assessment",
        )
    )
    monkeypatch.setattr(verification, "review_evidence", review)

    async def runner(shared):
        observed = capture(shared.evidence_store)
        shared.evidence_store.commit_observation(observed, scope=shared.evidence_scope)
        refs = snapshot_records(shared, [observed.evidence_id])
        shared.session_state.findings.append(
            VulnerabilityFinding(
                title="Audit fixture",
                vuln_type="test",
                target=TARGET,
                evidence_snapshots=refs,
            )
        )

    try:
        result = await run_agent_task(
            agent=agent, command="run", target=TARGET, resume=False, poc_verify=False, runner=runner
        )
        assessment = result.audited_session.findings[0].assessment
        assert assessment.complete, assessment.reason
        review.assert_awaited_once()
    finally:
        await agent.aclose()


@pytest.mark.asyncio
async def test_checkpoint_failure_counts_once_and_continues(tmp_path, monkeypatch):
    import vulnclaw.target_state.store as state_store

    agent = agent_for(tmp_path)
    observed = {}

    async def runner(shared):
        original = state_store.atomic_write_json

        def failing_current(path, payload):
            if Path(path).name == "current.json":
                raise OSError(errno.ENOSPC, "synthetic disk full")
            return original(path, payload)

        with monkeypatch.context() as patch:
            patch.setattr(state_store, "atomic_write_json", failing_current)
            try:
                shared.session_state._notify_checkpoint("audit-save")
            except OSError as exc:
                observed["escaped"] = str(exc)
        observed["total"] = shared.evidence_store.health.failure_total

    try:
        await run_agent_task(agent=agent, command="run", target=TARGET, resume=False, runner=runner)
        assert observed == {"total": 1}, observed
    finally:
        await agent.aclose()


@pytest.mark.asyncio
async def test_startup_checkpoint_error_releases_writer(tmp_path, monkeypatch):
    import vulnclaw.orchestrator as orchestrator

    agent = agent_for(tmp_path)

    def fail_save(*args, **kwargs):
        raise OSError(errno.ENOSPC, "synthetic initial checkpoint failure")

    monkeypatch.setattr(orchestrator, "save_target_state", fail_save)
    runner = AsyncMock()
    try:
        with pytest.raises(OSError):
            await run_agent_task(
                agent=agent, command="run", target=TARGET, resume=False, runner=runner
            )
        manifest_path = next((tmp_path / "runs").glob("*/*/run.json"))
        manifest = json.loads(manifest_path.read_text())
        runner.assert_not_awaited()
        with EvidenceStore(manifest_path.parent, run_id=manifest["run_id"]):
            pass
    finally:
        await agent.aclose()


def test_legacy_move_respects_active_run_writer(tmp_path):
    context = context_for(tmp_path, legacy=True)
    original = context.run_dir
    with EvidenceStore(original, run_id=context.run_id):
        with pytest.raises(EvidenceWriterBusyError):
            ensure_writable_run_layout(context)
        assert (original / "run.json").exists()


@pytest.mark.asyncio
async def test_target_resume_into_new_run_preserves_evidence_readability(tmp_path, monkeypatch):
    from vulnclaw.evidence import EvidenceNotFoundError, EvidenceScope
    from vulnclaw.evidence.models import ObservationAcquisition, ObservationProducer

    monkeypatch.setattr(
        "vulnclaw.findings.verification.review_evidence",
        AsyncMock(return_value=FindingAssessment(
            outcome="suspected_high", method="evidence_review", complete=True,
            reason="synthetic offline assessment",
        )),
    )
    first = agent_for(tmp_path)
    second = agent_for(tmp_path)
    saved = {}

    async def first_runner(shared):
        observation = capture(shared.evidence_store, "previous-run-evidence")
        observation.acquisition = ObservationAcquisition(
            started_at="2026-01-01T00:00:00Z", finished_at="2026-01-01T00:00:01Z",
            time_source="tool",
        )
        shared.evidence_store.commit_observation(observation, scope=shared.evidence_scope)
        shared.session_state.agent_state.remember_observation(
            shared.evidence_store.describe(observation.evidence_id, scope=shared.evidence_scope)
        )
        saved["evidence_id"] = observation.evidence_id
        saved["run_id"] = shared.evidence_store.run_id
        saved["run_dir"] = shared.run_context.run_dir
        saved["acquisition"] = observation.acquisition
        shared.session_state.findings.append(VulnerabilityFinding(
            title="Restored fixture", vuln_type="test", target=TARGET,
            evidence_snapshots=snapshot_records(shared, [observation.evidence_id]),
        ))
        # Cold evidence may already have left the bounded descriptor cache.
        cold = capture(shared.evidence_store, "cold evidence")
        shared.evidence_store.commit_observation(cold, scope=shared.evidence_scope)
        saved["cold_id"] = cold.evidence_id
        private = capture(shared.evidence_store, "private child evidence")
        private.producer = ObservationProducer(task_id="unpublished-child")
        shared.evidence_store.commit_observation(
            private, scope=EvidenceScope(run_id=saved["run_id"], task_id="unpublished-child")
        )
        saved["private_id"] = private.evidence_id
        shared.evidence_store.health.failure_total = 2
        shared.evidence_store.health.failures_by_stage = {"context_state": 2}

    async def second_runner(shared):
        assert shared.evidence_store.run_id != saved["run_id"]
        assert any(
            item.id == saved["evidence_id"] for item in shared.session_state.agent_state.evidence
        )
        assert (
            shared.evidence_store.view(saved["evidence_id"], scope=shared.evidence_scope).text
            == "previous-run-evidence"
        )
        assert shared.evidence_store.health.failure_total == 0
        observation = shared.evidence_store.observation(saved["evidence_id"], scope=shared.evidence_scope)
        assert observation.acquisition == saved["acquisition"]
        assert observation.provenance.origin_run_id == saved["run_id"]
        assert observation.provenance.imported_from_run_id == saved["run_id"]
        assert shared.evidence_store.view(saved["cold_id"], scope=shared.evidence_scope).text == (
            "cold evidence"
        )
        with pytest.raises(EvidenceNotFoundError):
            shared.evidence_store.view(saved["private_id"], scope=shared.evidence_scope)
        require_assessed(shared.session_state)
        # The imported snapshot is readable without consulting the original directory.
        relocated_source = saved["run_dir"].with_name("source-temporarily-away")
        saved["run_dir"].rename(relocated_source)
        try:
            snapshots = load_snapshots(shared.session_state.findings[0], run_root=shared.run_context.run_dir)
            assert snapshots[saved["evidence_id"]].content == "previous-run-evidence"
        finally:
            relocated_source.rename(saved["run_dir"])

    try:
        await run_agent_task(
            agent=first, command="run", target=TARGET, resume=False, runner=first_runner
        )
        await run_agent_task(
            agent=second, command="run", target=TARGET, resume=True, runner=second_runner
        )
    finally:
        await first.aclose()
        await second.aclose()


def test_moving_legacy_run_preserves_absolute_snapshot_references(tmp_path):
    from types import SimpleNamespace

    from vulnclaw.evidence.migration import migrate_finding_snapshots

    context = context_for(tmp_path, legacy=True)
    session = SessionState(target=TARGET)
    record = session.agent_state.remember_tool_result(
        tool="fetch",
        arguments={"url": TARGET},
        output="historical evidence",
        target=TARGET,
    )
    config = VulnClawConfig()
    config.session.output_dir = context.run_dir
    old_agent = SimpleNamespace(context=SimpleNamespace(state=session), config=config)
    refs = snapshot_records(old_agent, [record.id])
    finding = VulnerabilityFinding(
        title="Legacy fixture", vuln_type="test", target=TARGET, evidence_snapshots=refs
    )
    session.findings.append(finding)
    finding.assessment = FindingAssessment(
        outcome="suspected_high", method="evidence_review", reason="prior review",
        input_digest=finding_input_digest(finding),
    )
    assert load_snapshots(finding)[record.id].content == "historical evidence"
    session.resume_meta["run_dir"] = str(context.run_dir)
    atomic_write_json(context.state_path(), session.model_dump(mode="json"))
    historical = context.state_path().parent / "snapshots" / "before-move.json"
    atomic_write_json(historical, session.model_dump(mode="json"))

    moved = ensure_writable_run_layout(context)
    restored = SessionState.model_validate_json(moved.state_path().read_text())
    migrate_finding_snapshots(moved.run_dir, restored)
    loaded = load_snapshots(restored.findings[0], run_root=moved.run_dir)
    assert loaded[record.id].content == "historical evidence"
    require_assessed(restored)
    historical = moved.state_path().parent / "snapshots" / "before-move.json"
    restored = SessionState.model_validate_json(historical.read_text())
    assert load_snapshots(restored.findings[0], run_root=moved.run_dir)[record.id].content == (
        "historical evidence"
    )


@pytest.mark.asyncio
async def test_sixth_checkpoint_failure_stops_and_success_does_not_reset_total(tmp_path, monkeypatch):
    import vulnclaw.target_state.store as state_store

    agent = agent_for(tmp_path)
    counts = []

    async def runner(shared):
        original = state_store.atomic_write_json

        def fail_current(path, payload):
            if Path(path).name == "current.json":
                raise OSError(errno.ENOSPC, "synthetic disk full")
            return original(path, payload)

        for number in range(6):
            with monkeypatch.context() as patch:
                patch.setattr(state_store, "atomic_write_json", fail_current)
                shared.session_state._notify_checkpoint(f"failure-{number}")
            shared.session_state._notify_checkpoint("successful-save")
            counts.append(shared.evidence_store.health.failure_total)
        pytest.fail("execution continued after the sixth failure")

    with pytest.raises(PersistenceErrorLimitExceeded) as stopped:
        await run_agent_task(
            agent=agent, command="run", target=TARGET, resume=False, runner=runner
        )
    assert counts == [1, 2, 3, 4, 5]
    assert stopped.value.failure_total == 6
    assert stopped.value.stage == "context_state"
    assert agent.evidence_store is None
    manifest_path = next((tmp_path / "runs").glob("*/*/run.json"))
    manifest = json.loads(manifest_path.read_text())
    assert manifest["status"] == "failed"
    with EvidenceStore(manifest_path.parent, run_id=manifest["run_id"], error_limit=6) as reopened:
        assert reopened.health.failure_total == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_failed_cleanup_preserves_original_error_and_releases_writer(
    tmp_path, monkeypatch, cancelled
):
    import vulnclaw.orchestrator as orchestrator

    agent = agent_for(tmp_path)
    failure = asyncio.CancelledError() if cancelled else RuntimeError("original runner error")

    async def runner(shared):
        def fail(*args, **kwargs):
            raise OSError(errno.ENOSPC, "cleanup write failed")

        monkeypatch.setattr(orchestrator, "save_target_state", fail)
        monkeypatch.setattr(orchestrator, "mark_run_status", fail)
        raise failure

    with pytest.raises(type(failure)) as raised:
        await run_agent_task(
            agent=agent, command="run", target=TARGET, resume=False, runner=runner
        )
    assert raised.value is failure
    manifest_path = next((tmp_path / "runs").glob("*/*/run.json"))
    manifest = json.loads(manifest_path.read_text())
    with EvidenceStore(manifest_path.parent, run_id=manifest["run_id"]):
        pass


def test_repair_respects_active_writer(tmp_path):
    context = context_for(tmp_path)
    atomic_write_json(context.state_path().parent / "snapshots" / "saved.json", {"target": TARGET})
    with EvidenceStore(context.run_dir, run_id=context.run_id):
        with pytest.raises(EvidenceWriterBusyError):
            repair_run_context(context.run_name, runs_dir=context.runs_root)


def test_repair_of_legacy_run_can_replace_a_corrupt_current_state(tmp_path):
    context = context_for(tmp_path, legacy=True)
    state = SessionState(target=TARGET)
    state.resume_meta["run_dir"] = str(context.run_dir)
    atomic_write_json(
        context.state_path().parent / "snapshots" / "saved.json", state.model_dump(mode="json")
    )
    context.state_path().write_text("{interrupted JSON")

    repaired = repair_run_context(context.run_name, runs_dir=context.runs_root)

    assert not context.run_dir.exists()
    restored = SessionState.model_validate_json(repaired.state_path().read_text())
    assert restored.target == TARGET
    assert restored.resume_meta["run_dir"] == str(repaired.run_dir)


def test_exhausted_budget_blocks_legacy_move_before_mutation(tmp_path):
    context = context_for(tmp_path, legacy=True)
    atomic_write_json(
        context.run_dir / "evidence" / "health.json",
        {"run_id": context.run_id, "failure_total": 6, "failures_by_stage": {"context_state": 6}},
    )
    with pytest.raises(PersistenceErrorLimitExceeded):
        ensure_writable_run_layout(context)
    assert context.run_dir.exists()
    assert not (context.run_dir / "evidence" / "imports" / "layout-migration.json").exists()


def test_interrupted_rebase_is_finished_on_next_writable_resume(tmp_path, monkeypatch):
    import vulnclaw.run_context as runs

    context = context_for(tmp_path, legacy=True)
    def fail_rebase(*args, **kwargs):
        raise OSError(errno.ENOSPC, "interrupted rebase")

    with monkeypatch.context() as patch:
        patch.setattr(runs, "_rebase_current_state_paths", fail_rebase)
        with pytest.raises(OSError):
            ensure_writable_run_layout(context)
    destination = next(context.runs_root.glob("*/*/run.json")).parent
    moved = load_run_context_from_dir(destination, runs_root=context.runs_root)
    assert moved.schema_version == 1
    finished = ensure_writable_run_layout(moved)
    assert finished.schema_version == 2
