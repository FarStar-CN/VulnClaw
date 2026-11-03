from __future__ import annotations

import hashlib
import json

from vulnclaw.agent.agent_state import AgentState
from vulnclaw.agent.context import SessionState, VulnerabilityFinding
from vulnclaw.config.domain_models import EvidenceRef
from vulnclaw.evidence import EvidenceScope, EvidenceStore
from vulnclaw.evidence.migration import (
    migrate_embedded_checkpoint_evidence,
    migrate_finding_snapshots,
)
from vulnclaw.findings.types import EvidenceSnapshotRef
from vulnclaw.run_context import create_run_context
from vulnclaw.targets import parse_target


def _store(tmp_path):
    context = create_run_context(
        command="test",
        targets=[parse_target("https://example.test")],
        runs_dir=tmp_path / "runs",
        run_name="migration",
    )
    store = EvidenceStore(context.run_dir, run_id=context.run_id)
    scope = EvidenceScope(run_id=context.run_id, task_id="main", read_all=True)
    return context, store, scope


def test_embedded_checkpoint_body_is_imported_once_with_stable_id(tmp_path):
    context, store, scope = _store(tmp_path)
    try:
        state = AgentState(goal="capture flag")
        legacy = state.remember_tool_result(
            tool="fetch",
            arguments={"url": "https://example.test/"},
            output="flag{legacy_body}",
            target="https://example.test",
        )
        legacy_id = legacy.id
        state.record_verified_claim("flag from old session", [legacy.id])
        state.pin_fact("legacy fact", evidence_id=legacy.id)

        first = migrate_embedded_checkpoint_evidence(
            store,
            state,
            scope=scope,
            target="https://example.test",
            session=SessionState(target="https://example.test"),
        )
        second = migrate_embedded_checkpoint_evidence(
            store,
            state,
            scope=scope,
            target="https://example.test",
            session=SessionState(target="https://example.test"),
        )

        assert legacy_id in first
        new_id = first[legacy_id]
        assert new_id.startswith("ev_")
        assert second == {}
        assert state.evidence[0].id == new_id
        assert state.evidence[0].content == ""
        assert state.verified_claims[0].evidence_ids == [new_id]
        assert state.pinned_facts[0].evidence_id == new_id
        assert store.view(new_id, scope=scope).text == "flag{legacy_body}"
        mapping = json.loads(
            (context.run_dir / "evidence" / "imports").glob("checkpoint-*.json").__next__().read_text()
        )
        assert mapping["entries"][legacy_id] == new_id
    finally:
        store.close()


def test_snapshot_path_outside_its_root_is_not_copied(tmp_path):
    context, store, _scope = _store(tmp_path)
    try:
        outside = tmp_path / "outside"
        outside.mkdir()
        secret = tmp_path / "secret.json"
        raw = json.dumps(
            {"content": "out of root", "evidence_id": "e001"}, sort_keys=True
        ).encode()
        secret.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        finding = VulnerabilityFinding(
            title="Candidate",
            vuln_type="test",
            target="https://example.test",
            evidence_snapshots=[
                EvidenceSnapshotRef(
                    evidence_id="e001", source_id="e001", target="https://example.test",
                    root=str(outside), path="../secret.json", sha256=digest, fingerprint="e001",
                )
            ],
        )
        session = SessionState(target="https://example.test", findings=[finding])

        assert migrate_finding_snapshots(context.run_dir, session) == 0
        assert session.findings[0].evidence_snapshots[0].root == str(outside)
        assert not (context.run_dir / "evidence" / "findings" / "imported").exists()
    finally:
        store.close()


def test_absolute_finding_snapshot_is_copied_into_the_run_once(tmp_path):
    context, store, scope = _store(tmp_path)
    try:
        outside = tmp_path / "outside"
        outside.mkdir()
        payload = {"evidence_id": "e001", "target": "https://example.test", "content": "old bytes"}
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        digest = hashlib.sha256(raw).hexdigest()
        (outside / digest).write_bytes(raw)
        finding = VulnerabilityFinding(
            title="Candidate",
            vuln_type="test",
            target="https://example.test",
            evidence_snapshots=[
                EvidenceSnapshotRef(
                    evidence_id="e001", source_id="e001", target="https://example.test",
                    root=str(outside), path=digest, sha256=digest, fingerprint="e001",
                )
            ],
            evidence_refs=[EvidenceRef(kind="file", path=digest)],
        )
        session = SessionState(target="https://example.test", findings=[finding])

        copied = migrate_finding_snapshots(context.run_dir, session)
        second = migrate_finding_snapshots(context.run_dir, session)

        assert copied == 1
        assert second == 0
        ref = session.findings[0].evidence_snapshots[0]
        assert ref.root == "evidence"
        assert ref.sha256 == digest
        copied_path = context.run_dir / "evidence" / ref.path
        assert copied_path.read_bytes() == raw
    finally:
        store.close()
