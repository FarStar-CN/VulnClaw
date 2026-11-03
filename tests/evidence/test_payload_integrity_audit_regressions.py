from __future__ import annotations

import os

import pytest

from vulnclaw.agent.context import SessionState
from vulnclaw.evidence import (
    CapturedObservation,
    EvidenceScope,
    EvidenceStore,
    EvidenceUnavailableError,
    new_evidence_id,
)
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


@pytest.mark.parametrize("reader", ["view", "read_text", "search"])
def test_truncated_payload_is_not_returned_as_committed(tmp_path, reader):
    context = context_for(tmp_path)
    with EvidenceStore(context.run_dir, run_id=context.run_id) as store:
        scope = EvidenceScope(run_id=context.run_id, task_id="main")
        observed = capture(store, "important original evidence")
        store.commit_observation(observed, scope=scope)
        (store.payloads_dir / f"{observed.evidence_id}.txt").write_text("important")
        assert store.describe(observed.evidence_id, scope=scope).ledger_state == "unavailable"
        if reader == "search":
            result = store.search("important", scope=scope)
            assert not result.hits, "search returned a committed hit from a truncated payload"
            assert observed.evidence_id in result.unsearched_ids
        else:
            with pytest.raises(EvidenceUnavailableError):
                getattr(store, reader)(observed.evidence_id, scope=scope)
