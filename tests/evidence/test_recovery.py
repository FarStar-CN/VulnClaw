from __future__ import annotations

import errno
import json
import shutil
from pathlib import Path

import pytest

from vulnclaw.evidence import (
    CapturedObservation,
    EvidenceScope,
    EvidenceStore,
    EvidenceWriterBusyError,
    read_persistence_health,
)
from vulnclaw.evidence.models import ObservationExecution, ObservationTool
from vulnclaw.run_context import atomic_write_json, create_run_context
from vulnclaw.targets import parse_target


def _run(tmp_path):
    return create_run_context(
        command="test",
        targets=[parse_target("https://example.test")],
        runs_dir=tmp_path / "runs",
        run_name="recovery-test",
    )


def _capture(store, text, target="https://example.test"):
    from vulnclaw.evidence import new_evidence_id

    return CapturedObservation(
        evidence_id=new_evidence_id(),
        run_id=store.run_id,
        target=target,
        tool=ObservationTool(name="fetch", arguments={"url": target}),
        execution=ObservationExecution(outcome="succeeded"),
        text=text,
        summary="fetch succeeded",
    )


def test_second_writer_is_refused_and_read_only_works(tmp_path):
    context = _run(tmp_path)
    first = EvidenceStore(context.run_dir, run_id=context.run_id)
    try:
        with pytest.raises(EvidenceWriterBusyError):
            EvidenceStore(context.run_dir, run_id=context.run_id)
        reader = EvidenceStore(context.run_dir, run_id=context.run_id, writable=False)
        try:
            assert reader.max_commit_seq == 0
        finally:
            reader.close()
    finally:
        first.close()


def test_health_mirror_uses_largest_valid_total(tmp_path):
    context = _run(tmp_path)
    store = EvidenceStore(context.run_dir, run_id=context.run_id)
    scope = EvidenceScope(run_id=context.run_id, task_id="main")
    store.commit_observation(_capture(store, "x"), scope=scope)
    store.close()

    atomic_write_json(
        store.health_path,
        {
            "schema_version": 1,
            "run_id": context.run_id,
            "failure_total": 2,
            "failures_by_stage": {"record": 2},
        },
    )
    state_path = context.state_path()
    atomic_write_json(
        state_path,
        {
            "persistence_health": {
                "schema_version": 1,
                "run_id": context.run_id,
                "failure_total": 4,
                "failures_by_stage": {"payload": 3, "record": 1},
            }
        },
    )

    mirror = read_persistence_health(context.run_dir, run_id=context.run_id)
    assert mirror is not None and mirror.failure_total == 4
    restored = EvidenceStore(context.run_dir, run_id=context.run_id, restored_health=mirror)
    try:
        assert restored.health.failure_total == 4
        assert restored.health.failures_by_stage == {"payload": 3, "record": 1}
    finally:
        restored.close()


def test_health_mirror_from_another_run_is_ignored(tmp_path):
    context = _run(tmp_path)
    store = EvidenceStore(context.run_dir, run_id=context.run_id)
    store.close()
    atomic_write_json(
        store.health_path,
        {
            "schema_version": 1,
            "run_id": "another-run",
            "failure_total": 99,
        },
    )
    restored = EvidenceStore(context.run_dir, run_id=context.run_id)
    try:
        assert restored.health.failure_total == 0
    finally:
        restored.close()


def test_restart_keeps_commit_sequence_and_does_not_revive_pending(tmp_path, monkeypatch):
    context = _run(tmp_path)
    store = EvidenceStore(context.run_dir, run_id=context.run_id)
    scope = EvidenceScope(run_id=context.run_id, task_id="main")
    committed = _capture(store, "committed")
    store.commit_observation(committed, scope=scope)
    seq = store.max_commit_seq

    def fail(path, data):
        raise OSError(errno.ENOSPC, "full")

    monkeypatch.setattr(store, "_write_payload", fail)
    pending = _capture(store, "pending")
    store.commit_observation(pending, scope=scope)
    assert store.pending_count == 1
    store.close()

    restarted = EvidenceStore(context.run_dir, run_id=context.run_id)
    try:
        assert restarted.pending_count == 0
        assert restarted.max_commit_seq == seq
        next_capture = _capture(restarted, "next")
        outcome = restarted.commit_observation(next_capture, scope=scope)
        assert outcome.descriptor.commit_seq == seq + 1
    finally:
        restarted.close()


def test_moving_the_run_directory_keeps_relative_references(tmp_path):
    context = _run(tmp_path)
    store = EvidenceStore(context.run_dir, run_id=context.run_id)
    scope = EvidenceScope(run_id=context.run_id, task_id="main")
    captured = _capture(store, "move me")
    store.commit_observation(captured, scope=scope)
    store.close()

    moved = tmp_path / "moved-run"
    shutil.move(str(context.run_dir), moved)
    reader = EvidenceStore(moved, run_id=context.run_id, writable=False)
    try:
        assert reader.view(captured.evidence_id, scope=scope).text == "move me"
        record = json.loads(
            (moved / "evidence" / "records" / f"{captured.evidence_id}.json").read_text()
        )
        assert not Path(record["payload"]["path"]).is_absolute()
    finally:
        reader.close()


def test_corrupt_record_is_reported_unavailable(tmp_path):
    context = _run(tmp_path)
    store = EvidenceStore(context.run_dir, run_id=context.run_id)
    scope = EvidenceScope(run_id=context.run_id, task_id="main")
    captured = _capture(store, "body")
    store.commit_observation(captured, scope=scope)
    record_path = store.run_dir / "evidence" / "records" / f"{captured.evidence_id}.json"
    record_path.write_text("{not json")
    store._scan_records()

    descriptor = store.describe(captured.evidence_id, scope=scope)
    assert descriptor.ledger_state == "unavailable"
    store.close()


def test_index_is_dirty_until_a_boundary_flush(tmp_path):
    context = _run(tmp_path)
    store = EvidenceStore(context.run_dir, run_id=context.run_id)
    scope = EvidenceScope(run_id=context.run_id, task_id="main")
    store.commit_observation(_capture(store, "x"), scope=scope)
    assert not store.index_path.exists()
    assert store.flush_index() is True
    assert store.index_path.exists()
    payload = json.loads(store.index_path.read_text())
    assert payload["run_id"] == context.run_id
    assert payload["max_commit_seq"] == 1
    store.close()
