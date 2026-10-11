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


class FindingAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["verified", "rejected", "suspected_high", "suspected_low"]
    method: Literal["poc", "evidence_review"]
    complete: bool = True
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    observations: list[dict[str, str]] = Field(default_factory=list)
    missing_checks: list[str] = Field(default_factory=list)
    failure_reason: str = ""
    artifacts: list[str] = Field(default_factory=list)
    assessed_at: str = Field(default_factory=timestamp)


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
