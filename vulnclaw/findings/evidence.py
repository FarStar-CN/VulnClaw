"""Immutable snapshots of tool evidence, independent of hot-history eviction."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from vulnclaw.findings.types import EvidenceSnapshotRef, Snapshot
from vulnclaw.run_context import atomic_write_json


def scope_identity(target: str) -> tuple[str, int | None]:
    parsed = urlsplit(target if "://" in target else "//" + target)
    return (parsed.hostname or "").lower(), parsed.port


def snapshot_records(agent: Any, evidence_ids: list[str]) -> list[EvidenceSnapshotRef]:
    session = agent.context.state
    records = []
    for evidence_id in dict.fromkeys(evidence_ids):
        record = session.agent_state.get_evidence(evidence_id)
        if record is None or record.tool == "submit_finding":
            raise ValueError(f"Evidence is not available in this session: {evidence_id}")
        if record.target != session.target:
            raise ValueError(f"Evidence belongs to another target: {evidence_id}")
        records.append(record)
    root = (Path(agent.config.session.output_dir) / "evidence").resolve()
    namespace = hashlib.sha256(
        f"{session.started_at}:{session.target}:{session.task_id}".encode()
    ).hexdigest()[:24]
    result = []
    for record in records:
        snapshot = Snapshot(
            evidence_id=record.id, target=session.target or "", task_id=session.task_id,
            tool=record.tool, arguments=record.arguments, content=record.content,
            fingerprint=record.fingerprint,
        )
        payload = snapshot.model_dump(mode="json")
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        relative = f"findings/{namespace}/{digest}.json"
        atomic_write_json(root / relative, payload)
        result.append(EvidenceSnapshotRef(
            evidence_id=record.id, source_id=record.id, target=snapshot.target,
            root=str(root), path=relative, sha256=digest,
            fingerprint=record.fingerprint, task_id=session.task_id,
        ))
    return result


def load_snapshots(finding: Any) -> dict[str, Snapshot]:
    result = {}
    for ref in finding.evidence_snapshots:
        root = Path(ref.root).resolve()
        path = (root / ref.path).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Evidence snapshot path escapes its evidence directory")
        payload = json.loads(path.read_text(encoding="utf-8"))
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        if digest != ref.sha256:
            raise ValueError("Evidence snapshot integrity check failed")
        snapshot = Snapshot.model_validate(payload)
        if snapshot.evidence_id != ref.source_id or snapshot.target != ref.target:
            raise ValueError("Evidence snapshot ownership does not match its reference")
        if snapshot.target != finding.target:
            raise ValueError("Evidence snapshot belongs to another target")
        result[ref.evidence_id] = snapshot
    if not result:
        raise ValueError("No structured tool evidence is attached to this finding")
    return result
