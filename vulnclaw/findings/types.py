"""Finding evidence and assessment records owned by the application."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvidenceSnapshotRef(BaseModel):
    evidence_id: str
    source_id: str
    target: str
    root: str
    path: str
    sha256: str
    fingerprint: str
    task_id: str = ""


def merge_snapshot_refs(refs: list[EvidenceSnapshotRef]) -> list[EvidenceSnapshotRef]:
    """Keep distinct snapshots addressable across session-local evidence IDs."""
    result = []
    hashes: set[str] = set()
    used_ids: set[str] = set()
    reserved_ids = {ref.evidence_id for ref in refs}
    for ref in refs:
        if ref.sha256 in hashes:
            continue
        if ref.evidence_id in used_ids:
            alias = f"{ref.source_id}:{ref.sha256}"
            evidence_id = alias
            suffix = 2
            while evidence_id in reserved_ids or evidence_id in used_ids:
                evidence_id = f"{alias}:{suffix}"
                suffix += 1
            ref = ref.model_copy(update={"evidence_id": evidence_id})
        result.append(ref)
        hashes.add(ref.sha256)
        used_ids.add(ref.evidence_id)
    return result


class FindingAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["verified", "rejected", "suspected_high", "suspected_low"]
    method: Literal["poc", "evidence_review"]
    complete: bool = True
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    unresolved_evidence_ids: list[str] = Field(
        default_factory=list,
        description="Historical IDs with ambiguous or missing provenance; do not resolve these IDs against current snapshots",
    )
    observations: list[dict[str, str]] = Field(default_factory=list)
    missing_checks: list[str] = Field(default_factory=list)
    failure_reason: str = ""
    artifacts: list[str] = Field(default_factory=list)
    assessed_at: str = Field(default_factory=timestamp)


def remap_assessment_evidence(
    assessment: FindingAssessment,
    previous: list[EvidenceSnapshotRef],
    current: list[EvidenceSnapshotRef],
) -> FindingAssessment:
    """Rebind citations by snapshot identity, never by a reused local ID."""
    hashes: dict[str, set[str]] = {}
    for ref in previous:
        hashes.setdefault(ref.evidence_id, set()).add(ref.sha256)
    by_hash = {ref.sha256: ref.evidence_id for ref in current}
    unresolved = list(assessment.unresolved_evidence_ids)
    mapping = {}
    cited = dict.fromkeys([
        *assessment.evidence_ids,
        *(o["evidence_id"] for o in assessment.observations if "evidence_id" in o),
    ])
    for key in cited:
        if key in unresolved:
            continue
        candidates = hashes.get(key, set())
        if len(candidates) == 1 and next(iter(candidates)) in by_hash:
            mapping[key] = by_hash[next(iter(candidates))]
        else:
            # Old merged histories lack enough provenance to disambiguate an ID.
            # Preserve their recorded claim and explicitly flag it as unresolved.
            unresolved.append(key)
    return assessment.model_copy(update={
        "evidence_ids": list(dict.fromkeys(mapping.get(key, key) for key in assessment.evidence_ids)),
        "observations": [
            {**o, "evidence_id": mapping.get(o["evidence_id"], o["evidence_id"])}
            if "evidence_id" in o else dict(o) for o in assessment.observations
        ],
        "unresolved_evidence_ids": unresolved,
    })


def replace_snapshot_refs(finding: Any, refs: list[EvidenceSnapshotRef]) -> None:
    """Update aliases and all attached citations together without rewriting snapshots."""
    previous = finding.evidence_snapshots
    refs = merge_snapshot_refs(refs)
    finding.verification_history = [
        remap_assessment_evidence(a, previous, refs) for a in finding.verification_history
    ]
    if finding.report_assessment is not None:
        finding.report_assessment = remap_assessment_evidence(finding.report_assessment, previous, refs)
    by_hash = {ref.sha256: ref for ref in refs}
    lines = {}
    for ref in previous:
        if ref.sha256 in by_hash:
            lines[f"[{ref.evidence_id}] saved tool evidence: {ref.path}"] = (
                f"[{by_hash[ref.sha256].evidence_id}] saved tool evidence: {ref.path}"
            )
    if lines:
        parts = []
        for line in finding.evidence.splitlines(keepends=True):
            content = line.rstrip("\r\n")
            parts.append(lines.get(content, content) + line[len(content):])
        finding.evidence = "".join(parts)
    finding.evidence_snapshots = refs


class EvidenceReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confidence: Literal["high", "low"]
    reason: str = Field(min_length=1, max_length=4000)
    observations: list[dict[str, str]] = Field(default_factory=list, max_length=20)
    missing_checks: list[str] = Field(default_factory=list, max_length=20)


class FindingSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=300)
    vuln_type: str = Field(min_length=1, max_length=100)
    severity: Literal["Critical", "High", "Medium", "Low", "Info"]
    description: str = Field(min_length=1, max_length=12000)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    endpoint: str | None = Field(default=None, max_length=2048)
    method: str | None = Field(default=None, max_length=20)
    code_location: str | None = Field(default=None, max_length=4096)
    impact: str = Field(default="", max_length=6000)
    remediation: str = Field(default="", max_length=6000)
    cve: str | None = Field(default=None, max_length=100)
    cwe: str | None = Field(default=None, max_length=100)
    cvss: float | None = Field(default=None, ge=0, le=10, allow_inf_nan=False)


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    target: str
    task_id: str = ""
    tool: str
    arguments: dict[str, Any]
    content: str
    fingerprint: str
