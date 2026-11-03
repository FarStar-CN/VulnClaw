"""Evidence persistence data contracts.

The store persists one immutable record per observation plus the exact captured
payload.  These models are the shared vocabulary for the tool layer, agent
state, sub-agent projection and importers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

EVIDENCE_SCHEMA_VERSION = 1
EVIDENCE_ID_PREFIX = "ev_"
EVIDENCE_ID_PATTERN = re.compile(r"^ev_[0-9a-f]{32}$")
LEGACY_EVIDENCE_ID_PATTERN = re.compile(r"^e\d{3,}$")
EVIDENCE_REFERENCE_RE = re.compile(r"\b(ev_[0-9a-f]{32}|e\d{3,})\b", re.IGNORECASE)

REPRESENTATION_TOOL_TEXT = "tool-text-v1"
REPRESENTATION_TOOL_JSON = "tool-json-v1"
REPRESENTATION_BINARY = "tool-binary-v1"

RECORD_KIND_OBSERVATION = "observation"
RECORD_KIND_DERIVED = "derived"

DURABILITY_COMMITTED = "committed"
DURABILITY_PENDING = "pending"
DURABILITY_UNAVAILABLE = "unavailable"

OUTCOME_SUCCEEDED = "succeeded"
OUTCOME_FAILED = "failed"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_CANCELLED = "cancelled"
OUTCOME_UNKNOWN = "unknown"
EXECUTION_OUTCOMES = frozenset(
    {OUTCOME_SUCCEEDED, OUTCOME_FAILED, OUTCOME_TIMEOUT, OUTCOME_CANCELLED, OUTCOME_UNKNOWN}
)


class EvidenceError(RuntimeError):
    """Base class for evidence-store failures."""


class EvidenceArgumentError(EvidenceError, ValueError):
    """A caller supplied an invalid id, range, cursor or filter."""


class EvidenceNotFoundError(EvidenceError):
    """No committed or pending observation matches the id."""


class EvidenceScopeError(EvidenceError):
    """The observation exists but is not visible to this scope."""


class EvidenceUnavailableError(EvidenceError):
    """The record exists but its payload cannot be used."""


class EvidenceIdConflictError(EvidenceError):
    """A different observation already committed under the same id."""


class EvidencePersistenceError(EvidenceError):
    """A payload or record could not be committed."""

    def __init__(self, stage: str, message: str, *, retryable: bool = False) -> None:
        self.stage = stage
        self.retryable = retryable
        super().__init__(f"{stage}: {message}")


class PersistenceErrorLimitExceeded(EvidenceError):
    """The run's cumulative save-failure budget was exhausted."""

    def __init__(
        self,
        *,
        failure_total: int,
        limit: int,
        stage: str,
        last_error: str,
        unsaved_ids: list[str] | None = None,
    ) -> None:
        self.failure_total = failure_total
        self.limit = limit
        self.stage = stage
        self.last_error = last_error
        self.unsaved_ids = list(unsaved_ids or [])
        super().__init__(
            f"evidence persistence stopped after {failure_total} failed saves "
            f"(limit {limit}, stage {stage}): {last_error}"
        )


def new_evidence_id() -> str:
    from uuid import uuid4

    return f"{EVIDENCE_ID_PREFIX}{uuid4().hex}"


def stable_import_id(source_namespace: str, legacy_id: str) -> str:
    """Deterministic id for one legacy observation from one fixed source."""

    from uuid import NAMESPACE_URL, uuid5

    return f"{EVIDENCE_ID_PREFIX}{uuid5(NAMESPACE_URL, f'{source_namespace}:{legacy_id}').hex}"


def is_evidence_id(value: str) -> bool:
    return bool(EVIDENCE_ID_PATTERN.fullmatch(str(value or "")))


class ObservationProducer(BaseModel):
    task_id: str = "main"
    parent_task_id: str | None = None
    session_kind: str = "parent"


class ObservationTool(BaseModel):
    name: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    tool_call_id: str = ""
    execution_id: str = ""


class ObservationAcquisition(BaseModel):
    started_at: str | None = None
    finished_at: str | None = None
    time_source: str = "unknown"


class ObservationIdentity(BaseModel):
    label: str | None = None
    source: str = "unknown"


class ObservationExecution(BaseModel):
    outcome: str = OUTCOME_UNKNOWN
    duration_ms: int = 0
    exit_code: int | None = None
    http_status: int | None = None

    @field_validator("outcome")
    @classmethod
    def _validate_outcome(cls, value: str) -> str:
        return value if value in EXECUTION_OUTCOMES else OUTCOME_UNKNOWN


class ObservationPayload(BaseModel):
    representation: str = REPRESENTATION_TOOL_TEXT
    path: str = ""
    size_bytes: int = 0
    size_chars: int | None = 0


class ObservationCompleteness(BaseModel):
    captured_complete: bool | None = None
    upstream_truncated: bool | None = None
    partial_output: bool | None = None
    reason: str | None = None


class ObservationArtifact(BaseModel):
    kind: str = ""
    path: str = ""
    size_bytes: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservationProvenance(BaseModel):
    source_evidence_ids: list[str] = Field(default_factory=list)
    legacy: dict[str, Any] | None = None
    origin_run_id: str = ""
    imported_from_run_id: str = ""


class EvidenceObservation(BaseModel):
    """Immutable observation record stored as ``evidence/records/<id>.json``."""

    model_config = ConfigDict(populate_by_name=True)

    schema_version: int = EVIDENCE_SCHEMA_VERSION
    id: str
    run_id: str
    commit_seq: int = 0
    record_kind: Literal["observation", "derived"] = RECORD_KIND_OBSERVATION
    target_id: str = ""
    target: str = ""
    resource: str = ""
    producer: ObservationProducer = Field(default_factory=ObservationProducer)
    tool: ObservationTool = Field(default_factory=ObservationTool)
    acquisition: ObservationAcquisition = Field(default_factory=ObservationAcquisition)
    identity: ObservationIdentity = Field(default_factory=ObservationIdentity)
    execution: ObservationExecution = Field(default_factory=ObservationExecution)
    payload: ObservationPayload = Field(default_factory=ObservationPayload)
    completeness: ObservationCompleteness = Field(default_factory=ObservationCompleteness)
    artifacts: list[ObservationArtifact] = Field(default_factory=list)
    provenance: ObservationProvenance = Field(default_factory=ObservationProvenance)
    committed_at: str = ""

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not is_evidence_id(value):
            raise ValueError("evidence id must be ev_ plus 32 lowercase hex characters")
        return value

    @field_validator("payload")
    @classmethod
    def _validate_payload_path(cls, value: ObservationPayload) -> ObservationPayload:
        if value.path:
            relative = value.path.replace("\\", "/")
            if relative.startswith("/") or ".." in relative.split("/"):
                raise ValueError("payload path must stay inside the run")
            value.path = relative
        return value


@dataclass
class CapturedObservation:
    """Frozen tool result ready for one or more save attempts.

    Rewriting or mutating this object between retries is forbidden: the identity,
    acquisition conditions and payload bytes must stay identical.
    """

    evidence_id: str
    run_id: str
    target_id: str = ""
    target: str = ""
    resource: str = ""
    producer: ObservationProducer = field(default_factory=ObservationProducer)
    tool: ObservationTool = field(default_factory=ObservationTool)
    acquisition: ObservationAcquisition = field(default_factory=ObservationAcquisition)
    identity: ObservationIdentity = field(default_factory=ObservationIdentity)
    execution: ObservationExecution = field(default_factory=ObservationExecution)
    completeness: ObservationCompleteness = field(default_factory=ObservationCompleteness)
    artifacts: list[ObservationArtifact] = field(default_factory=list)
    provenance: ObservationProvenance = field(default_factory=ObservationProvenance)
    record_kind: str = RECORD_KIND_OBSERVATION
    representation: str = REPRESENTATION_TOOL_TEXT
    text: str | None = None
    data: bytes | None = None
    summary: str = ""
    preview: str = ""


class EvidenceDescriptor(BaseModel):
    """Bounded metadata kept in AgentState and returned by list/describe."""

    model_config = ConfigDict(extra="ignore")

    id: str
    run_id: str = ""
    commit_seq: int = 0
    record_kind: str = RECORD_KIND_OBSERVATION
    tool: str = ""
    target: str = ""
    target_id: str = ""
    resource: str = ""
    producer_task_id: str = "main"
    identity_label: str | None = None
    outcome: str = OUTCOME_UNKNOWN
    status: int = 0
    started_at: str = ""
    finished_at: str = ""
    representation: str = REPRESENTATION_TOOL_TEXT
    size_chars: int = 0
    size_bytes: int = 0
    captured_complete: bool | None = None
    summary: str = ""
    ledger_state: str = DURABILITY_COMMITTED
    # Legacy fields retained so old checkpoints can be read and migrated in memory.
    arguments: dict[str, Any] = Field(default_factory=dict)
    key_args: str = ""
    preview: str = Field(default="", exclude=True)
    truncated: bool = False
    created_at: str = ""
    duplicate_of: str = ""
    content_hash: str = ""
    fingerprint: str = ""
    content: str = Field(default="", exclude=True)

    @property
    def size(self) -> int:
        return int(self.size_chars or self.size_bytes or 0)

    @property
    def payload_path_hint(self) -> str:
        return ""


class SaveStatus(str):
    COMMITTED = "committed"
    PENDING = "pending"
    INDEX_DIRTY = "index_dirty"


class SaveOutcome(BaseModel):
    evidence_id: str
    status: str
    durability: str
    descriptor: EvidenceDescriptor | None = None
    preview: str = ""
    index_dirty: bool = False
    error: str = ""


class ViewResult(BaseModel):
    descriptor: EvidenceDescriptor
    representation: str
    text: str = ""
    start: int = 0
    end: int = 0
    total_chars: int = 0
    size_bytes: int = 0
    next_offset: int | None = None
    eof: bool = False
    durability: str = DURABILITY_COMMITTED
    preview_truncated: bool = False
    returned_truncated: bool = False


class ListResult(BaseModel):
    items: list[EvidenceDescriptor] = Field(default_factory=list)
    next_cursor: str = ""
    truncated: bool = False
    returned_count: int = 0
    candidate_total: int | None = None
    pending_count: int = 0
    scope_description: str = ""
    watermark: int = 0


class SearchHit(BaseModel):
    evidence_id: str
    representation: str
    match_start: int
    match_end: int
    snippet_start: int
    snippet_end: int
    text: str
    tool: str = ""
    target: str = ""
    outcome: str = OUTCOME_UNKNOWN
    durability: str = DURABILITY_COMMITTED
    match_kind: str = "body"
    field: str = ""


class SearchResult(BaseModel):
    query: str = ""
    hits: list[SearchHit] = Field(default_factory=list)
    metadata_matches: list[SearchHit] = Field(default_factory=list)
    scan_complete: bool = True
    unsearched_ids: list[str] = Field(default_factory=list)
    skipped_reasons: dict[str, str] = Field(default_factory=dict)
    scanned_records: int = 0
    scanned_chars: int = 0
    returned_count: int = 0
    next_cursor: str = ""
    pending_count: int = 0
    watermark: int = 0


class RebuildResult(BaseModel):
    total_records: int = 0
    available: int = 0
    unavailable_ids: list[str] = Field(default_factory=list)
    max_commit_seq: int = 0
    index_written: bool = False
    error: str = ""


class FlushResult(BaseModel):
    attempted: int = 0
    committed_ids: list[str] = Field(default_factory=list)
    failed_ids: list[str] = Field(default_factory=list)
    outcomes: list[SaveOutcome] = Field(default_factory=list)


class PersistenceHealth(BaseModel):
    schema_version: int = 1
    run_id: str = ""
    failure_total: int = 0
    failures_by_stage: dict[str, int] = Field(default_factory=dict)
    index_write_failures: int = 0
    last_error: dict[str, Any] | None = None
    pending_ids: list[str] = Field(default_factory=list)
    counter_checkpoint_unsaved: bool = False


class EvidenceFilters(BaseModel):
    evidence_ids: list[str] = Field(default_factory=list)
    target_id: str = ""
    target: str = ""
    resource: str = ""
    tool: str = ""
    producer_task_id: str = ""
    identity_label: str = ""
    since: str = ""
    until: str = ""
    outcome: str = ""
    record_kind: str = ""

    @field_validator("evidence_ids")
    @classmethod
    def _validate_ids(cls, values: list[str]) -> list[str]:
        for value in values:
            if not (is_evidence_id(value) or LEGACY_EVIDENCE_ID_PATTERN.fullmatch(value)):
                raise ValueError(f"invalid evidence id: {value}")
        return values


@dataclass(frozen=True)
class EvidenceScope:
    """Framework-provided visibility scope.

    Tool arguments can narrow this scope but can never widen it.  ``read_all`` is
    reserved for administrative reads (reports, independent verification) that
    were explicitly given a run, not for Agent tool calls.
    """

    run_id: str
    task_id: str = "main"
    target_id: str = ""
    inherited_evidence_ids: frozenset[str] = frozenset()
    published_task_ids: frozenset[str] = frozenset()
    read_all: bool = False

    def can_read(self, observation: EvidenceObservation) -> bool:
        if observation.run_id != self.run_id:
            return False
        if self.read_all:
            return True
        producer = observation.producer.task_id or "main"
        if producer == self.task_id:
            return True
        if observation.id in self.inherited_evidence_ids:
            return True
        return producer in self.published_task_ids

    def describe(self) -> str:
        tasks = sorted({self.task_id, *self.published_task_ids})
        return f"run={self.run_id} task={self.task_id} visible_tasks={','.join(tasks)}"
