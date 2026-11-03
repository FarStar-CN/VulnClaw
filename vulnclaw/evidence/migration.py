"""One-time import of legacy evidence embedded in checkpoints.

Old ``AgentState.evidence`` records carried their full body in ``current.json``
(and in every snapshot).  The descriptor model keeps that body only in memory
during load so it can be committed once into ``evidence/records``/``payloads``;
new checkpoints never embed it again.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

from vulnclaw.evidence.models import (
    REPRESENTATION_TOOL_TEXT,
    CapturedObservation,
    EvidenceScope,
    ObservationAcquisition,
    ObservationCompleteness,
    ObservationExecution,
    ObservationIdentity,
    ObservationProducer,
    ObservationProvenance,
    ObservationTool,
    stable_import_id,
)
from vulnclaw.evidence.store import EvidenceStore, utc_now_iso
from vulnclaw.findings.types import finding_input_digest
from vulnclaw.run_context import atomic_write_bytes, atomic_write_json, load_run_context_from_dir

logger = logging.getLogger(__name__)


def legacy_source_namespace(run_id: str, state: Any = None) -> str:
    """Stable source identity for embedded checkpoint evidence."""

    if state is not None:
        started = str(getattr(state, "started_at", "") or "")
        target = str(getattr(state, "target", "") or "")
        task_id = str(getattr(state, "task_id", "") or "main")
        if started or target or task_id:
            digest = hashlib.sha256(
                f"{started}:{target}:{task_id}".encode("utf-8")
            ).hexdigest()[:24]
            return f"checkpoint:{digest}"
    return f"checkpoint:{run_id}"


def migrate_embedded_checkpoint_evidence(
    store: EvidenceStore,
    agent_state: Any,
    *,
    scope: EvidenceScope,
    target: str = "",
    target_id: str = "",
    session: Any = None,
) -> dict[str, str]:
    """Commit every legacy descriptor body once; return ``legacy_id -> new_id``.

    The deterministic UUID5 makes a repeated migration idempotent.  Legacy
    acquisition time is not known: it is recorded as unknown rather than faked
    with the import time.
    """

    source_namespace = legacy_source_namespace(store.run_id)
    id_map: dict[str, str] = {}
    legacy_items = [item for item in agent_state.evidence if getattr(item, "content", "")]
    if not legacy_items:
        return id_map

    for item in legacy_items:
        if str(getattr(item, "tool", "")) == "submit_finding":
            continue
        new_id = stable_import_id(source_namespace, str(item.id))
        captured = CapturedObservation(
            evidence_id=new_id,
            run_id=store.run_id,
            target_id=target_id,
            target=target or str(getattr(item, "target", "") or ""),
            resource=str(getattr(item, "tool", "")) or "legacy-import",
            producer=ObservationProducer(
                task_id=scope.task_id or "main",
                parent_task_id=None,
                session_kind="legacy_import",
            ),
            tool=ObservationTool(
                name=str(getattr(item, "tool", "") or "legacy"),
                arguments=dict(getattr(item, "arguments", {}) or {}),
                tool_call_id="",
                execution_id="",
            ),
            acquisition=ObservationAcquisition(
                started_at=None,
                finished_at=None,
                time_source="unknown",
            ),
            identity=ObservationIdentity(label=None, source="unknown"),
            execution=ObservationExecution(
                outcome="unknown",
                duration_ms=0,
                exit_code=None,
                http_status=int(getattr(item, "status", 0) or 0) or None,
            ),
            completeness=ObservationCompleteness(
                captured_complete=None,
                upstream_truncated=None,
                partial_output=None,
                reason="imported from an embedded checkpoint; original completeness unknown",
            ),
            provenance=ObservationProvenance(
                legacy={
                    "legacy_id": str(item.id),
                    "source": "checkpoint",
                    "source_namespace": source_namespace,
                }
            ),
            representation=REPRESENTATION_TOOL_TEXT,
            text=str(item.content),
            summary=str(getattr(item, "summary", "") or ""),
        )
        outcome = store.commit_observation(
            captured, scope=scope, operation_id=f"legacy_import_{item.id}"
        )
        if outcome.durability != "committed":
            logger.warning("legacy evidence %s remains pending after import", item.id)
            continue
        id_map[str(item.id)] = new_id

    _rewrite_structured_references(agent_state, id_map)
    _write_import_mapping(store, source_namespace, id_map)
    return id_map


def _rewrite_structured_references(agent_state: Any, id_map: dict[str, str]) -> None:
    if not id_map:
        return
    for claim in agent_state.verified_claims:
        claim.evidence_ids = [
            id_map.get(item, item) for item in claim.evidence_ids
        ]
    for fact in agent_state.pinned_facts:
        fact.evidence_id = id_map.get(fact.evidence_id, fact.evidence_id)
    for signal in agent_state.progress_signals:
        signal.evidence_id = id_map.get(signal.evidence_id, signal.evidence_id)
    for tool_call in agent_state.tool_calls:
        tool_call.evidence_id = id_map.get(tool_call.evidence_id, tool_call.evidence_id)
    for item in agent_state.evidence:
        if item.duplicate_of:
            item.duplicate_of = id_map.get(item.duplicate_of, item.duplicate_of)
    for descriptor in agent_state.evidence:
        new_id = id_map.get(str(descriptor.id))
        if new_id:
            descriptor.id = new_id
            descriptor.content = ""
            descriptor.preview = ""


def _write_import_mapping(
    store: EvidenceStore, source_namespace: str, id_map: dict[str, str]
) -> None:
    safe_namespace = source_namespace.replace(":", "-")
    path = store.run_dir / "evidence" / "imports" / f"{safe_namespace}.json"
    entries: dict[str, str] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and isinstance(existing.get("entries"), dict):
                entries.update(existing["entries"])
        except (OSError, ValueError):
            entries = {}
    entries.update(id_map)
    atomic_write_json(
        path,
        {
            "schema_version": 1,
            "run_id": store.run_id,
            "source_namespace": source_namespace,
            "imported_at": utc_now_iso(),
            "entries": dict(sorted(entries.items())),
        },
    )


def import_run_evidence(
    store: EvidenceStore,
    agent_state: Any,
    *,
    scope: EvidenceScope,
    source_run_dir: str | Path,
    source_run_id: str,
) -> int:
    """Carry the restored task's visible evidence into its new run, retaining IDs."""

    source_context = load_run_context_from_dir(
        source_run_dir, expected_run_id=source_run_id, validate=False
    )
    imported = 0
    source_scope = replace(scope, run_id=source_context.run_id)
    watermark = agent_state.checkpoint_commit_seq
    with EvidenceStore(source_context.run_dir, run_id=source_context.run_id, writable=False) as source:
        cursor = None
        while True:
            page = source.list_records(scope=source_scope, cursor=cursor, limit=100)
            for descriptor in page.items:
                if watermark and descriptor.commit_seq > watermark:
                    continue
                observation = source.observation(descriptor.id, scope=source_scope)
                outcome = store.import_observation(
                    observation, source.read_payload(descriptor.id, scope=source_scope), scope=scope
                )
                if outcome.durability == "committed":
                    imported += 1
                for cached in agent_state.evidence:
                    if cached.id == descriptor.id:
                        cached.run_id = store.run_id
                        cached.commit_seq = outcome.descriptor.commit_seq if outcome.descriptor else 0
                        cached.ledger_state = outcome.durability
            cursor = page.next_cursor
            if not cursor:
                break
    return imported


def migrate_finding_snapshots(
    run_dir: str | Path, session: Any, *, source_run_dir: str | Path | None = None
) -> int:
    """Copy absolute/foreign finding snapshots into this run once.

    The payload bytes, sha256, source id and target are preserved exactly.  Only
    the physical location becomes run-relative so a later directory move keeps
    the dependency closed inside the run.
    """

    root = Path(run_dir).expanduser().resolve()
    evidence_root = (root / "evidence").resolve()
    copied = 0
    for finding in list(getattr(session, "findings", []) or []):
        assessment = finding.assessment
        assessment_current = (
            assessment is not None and assessment.input_digest == finding_input_digest(finding)
        )
        changed = False
        for ref in list(getattr(finding, "evidence_snapshots", []) or []):
            if str(getattr(ref, "root", "")) == "evidence":
                if source_run_dir is None:
                    continue
                source_root = (Path(source_run_dir) / "evidence").resolve()
            else:
                source_root = Path(str(ref.root or "")).expanduser().resolve()
            relative = str(ref.path or "")
            source = (source_root / relative).resolve()
            if not source.is_relative_to(source_root):
                continue
            if not source.is_file():
                continue
            try:
                payload = source.read_bytes()
                parsed = json.loads(payload.decode("utf-8"))
            except (OSError, ValueError):
                continue
            digest = hashlib.sha256(
                json.dumps(parsed, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            if digest != str(getattr(ref, "sha256", "")):
                # Integrity already failed; leave it for the assessment path to
                # report rather than rewriting a tampered snapshot.
                continue
            if relative.startswith("findings/") and ".." not in relative.split("/"):
                destination_relative = relative
            else:
                destination_relative = (
                    f"findings/imported/{digest[:12]}/{Path(relative).name or 'snapshot.json'}"
                )
            destination = (evidence_root / destination_relative).resolve()
            if not destination.is_relative_to(evidence_root):
                continue
            if not destination.exists() or destination.read_bytes() != payload:
                atomic_write_bytes(destination, payload)
            ref.root = "evidence"
            ref.path = destination_relative
            changed = True
            copied += 1
        if changed:
            metadata = getattr(finding, "finding_meta", None)
            if isinstance(metadata, dict):
                metadata["snapshot_imported_into_run"] = True
            if assessment_current:
                assessment.input_digest = finding_input_digest(finding)
    return copied
