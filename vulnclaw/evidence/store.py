"""Per-run evidence store.

Commit protocol (see ``docs/architecture/evidence-persistence-implementation-guide.md``):

1. write the frozen payload to ``evidence/payloads/<id>`` atomically;
2. allocate ``commit_seq`` and atomically write ``evidence/records/<id>.json``;
3. update the in-process catalog immediately; the derived ``index.json`` is
   refreshed at a batch/step boundary.

The record is the commit point.  An orphan payload without a record is not a
usable observation and never appears in list/search/view.
"""

from __future__ import annotations

import asyncio
import base64
import errno
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from vulnclaw.evidence.models import (
    DURABILITY_COMMITTED,
    DURABILITY_PENDING,
    DURABILITY_UNAVAILABLE,
    EVIDENCE_SCHEMA_VERSION,
    LEGACY_EVIDENCE_ID_PATTERN,
    REPRESENTATION_BINARY,
    REPRESENTATION_TOOL_TEXT,
    CapturedObservation,
    EvidenceArgumentError,
    EvidenceDescriptor,
    EvidenceFilters,
    EvidenceIdConflictError,
    EvidenceNotFoundError,
    EvidenceObservation,
    EvidencePersistenceError,
    EvidenceScope,
    EvidenceScopeError,
    EvidenceUnavailableError,
    FlushResult,
    ListResult,
    ObservationPayload,
    PersistenceErrorLimitExceeded,
    PersistenceHealth,
    RebuildResult,
    SaveOutcome,
    SearchHit,
    SearchResult,
    ViewResult,
    is_evidence_id,
)
from vulnclaw.run_context import atomic_write_bytes, atomic_write_json

logger = logging.getLogger(__name__)

EVIDENCE_INDEX_SCHEMA_VERSION = 1
DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 100
DEFAULT_SEARCH_LIMIT = 12
MAX_SEARCH_LIMIT = 50
DEFAULT_SEARCH_CONTEXT_CHARS = 180
MAX_SEARCH_CONTEXT_CHARS = 2000
DEFAULT_VIEW_LIMIT = 12_000
MAX_VIEW_LIMIT = 24_000
SEARCH_SCAN_CHAR_BUDGET = 2_000_000
REGEX_RECORD_CHAR_LIMIT = 256_000
LITERAL_CHUNK_CHARS = 1_000_000
SAVE_ATTEMPTS = 3
SAVE_RETRY_DELAYS = (0.1, 0.3)
NON_RETRYABLE_ERRNOS = frozenset(
    {
        errno.ENOSPC,
        errno.EACCES,
        errno.EPERM,
        errno.EROFS,
        errno.EISDIR,
        errno.ENOTDIR,
        errno.ENAMETOOLONG,
    }
)
_MAX_CURSOR_BYTES = 4096


class EvidenceWriterBusyError(EvidencePersistenceError):
    """Another process already owns the writable side of this run."""

    def __init__(self, run_dir: Path) -> None:
        super().__init__("ownership", f"another writer owns the run at {run_dir}")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, OSError):
        return exc.errno not in NON_RETRYABLE_ERRNOS
    return False


def serialize_tool_output(value: Any) -> tuple[str | None, bytes | None, str]:
    """Freeze a tool return into an exact representation.

    ``None``, ``""`` and ``0`` stay distinct: strings use ``tool-text-v1``, all
    other JSON values use ``tool-json-v1``, and bytes use ``tool-binary-v1``.
    """

    if isinstance(value, str):
        return value, None, REPRESENTATION_TOOL_TEXT
    if isinstance(value, (bytes, bytearray, memoryview)):
        return None, bytes(value), REPRESENTATION_BINARY
    return json.dumps(value, ensure_ascii=False, sort_keys=True), None, "tool-json-v1"


class EvidenceStore:
    """One run's immutable observation store plus mutable health state."""

    def __init__(
        self,
        run_dir: str | Path,
        *,
        run_id: str,
        error_limit: int = 5,
        writable: bool = True,
        restored_health: SerializedHealth | None = None,
        retry_delays: tuple[float, ...] = SAVE_RETRY_DELAYS,
    ) -> None:
        if not str(run_id or "").strip():
            raise ValueError("evidence store requires a non-empty run_id")
        self.run_dir = Path(run_dir).expanduser().resolve()
        self.run_id = str(run_id)
        self.error_limit = max(0, int(error_limit))
        self.writable = bool(writable)
        self._retry_delays = tuple(float(delay) for delay in retry_delays)
        self._lock = threading.RLock()
        self.records_dir = self.run_dir / "evidence" / "records"
        self.payloads_dir = self.run_dir / "evidence" / "payloads"
        self.imports_dir = self.run_dir / "evidence" / "imports"
        self.index_path = self.run_dir / "evidence" / "index.json"
        self.health_path = self.run_dir / "evidence" / "health.json"
        self._records: dict[str, EvidenceObservation] = {}
        self._order: list[str] = []
        self._invalid_records: dict[str, str] = {}
        self._max_seq = 0
        self._pending: dict[str, CapturedObservation] = {}
        self._index_dirty = True
        self._closed = False
        self._writer_lock = _WriterLock(self.run_dir / ".evidence-writer.lock")
        if self.writable:
            self._writer_lock.acquire(self.run_dir)
        try:
            self.health = self._load_health()
            if restored_health is not None:
                self.adopt_health_mirror(restored_health)
            if self.writable:
                self.check_writable_execution_allowed()
                for directory in (self.records_dir, self.payloads_dir, self.imports_dir):
                    directory.mkdir(parents=True, exist_ok=True)
            self._scan_records()
        except BaseException:
            self._writer_lock.release()
            raise

    # ── lifecycle ──────────────────────────────────────────────────────
    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._writer_lock.release()

    def __enter__(self) -> "EvidenceStore":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    # ── health and failure budget ──────────────────────────────────────
    def _load_health(self) -> PersistenceHealth:
        health = PersistenceHealth(run_id=self.run_id)
        try:
            raw = json.loads(self.health_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return health
        if not isinstance(raw, dict):
            return health
        try:
            loaded = PersistenceHealth.model_validate(raw)
        except Exception:
            return health
        if loaded.run_id and loaded.run_id != self.run_id:
            return health
        loaded.run_id = self.run_id
        return loaded

    def adopt_health_mirror(self, mirror: Any) -> None:
        """Adopt a checkpoint mirror only when it records a larger failure total."""

        if mirror is None:
            return
        if isinstance(mirror, PersistenceHealth):
            incoming = mirror.model_copy(deep=True)
        elif isinstance(mirror, dict):
            try:
                incoming = PersistenceHealth.model_validate(mirror)
            except Exception:
                return
        else:
            return
        if incoming.run_id and incoming.run_id != self.run_id:
            return
        with self._lock:
            if incoming.failure_total > self.health.failure_total:
                stages = dict(incoming.failures_by_stage)
                if sum(stages.values()) > incoming.failure_total:
                    # Never invent a breakdown that exceeds the recovered total.
                    stages = {}
                self.health = PersistenceHealth(
                    run_id=self.run_id,
                    failure_total=incoming.failure_total,
                    failures_by_stage=stages,
                    index_write_failures=max(
                        incoming.index_write_failures, self.health.index_write_failures
                    ),
                    last_error=incoming.last_error or self.health.last_error,
                    pending_ids=list(incoming.pending_ids),
                    counter_checkpoint_unsaved=bool(incoming.counter_checkpoint_unsaved),
                )
            elif incoming.failure_total == self.health.failure_total:
                if incoming.failures_by_stage and not self.health.failures_by_stage:
                    self.health.failures_by_stage = dict(incoming.failures_by_stage)

    def health_snapshot(self) -> dict[str, Any]:
        with self._lock:
            self.health.pending_ids = sorted(self._pending)
            return self.health.model_dump(mode="json")

    def check_writable_execution_allowed(self) -> None:
        if not self.writable:
            raise EvidencePersistenceError("ownership", "evidence store is read-only")
        if self.health.failure_total > self.error_limit:
            last = self.health.last_error or {}
            raise PersistenceErrorLimitExceeded(
                failure_total=self.health.failure_total,
                limit=self.error_limit,
                stage=str(last.get("stage") or "unknown"),
                last_error=str(last.get("message") or "evidence persistence limit reached"),
                unsaved_ids=sorted(self._pending),
            )

    def _persist_health(self) -> None:
        try:
            atomic_write_json(self.health_path, self.health.model_dump(mode="json"))
            self.health.counter_checkpoint_unsaved = False
        except Exception as exc:  # never recurse into another failure count
            self.health.counter_checkpoint_unsaved = True
            logger.error(
                "evidence health mirror write failed run_id=%s error=%s",
                self.run_id,
                type(exc).__name__,
            )

    def _record_failure(self, stage: str, operation_id: str, exc: BaseException) -> None:
        with self._lock:
            self.health.failure_total += 1
            self.health.failures_by_stage[stage] = (
                int(self.health.failures_by_stage.get(stage, 0)) + 1
            )
            self.health.last_error = {
                "stage": stage,
                "operation_id": operation_id,
                "error_type": type(exc).__name__,
                "message": str(exc)[:1000],
                "at": utc_now_iso(),
            }
            self.health.pending_ids = sorted(self._pending)
            total = self.health.failure_total
            self._persist_health()
        logger.warning(
            "evidence save failed run_id=%s operation_id=%s stage=%s error=%s "
            "failure_total=%s limit=%s recoverable=%s",
            self.run_id,
            operation_id,
            stage,
            type(exc).__name__,
            total,
            self.error_limit,
            True,
        )
        if total > self.error_limit:
            raise PersistenceErrorLimitExceeded(
                failure_total=total,
                limit=self.error_limit,
                stage=stage,
                last_error=str(exc),
                unsaved_ids=sorted(self._pending),
            )

    def save_checkpoint(
        self, save: Callable[[], None], *, operation_id: str, required: bool = False
    ) -> bool:
        """Retry only the state write; count one failure after attempts are exhausted."""

        self.check_writable_execution_allowed()
        for attempt in range(SAVE_ATTEMPTS):
            try:
                save()
                return True
            except (OSError, ValueError, TypeError) as exc:
                if _is_transient(exc) and attempt < SAVE_ATTEMPTS - 1:
                    time.sleep(self._retry_delays[min(attempt, len(self._retry_delays) - 1)])
                    continue
                self._record_failure("context_state", operation_id, exc)
                if required:
                    raise
                return False
        return False

    # ── catalog loading ────────────────────────────────────────────────
    def _scan_records(self) -> None:
        records: dict[str, EvidenceObservation] = {}
        order: list[tuple[int, str]] = []
        invalid: dict[str, str] = {}
        for path in sorted(self.records_dir.glob("*.json")):
            if path.name.startswith("."):
                continue
            observation = self._read_record_file(path)
            if observation is None:
                if is_evidence_id(path.stem):
                    invalid[path.stem] = "record is unreadable or structurally invalid"
                continue
            records[observation.id] = observation
            order.append((observation.commit_seq, observation.id))
        with self._lock:
            self._records = records
            self._order = [item_id for _, item_id in sorted(order)]
            self._invalid_records = invalid
            self._max_seq = max((seq for seq, _ in order), default=0)

    def _read_record_file(self, path: Path) -> EvidenceObservation | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            observation = EvidenceObservation.model_validate(raw)
        except Exception as exc:
            logger.warning("ignoring unreadable evidence record %s: %s", path, exc)
            return None
        if observation.run_id != self.run_id:
            logger.warning(
                "ignoring evidence record %s from another run (%s)", path, observation.run_id
            )
            return None
        if path.stem != observation.id:
            logger.warning("ignoring evidence record with mismatched filename %s", path)
            return None
        return observation

    @property
    def max_commit_seq(self) -> int:
        with self._lock:
            return self._max_seq

    @property
    def committed_count(self) -> int:
        with self._lock:
            return len(self._records)

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    # ── commit ─────────────────────────────────────────────────────────
    def commit_observation(
        self,
        captured: CapturedObservation,
        *,
        scope: EvidenceScope,
        operation_id: str = "",
    ) -> SaveOutcome:
        self._ensure_writable()
        self._validate_captured(captured, scope)
        self.check_writable_execution_allowed()
        operation = operation_id or f"save_{captured.evidence_id}"

        existing = self._records.get(captured.evidence_id)
        if existing is not None:
            self._verify_idempotent(existing, captured)
            return self._outcome_from_observation(existing, index_dirty=self._index_dirty, preview=captured.preview)
        pending_existing = self._pending.get(captured.evidence_id)
        if pending_existing is not None:
            self._verify_captured_equal(pending_existing, captured)
            return SaveOutcome(
                evidence_id=captured.evidence_id,
                status=DURABILITY_PENDING,
                durability=DURABILITY_PENDING,
                preview=captured.preview,
            )
        if not self._scope_can_read_captured(captured, scope):
            raise EvidenceScopeError("captured observation is not visible in this scope")

        try:
            return self._attempt_commit(captured)
        except EvidencePersistenceError as exc:
            with self._lock:
                self._pending[captured.evidence_id] = captured
                self.health.pending_ids = sorted(self._pending)
            self._record_failure(exc.stage, operation, exc)
            return SaveOutcome(
                evidence_id=captured.evidence_id,
                status=DURABILITY_PENDING,
                durability=DURABILITY_PENDING,
                preview=captured.preview,
                error=str(exc),
            )

    async def commit_observation_async(
        self,
        captured: CapturedObservation,
        *,
        scope: EvidenceScope,
        operation_id: str = "",
    ) -> SaveOutcome:
        """Run the blocking save protocol off the event loop."""

        return await asyncio.to_thread(
            self.commit_observation,
            captured,
            scope=scope,
            operation_id=operation_id,
        )

    def _attempt_commit(self, captured: CapturedObservation) -> SaveOutcome:
        last_error: EvidencePersistenceError | None = None
        for attempt in range(SAVE_ATTEMPTS):
            try:
                return self._commit_once(captured)
            except EvidencePersistenceError as exc:
                last_error = exc
                if not exc.retryable or attempt >= SAVE_ATTEMPTS - 1:
                    break
                time.sleep(self._retry_delays[min(attempt, len(self._retry_delays) - 1)])
        assert last_error is not None
        raise last_error

    def _commit_once(self, captured: CapturedObservation) -> SaveOutcome:
        payload_path, payload_bytes, payload_chars = self._payload_target(captured)
        try:
            self._write_payload(payload_path, payload_bytes)
        except OSError as exc:
            raise EvidencePersistenceError("payload", str(exc), retryable=_is_transient(exc)) from exc

        with self._lock:
            existing = self._records.get(captured.evidence_id)
            if existing is not None:
                self._verify_idempotent(existing, captured)
                return self._outcome_from_observation(
                    existing, index_dirty=self._index_dirty, preview=captured.preview
                )
            observation = self._build_observation(
                captured, payload_path, len(payload_bytes), payload_chars
            )
            try:
                atomic_write_json(self.records_dir / f"{observation.id}.json", observation.model_dump(mode="json"))
            except OSError as exc:
                raise EvidencePersistenceError("record", str(exc), retryable=_is_transient(exc)) from exc
            self._records[observation.id] = observation
            self._order.append(observation.id)
            self._max_seq = max(self._max_seq, observation.commit_seq)
            self._pending.pop(observation.id, None)
            self._index_dirty = True
            self.health.pending_ids = sorted(self._pending)
        return self._outcome_from_observation(
            observation, index_dirty=True, preview=captured.preview
        )

    def _payload_target(self, captured: CapturedObservation) -> tuple[Path, bytes, int | None]:
        if captured.representation == REPRESENTATION_BINARY:
            if captured.data is None:
                raise EvidenceArgumentError("binary observation requires bytes payload")
            suffix = ".bin"
            data = captured.data
            chars = None
        else:
            if captured.text is None:
                raise EvidenceArgumentError("text observation requires text payload")
            suffix = ".txt"
            data = captured.text.encode("utf-8")
            chars = len(captured.text)
        return self.payloads_dir / f"{captured.evidence_id}{suffix}", data, chars

    def _write_payload(self, path: Path, data: bytes) -> None:
        if path.exists():
            try:
                if path.read_bytes() == data:
                    return
            except OSError:
                pass
        atomic_write_bytes(path, data)

    def _build_observation(
        self,
        captured: CapturedObservation,
        payload_path: Path,
        size_bytes: int,
        size_chars: int | None,
    ) -> EvidenceObservation:
        relative = payload_path.relative_to(self.run_dir).as_posix()
        return EvidenceObservation(
            schema_version=EVIDENCE_SCHEMA_VERSION,
            id=captured.evidence_id,
            run_id=self.run_id,
            commit_seq=self._max_seq + 1,
            record_kind=captured.record_kind,
            target_id=captured.target_id,
            target=captured.target,
            resource=captured.resource,
            producer=captured.producer,
            tool=captured.tool,
            acquisition=captured.acquisition,
            identity=captured.identity,
            execution=captured.execution,
            payload=ObservationPayload(
                representation=captured.representation,
                path=relative,
                size_bytes=size_bytes,
                size_chars=size_chars,
            ),
            completeness=captured.completeness,
            artifacts=list(captured.artifacts),
            provenance=captured.provenance,
            committed_at=utc_now_iso(),
        )

    def _validate_captured(self, captured: CapturedObservation, scope: EvidenceScope) -> None:
        if captured.run_id != self.run_id:
            raise EvidenceScopeError("captured observation belongs to another run")
        if scope.run_id != self.run_id:
            raise EvidenceScopeError("evidence scope belongs to another run")
        if not is_evidence_id(captured.evidence_id):
            raise EvidenceArgumentError(f"invalid evidence id: {captured.evidence_id}")
        if captured.representation not in {
            REPRESENTATION_TOOL_TEXT,
            REPRESENTATION_BINARY,
            "tool-json-v1",
        }:
            raise EvidenceArgumentError(f"unsupported representation: {captured.representation}")

    def _verify_idempotent(
        self, observation: EvidenceObservation, captured: CapturedObservation
    ) -> None:
        expected_text, expected_data, expected_representation = self._captured_payload(captured)
        if (
            observation.tool.name != captured.tool.name
            or _json_key(observation.tool.arguments) != _json_key(captured.tool.arguments)
            or observation.acquisition.started_at != captured.acquisition.started_at
            or observation.acquisition.finished_at != captured.acquisition.finished_at
            or observation.execution.outcome != captured.execution.outcome
            or observation.payload.representation != expected_representation
        ):
            raise EvidenceIdConflictError(
                f"evidence id {observation.id} is already committed with different acquisition data"
            )
        try:
            current = self._payload_bytes(observation)
        except EvidenceUnavailableError as exc:
            raise EvidenceUnavailableError(
                f"committed record {observation.id} has no usable payload: {exc}"
            ) from exc
        expected = expected_data if expected_data is not None else (expected_text or "").encode("utf-8")
        if current != expected:
            raise EvidenceIdConflictError(
                f"evidence id {observation.id} is already committed with different content"
            )

    def _verify_captured_equal(
        self, first: CapturedObservation, second: CapturedObservation
    ) -> None:
        if (
            first.tool.name != second.tool.name
            or _json_key(first.tool.arguments) != _json_key(second.tool.arguments)
            or first.acquisition.started_at != second.acquisition.started_at
            or first.acquisition.finished_at != second.acquisition.finished_at
            or first.representation != second.representation
            or first.text != second.text
            or first.data != second.data
        ):
            raise EvidenceIdConflictError(
                f"pending evidence id {second.evidence_id} was frozen with different data"
            )

    def _captured_payload(
        self, captured: CapturedObservation
    ) -> tuple[str | None, bytes | None, str]:
        if captured.representation == REPRESENTATION_BINARY:
            return None, captured.data, REPRESENTATION_BINARY
        return captured.text, None, captured.representation

    def _outcome_from_observation(
        self,
        observation: EvidenceObservation,
        *,
        index_dirty: bool,
        preview: str = "",
    ) -> SaveOutcome:
        descriptor = self._descriptor(observation)
        return SaveOutcome(
            evidence_id=observation.id,
            status="index_dirty" if index_dirty and self._index_dirty else "committed",
            durability=DURABILITY_COMMITTED,
            descriptor=descriptor,
            preview=preview,
            index_dirty=bool(index_dirty and self._index_dirty),
        )

    # ── descriptors and views ──────────────────────────────────────────
    def _descriptor(
        self, observation: EvidenceObservation, *, ledger_state: str = DURABILITY_COMMITTED
    ) -> EvidenceDescriptor:
        return EvidenceDescriptor(
            id=observation.id,
            run_id=observation.run_id,
            commit_seq=observation.commit_seq,
            record_kind=observation.record_kind,
            tool=observation.tool.name,
            target=observation.target,
            target_id=observation.target_id,
            resource=observation.resource,
            producer_task_id=observation.producer.task_id,
            identity_label=observation.identity.label,
            outcome=observation.execution.outcome,
            status=int(observation.execution.http_status or 0),
            started_at=observation.acquisition.started_at or "",
            finished_at=observation.acquisition.finished_at or "",
            representation=observation.payload.representation,
            size_chars=int(observation.payload.size_chars or 0),
            size_bytes=int(observation.payload.size_bytes or 0),
            captured_complete=observation.completeness.captured_complete,
            summary=_summary_text(observation),
            ledger_state=ledger_state,
        )

    @staticmethod
    def _pending_descriptor(captured: CapturedObservation) -> EvidenceDescriptor:
        size_chars = len(captured.text) if captured.text is not None else 0
        size_bytes = (
            len(captured.data)
            if captured.data is not None
            else len((captured.text or "").encode("utf-8"))
        )
        return EvidenceDescriptor(
            id=captured.evidence_id,
            run_id=captured.run_id,
            commit_seq=0,
            record_kind=captured.record_kind,
            tool=captured.tool.name,
            target=captured.target,
            target_id=captured.target_id,
            resource=captured.resource,
            producer_task_id=captured.producer.task_id,
            identity_label=captured.identity.label,
            outcome=captured.execution.outcome,
            status=int(captured.execution.http_status or 0),
            started_at=captured.acquisition.started_at or "",
            finished_at=captured.acquisition.finished_at or "",
            representation=captured.representation,
            size_chars=size_chars,
            size_bytes=size_bytes,
            captured_complete=captured.completeness.captured_complete,
            summary=captured.summary,
            ledger_state=DURABILITY_PENDING,
        )

    def observation(self, evidence_id: str, *, scope: EvidenceScope) -> EvidenceObservation:
        """Return committed observation metadata (never the payload)."""

        self._validate_read_id(evidence_id)
        observation = self._records.get(evidence_id)
        if observation is None:
            captured = self._pending.get(evidence_id)
            if captured is not None:
                raise EvidenceUnavailableError(
                    f"evidence {evidence_id} is pending and not yet a committed observation"
                )
            raise EvidenceNotFoundError(f"evidence {evidence_id} was not found")
        if not scope.can_read(observation):
            raise EvidenceScopeError(f"evidence {evidence_id} is not visible in this task")
        return observation.model_copy(deep=True)

    def read_payload(self, evidence_id: str, *, scope: EvidenceScope) -> bytes:
        """Read the exact committed bytes for an authorized import."""

        return self._payload_bytes(self.observation(evidence_id, scope=scope))

    def describe(self, evidence_id: str, *, scope: EvidenceScope) -> EvidenceDescriptor:
        self._validate_read_id(evidence_id)
        observation = self._records.get(evidence_id)
        if observation is not None:
            if not scope.can_read(observation):
                raise EvidenceScopeError(f"evidence {evidence_id} is not visible in this task")
            if not self._payload_available(observation):
                return self._descriptor(observation, ledger_state=DURABILITY_UNAVAILABLE)
            return self._descriptor(observation)
        captured = self._pending.get(evidence_id)
        if captured is not None:
            if not self._scope_can_read_captured(captured, scope):
                raise EvidenceScopeError(f"evidence {evidence_id} is not visible in this task")
            return self._pending_descriptor(captured)
        if evidence_id in self._invalid_records:
            return EvidenceDescriptor(
                id=evidence_id,
                ledger_state=DURABILITY_UNAVAILABLE,
                summary="record is unreadable or structurally invalid",
            )
        raise EvidenceNotFoundError(f"evidence {evidence_id} was not found")

    def list_records(
        self,
        filters: EvidenceFilters | None = None,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        scope: EvidenceScope,
    ) -> ListResult:
        filters = filters or EvidenceFilters()
        page_size = _clamp_int(limit, 1, MAX_LIST_LIMIT, "limit")
        state = _decode_cursor(cursor)
        if state and state.get("kind") != "list":
            raise EvidenceArgumentError("cursor belongs to a different query kind")
        watermark = int(state["watermark"]) if state else self.max_commit_seq
        after_key = _cursor_key(state) if state else None
        items = self._visible_descriptors(filters, scope)
        total = len(items)
        items.sort(key=lambda item: (item.commit_seq or 2**63, item.id), reverse=True)
        if after_key is not None:
            items = [item for item in items if (item.commit_seq or 2**63, item.id) < after_key]
        items = [item for item in items if item.commit_seq <= watermark or not item.commit_seq]
        page = items[:page_size]
        next_cursor = ""
        if len(items) > page_size and page:
            last = page[-1]
            next_cursor = _encode_cursor(
                {
                    "kind": "list",
                    "watermark": watermark,
                    "after_seq": last.commit_seq or 2**63,
                    "after_id": last.id,
                }
            )
        return ListResult(
            items=page,
            next_cursor=next_cursor,
            truncated=bool(next_cursor),
            returned_count=len(page),
            candidate_total=total,
            pending_count=sum(1 for item in page if item.ledger_state == DURABILITY_PENDING),
            scope_description=scope.describe(),
            watermark=watermark,
        )

    def view(
        self,
        evidence_id: str,
        *,
        offset: int = 0,
        limit: int = DEFAULT_VIEW_LIMIT,
        representation: str = REPRESENTATION_TOOL_TEXT,
        scope: EvidenceScope,
    ) -> ViewResult:
        if offset < 0:
            raise EvidenceArgumentError("offset must be non-negative")
        if limit <= 0:
            raise EvidenceArgumentError("limit must be positive; 0 is not 'read everything'")
        self._validate_read_id(evidence_id)
        descriptor, text, total_chars, size_bytes, durability = self._read_text(
            evidence_id, scope, representation
        )
        if offset > total_chars:
            raise EvidenceArgumentError(
                f"offset {offset} is beyond the {total_chars}-character payload"
            )
        effective_limit = min(int(limit), MAX_VIEW_LIMIT)
        end = min(total_chars, offset + effective_limit)
        chunk = text[offset:end]
        eof = offset >= total_chars
        descriptor = descriptor.model_copy(update={"ledger_state": durability})
        return ViewResult(
            descriptor=descriptor,
            representation=representation,
            text="" if eof else chunk,
            start=offset,
            end=end,
            total_chars=total_chars,
            size_bytes=size_bytes,
            next_offset=end if end < total_chars else None,
            eof=eof,
            durability=durability,
            returned_truncated=end < total_chars,
        )

    def _read_text(
        self, evidence_id: str, scope: EvidenceScope, representation: str
    ) -> tuple[EvidenceDescriptor, str, int, int, str]:
        observation = self._records.get(evidence_id)
        if observation is not None:
            if not scope.can_read(observation):
                raise EvidenceScopeError(f"evidence {evidence_id} is not visible in this task")
            if observation.payload.representation != representation:
                raise EvidenceArgumentError(
                    f"evidence {evidence_id} has representation "
                    f"{observation.payload.representation!r}, not {representation!r}"
                )
            data = self._payload_bytes(observation)
            if representation == REPRESENTATION_BINARY:
                raise EvidenceArgumentError(
                    "binary payloads cannot be viewed as text; use an artifact-aware reader"
                )
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise EvidenceUnavailableError(
                    f"payload for {evidence_id} is not valid UTF-8: {exc}"
                ) from exc
            return (
                self._descriptor(observation),
                text,
                len(text),
                len(data),
                DURABILITY_COMMITTED,
            )
        captured = self._pending.get(evidence_id)
        if captured is None:
            if evidence_id in self._invalid_records:
                raise EvidenceUnavailableError(
                    f"evidence {evidence_id} record is unreadable or structurally invalid"
                )
            raise EvidenceNotFoundError(f"evidence {evidence_id} was not found")
        if not self._scope_can_read_captured(captured, scope):
            raise EvidenceScopeError(f"evidence {evidence_id} is not visible in this task")
        if captured.representation != representation or captured.text is None:
            raise EvidenceArgumentError(
                f"pending evidence {evidence_id} has representation {captured.representation!r}"
            )
        data = captured.text.encode("utf-8")
        return (
            self._pending_descriptor(captured),
            captured.text,
            len(captured.text),
            len(data),
            DURABILITY_PENDING,
        )

    def read_text(
        self,
        evidence_id: str,
        *,
        scope: EvidenceScope,
        max_chars: int = 2_000_000,
    ) -> str:
        """Read a complete text payload for local analysis, with an explicit bound."""

        self._validate_read_id(evidence_id)
        _descriptor, text, total_chars, _size_bytes, _durability = self._read_text(
            evidence_id, scope, REPRESENTATION_TOOL_TEXT
        )
        if total_chars > max_chars:
            raise EvidenceArgumentError(
                f"evidence {evidence_id} has {total_chars} chars; local analysis is limited "
                f"to {max_chars}"
            )
        return text

    def search(
        self,
        query: str,
        filters: EvidenceFilters | None = None,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
        context_chars: int = DEFAULT_SEARCH_CONTEXT_CHARS,
        regex: bool = False,
        evidence_id: str = "",
        scope: EvidenceScope,
        scan_budget: int = SEARCH_SCAN_CHAR_BUDGET,
    ) -> SearchResult:
        if not str(query or ""):
            raise EvidenceArgumentError("search requires a non-empty query")
        if evidence_id:
            self._validate_read_id(evidence_id)
        page_size = _clamp_int(limit, 1, MAX_SEARCH_LIMIT, "limit")
        ctx = _clamp_int(context_chars, 0, MAX_SEARCH_CONTEXT_CHARS, "context_chars")
        state = _decode_cursor(cursor)
        if state and state.get("kind") != "search":
            raise EvidenceArgumentError("cursor belongs to a different query kind")
        watermark = int(state["watermark"]) if state else self.max_commit_seq
        cursor_key = _cursor_key(state) if state else None
        resume_offset = int(state.get("within_offset") or 0) if state else 0
        filters = filters or EvidenceFilters()
        if evidence_id:
            filters = filters.model_copy(update={"evidence_ids": [evidence_id]})
        candidates = self._visible_observations(filters, scope)
        candidates.sort(key=lambda item: (item.commit_seq, item.id))
        compiled: re.Pattern[str] | None = None
        if regex:
            try:
                compiled = re.compile(query, re.IGNORECASE | re.MULTILINE)
            except re.error as exc:
                raise EvidenceArgumentError(f"invalid regex: {exc}") from exc

        hits: list[SearchHit] = []
        unsearched: list[str] = []
        skipped: dict[str, str] = {}
        scanned_records = 0
        scanned_chars = 0
        scan_complete = True
        stop_key: tuple[int, str] | None = None
        stop_within = 0

        for index, observation in enumerate(candidates):
            if observation.commit_seq and observation.commit_seq > watermark:
                continue
            key = (observation.commit_seq, observation.id)
            if cursor_key is not None:
                if key < cursor_key:
                    continue
                if key == cursor_key and resume_offset <= 0:
                    continue
            if len(hits) >= page_size:
                stop_key = key
                stop_within = 0
                scan_complete = False
                break
            remaining = scan_budget - scanned_chars
            if remaining <= 0:
                unsearched.append(observation.id)
                skipped[observation.id] = "search scan character budget exhausted"
                stop_key = key
                stop_within = 0
                scan_complete = False
                break
            try:
                descriptor, text, total_chars, _size_bytes, durability = self._read_text(
                    observation.id, scope, observation.payload.representation
                )
            except (EvidenceUnavailableError, EvidenceArgumentError) as exc:
                unsearched.append(observation.id)
                skipped[observation.id] = str(exc)
                scan_complete = False
                continue
            if compiled is not None and total_chars > REGEX_RECORD_CHAR_LIMIT:
                unsearched.append(observation.id)
                skipped[observation.id] = (
                    f"record has {total_chars} chars; regex scans are limited to "
                    f"{REGEX_RECORD_CHAR_LIMIT}"
                )
                scan_complete = False
                continue
            if compiled is not None and total_chars > remaining:
                unsearched.append(observation.id)
                skipped[observation.id] = "record exceeds the remaining regex scan budget"
                stop_key = key
                stop_within = 0
                scan_complete = False
                break
            start_offset = resume_offset if key == cursor_key else 0
            scanned_records += 1
            consumed, record_hits, complete, stopped_at = self._scan_record(
                descriptor,
                text,
                query=query,
                compiled=compiled,
                context_chars=ctx,
                durability=durability,
                start_offset=start_offset,
                max_hits=page_size - len(hits),
                max_chars=remaining,
            )
            scanned_chars += consumed
            hits.extend(record_hits)
            if not complete:
                unsearched.append(observation.id)
                skipped.setdefault(observation.id, "scan stopped at the hit/character budget")
                stop_key = key
                stop_within = stopped_at
                scan_complete = False
                break
            if len(hits) >= page_size:
                stop_key = key
                stop_within = 0
                scan_complete = index >= len(candidates) - 1
                break
            if consumed >= remaining and index < len(candidates) - 1:
                unsearched.extend(item.id for item in candidates[index + 1:])
                stop_key = key
                stop_within = 0
                scan_complete = False
                break

        # Pending observations are searchable in this process, clearly marked.
        if cursor is None and len(hits) < page_size:
            for captured in self._visible_pending(filters, scope):
                if captured.representation != REPRESENTATION_TOOL_TEXT or captured.text is None:
                    continue
                descriptor = self._pending_descriptor(captured)
                consumed, record_hits, complete, _ = self._scan_record(
                    descriptor,
                    captured.text,
                    query=query,
                    compiled=compiled,
                    context_chars=ctx,
                    durability=DURABILITY_PENDING,
                    start_offset=0,
                    max_hits=page_size - len(hits),
                    max_chars=max(0, scan_budget - scanned_chars),
                )
                scanned_chars += consumed
                hits.extend(record_hits)
                if not complete:
                    scan_complete = False

        next_cursor = ""
        if stop_key is not None and (not scan_complete or self.pending_count):
            next_cursor = _encode_cursor(
                {
                    "kind": "search",
                    "watermark": watermark,
                    "after_seq": stop_key[0],
                    "after_id": stop_key[1],
                    "within_offset": stop_within,
                }
            )
        metadata_matches = (
            self._metadata_matches(query, filters, scope, limit=page_size) if not hits else []
        )
        return SearchResult(
            query=query,
            hits=hits,
            metadata_matches=metadata_matches,
            scan_complete=scan_complete and not unsearched,
            unsearched_ids=unsearched,
            skipped_reasons=skipped,
            scanned_records=scanned_records,
            scanned_chars=scanned_chars,
            returned_count=len(hits),
            next_cursor=next_cursor,
            pending_count=sum(1 for hit in hits if hit.durability == DURABILITY_PENDING),
            watermark=watermark,
        )

    def _scan_record(
        self,
        descriptor: EvidenceDescriptor,
        text: str,
        *,
        query: str,
        compiled: re.Pattern[str] | None,
        context_chars: int,
        durability: str,
        start_offset: int,
        max_hits: int,
        max_chars: int,
    ) -> tuple[int, list[SearchHit], bool, int]:
        hits: list[SearchHit] = []
        if max_hits <= 0:
            return 0, hits, False, start_offset
        if compiled is not None:
            consumed = min(len(text), max_chars)
            for match in compiled.finditer(text):
                if match.start() < start_offset:
                    continue
                if len(hits) >= max_hits:
                    return consumed, hits, False, match.start()
                hits.append(
                    self._hit(
                        descriptor, text, match.start(), match.end(), context_chars, durability
                    )
                )
            return consumed, hits, True, 0

        pattern = re.compile(re.escape(query), re.IGNORECASE)
        consumed = 0
        position = max(0, start_offset)
        overlap = max(0, len(query) - 1)
        while position < len(text):
            budget = max_chars - consumed
            if budget <= 0:
                return consumed, hits, False, position
            chunk_start = position
            chunk_end = min(len(text), chunk_start + LITERAL_CHUNK_CHARS + overlap)
            chunk = text[chunk_start:chunk_end]
            for match in pattern.finditer(chunk):
                absolute_start = chunk_start + match.start()
                absolute_end = chunk_start + match.end()
                if absolute_start < position:
                    continue
                if len(hits) >= max_hits:
                    consumed += max(0, absolute_start - chunk_start)
                    return consumed, hits, False, absolute_start
                hits.append(
                    self._hit(
                        descriptor, text, absolute_start, absolute_end, context_chars, durability
                    )
                )
            advance = min(budget, chunk_end - chunk_start)
            consumed += advance
            position = chunk_start + advance
            if len(hits) >= max_hits and position < len(text):
                return consumed, hits, False, position
        return consumed, hits, True, 0

    @staticmethod
    def _hit(
        descriptor: EvidenceDescriptor,
        text: str,
        start: int,
        end: int,
        context_chars: int,
        durability: str,
    ) -> SearchHit:
        snippet_start = max(0, start - context_chars)
        snippet_end = min(len(text), end + context_chars)
        return SearchHit(
            evidence_id=descriptor.id,
            representation=descriptor.representation,
            match_start=start,
            match_end=end,
            snippet_start=snippet_start,
            snippet_end=snippet_end,
            text=text[snippet_start:snippet_end],
            tool=descriptor.tool,
            target=descriptor.target,
            outcome=descriptor.outcome,
            durability=durability,
        )

    def _metadata_matches(
        self,
        query: str,
        filters: EvidenceFilters,
        scope: EvidenceScope,
        *,
        limit: int,
    ) -> list[SearchHit]:
        lowered = query.lower()
        matches: list[SearchHit] = []
        for descriptor in self._visible_descriptors(filters, scope):
            fields = {
                "tool": descriptor.tool,
                "target": descriptor.target,
                "resource": descriptor.resource,
                "identity_label": descriptor.identity_label or "",
                "summary": descriptor.summary,
            }
            for field_name, value in fields.items():
                position = value.lower().find(lowered)
                if position < 0:
                    continue
                matches.append(
                    SearchHit(
                        evidence_id=descriptor.id,
                        representation=descriptor.representation,
                        match_start=position,
                        match_end=position + len(query),
                        snippet_start=max(0, position - 80),
                        snippet_end=min(len(value), position + len(query) + 80),
                        text=value[max(0, position - 80): position + len(query) + 80],
                        tool=descriptor.tool,
                        target=descriptor.target,
                        outcome=descriptor.outcome,
                        durability=descriptor.ledger_state,
                        match_kind="metadata",
                        field=field_name,
                    )
                )
                break
            if len(matches) >= limit:
                break
        return matches

    # ── index maintenance ──────────────────────────────────────────────
    def flush_index(self) -> bool:
        if not self.writable:
            return False
        with self._lock:
            if not self._index_dirty:
                return True
            payload = self._index_payload()
            try:
                atomic_write_json(self.index_path, payload)
            except Exception as exc:
                self.health.index_write_failures += 1
                self._index_dirty = True
                self._persist_health()
                logger.warning(
                    "evidence index write failed run_id=%s error=%s index_write_failures=%s",
                    self.run_id,
                    type(exc).__name__,
                    self.health.index_write_failures,
                )
                return False
            self._index_dirty = False
            return True

    def rebuild_index(self) -> RebuildResult:
        self._scan_records()
        unavailable = [
            item_id
            for item_id in self._order
            if not self._payload_available(self._records[item_id])
        ]
        written = False
        error = ""
        with self._lock:
            self._index_dirty = True
            try:
                atomic_write_json(self.index_path, self._index_payload())
                self._index_dirty = False
                written = True
            except Exception as exc:
                error = str(exc)
                self.health.index_write_failures += 1
                self._persist_health()
                logger.warning(
                    "evidence index rebuild failed run_id=%s error=%s", self.run_id, exc
                )
        return RebuildResult(
            total_records=len(self._order),
            available=len(self._order) - len(unavailable),
            unavailable_ids=unavailable,
            max_commit_seq=self._max_seq,
            index_written=written,
            error=error,
        )

    def _index_payload(self) -> dict[str, Any]:
        records = []
        for item_id in self._order:
            observation = self._records[item_id]
            descriptor = self._descriptor(
                observation,
                ledger_state=(
                    DURABILITY_COMMITTED
                    if self._payload_available(observation)
                    else DURABILITY_UNAVAILABLE
                ),
            )
            records.append(descriptor.model_dump(mode="json"))
        return {
            "schema_version": EVIDENCE_INDEX_SCHEMA_VERSION,
            "run_id": self.run_id,
            "generated_at": utc_now_iso(),
            "max_commit_seq": self._max_seq,
            "records": records,
        }

    # ── pending recovery ───────────────────────────────────────────────
    def flush_pending(self, *, boundary_id: str = "") -> FlushResult:
        self._ensure_writable()
        self.check_writable_execution_allowed()
        with self._lock:
            pending = list(self._pending.values())
        result = FlushResult(attempted=len(pending))
        for captured in pending:
            try:
                outcome = self._attempt_commit(captured)
            except EvidencePersistenceError as exc:
                result.failed_ids.append(captured.evidence_id)
                self._record_failure(
                    exc.stage, boundary_id or f"flush_{captured.evidence_id}", exc
                )
                continue
            result.committed_ids.append(captured.evidence_id)
            result.outcomes.append(outcome)
        self.flush_index()
        return result

    # ── helpers ────────────────────────────────────────────────────────
    def _ensure_writable(self) -> None:
        if not self.writable:
            raise EvidencePersistenceError("ownership", "evidence store is read-only")

    def _validate_read_id(self, evidence_id: str) -> None:
        value = str(evidence_id or "").strip()
        if not value:
            raise EvidenceArgumentError("evidence_id is required")
        if not (is_evidence_id(value) or LEGACY_EVIDENCE_ID_PATTERN.fullmatch(value)):
            raise EvidenceArgumentError(f"invalid evidence id: {value}")

    def _scope_can_read_captured(
        self, captured: CapturedObservation, scope: EvidenceScope
    ) -> bool:
        if scope.read_all:
            return True
        producer = captured.producer.task_id or "main"
        return (
            producer == scope.task_id
            or captured.evidence_id in scope.inherited_evidence_ids
            or producer in scope.published_task_ids
        )

    def _visible_observations(
        self, filters: EvidenceFilters, scope: EvidenceScope
    ) -> list[EvidenceObservation]:
        result = []
        for item_id in self._order:
            observation = self._records[item_id]
            if not scope.can_read(observation):
                continue
            if _matches_filters(observation, filters):
                result.append(observation)
        return result

    def _visible_descriptors(
        self, filters: EvidenceFilters, scope: EvidenceScope
    ) -> list[EvidenceDescriptor]:
        descriptors = []
        for observation in self._visible_observations(filters, scope):
            state = (
                DURABILITY_COMMITTED
                if self._payload_available(observation)
                else DURABILITY_UNAVAILABLE
            )
            descriptors.append(self._descriptor(observation, ledger_state=state))
        for captured in self._visible_pending(filters, scope):
            descriptors.append(self._pending_descriptor(captured))
        return descriptors

    def _visible_pending(
        self, filters: EvidenceFilters, scope: EvidenceScope
    ) -> list[CapturedObservation]:
        pending = []
        with self._lock:
            values = list(self._pending.values())
        for captured in values:
            if not self._scope_can_read_captured(captured, scope):
                continue
            if _matches_captured_filters(captured, filters):
                pending.append(captured)
        return pending

    def _payload_available(self, observation: EvidenceObservation) -> bool:
        try:
            self._payload_bytes(observation)
        except EvidenceUnavailableError:
            return False
        return True

    def _payload_bytes(self, observation: EvidenceObservation) -> bytes:
        relative = str(observation.payload.path or "")
        if not relative:
            raise EvidenceUnavailableError(f"evidence {observation.id} has no payload path")
        path = (self.run_dir / relative).resolve()
        run_root = self.run_dir.resolve()
        if not path.is_relative_to(run_root):
            raise EvidenceUnavailableError(f"evidence {observation.id} payload escapes the run")
        payload_root = self.payloads_dir.resolve()
        if not path.is_relative_to(payload_root):
            raise EvidenceUnavailableError(
                f"evidence {observation.id} payload is outside the payload directory"
            )
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise EvidenceUnavailableError(
                f"evidence {observation.id} payload is unreadable: {exc}"
            ) from exc
        if len(data) != observation.payload.size_bytes:
            raise EvidenceUnavailableError(
                f"evidence {observation.id} payload size does not match its committed record"
            )
        return data

    # ── import support ─────────────────────────────────────────────────
    def import_observation(
        self,
        observation: EvidenceObservation,
        payload: bytes,
        *,
        scope: EvidenceScope,
    ) -> SaveOutcome:
        """Import a legacy/foreign observation under a new local commit envelope."""

        self._ensure_writable()
        if len(payload) != observation.payload.size_bytes:
            raise EvidenceUnavailableError(f"evidence {observation.id} payload size mismatch")
        provenance = observation.provenance.model_copy(deep=True)
        if observation.run_id != self.run_id:
            provenance.origin_run_id = provenance.origin_run_id or observation.run_id
            provenance.imported_from_run_id = observation.run_id
        captured = CapturedObservation(
            evidence_id=observation.id,
            run_id=self.run_id,
            target_id=observation.target_id,
            target=observation.target,
            resource=observation.resource,
            producer=observation.producer,
            tool=observation.tool,
            acquisition=observation.acquisition,
            identity=observation.identity,
            execution=observation.execution,
            completeness=observation.completeness,
            artifacts=list(observation.artifacts),
            provenance=provenance,
            record_kind=observation.record_kind,
            representation=observation.payload.representation,
            text=payload.decode("utf-8")
            if observation.payload.representation != REPRESENTATION_BINARY
            else None,
            data=payload if observation.payload.representation == REPRESENTATION_BINARY else None,
            summary=_summary_text(observation),
        )
        return self.commit_observation(captured, scope=scope, operation_id="import")


# ── cursor and filter helpers ───────────────────────────────────────────


def _clamp_int(value: Any, minimum: int, maximum: int, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise EvidenceArgumentError(f"{name} must be an integer") from exc
    if number < minimum:
        raise EvidenceArgumentError(f"{name} must be at least {minimum}")
    return min(number, maximum)


def _encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    if not cursor:
        return None
    if len(cursor) > _MAX_CURSOR_BYTES:
        raise EvidenceArgumentError("cursor is too large")
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise EvidenceArgumentError("invalid cursor") from exc
    if not isinstance(payload, dict):
        raise EvidenceArgumentError("invalid cursor")
    try:
        payload["watermark"] = int(payload.get("watermark") or 0)
        payload["after_seq"] = int(payload.get("after_seq") or 0)
    except (TypeError, ValueError) as exc:
        raise EvidenceArgumentError("invalid cursor") from exc
    return payload


def _cursor_key(state: dict[str, Any]) -> tuple[int, str]:
    return (int(state.get("after_seq") or 0), str(state.get("after_id") or ""))


def _matches_filters(observation: EvidenceObservation, filters: EvidenceFilters) -> bool:
    if filters.evidence_ids and observation.id not in filters.evidence_ids:
        return False
    if filters.target_id and observation.target_id != filters.target_id:
        return False
    if filters.target and observation.target != filters.target:
        return False
    if filters.resource and filters.resource not in observation.resource:
        return False
    if filters.tool and observation.tool.name != filters.tool:
        return False
    if filters.producer_task_id and observation.producer.task_id != filters.producer_task_id:
        return False
    if filters.identity_label and (observation.identity.label or "") != filters.identity_label:
        return False
    if filters.outcome and observation.execution.outcome != filters.outcome:
        return False
    if filters.record_kind and observation.record_kind != filters.record_kind:
        return False
    acquired = observation.acquisition.finished_at or observation.acquisition.started_at or ""
    if filters.since:
        _validate_time_filter(filters.since)
        if acquired and acquired < filters.since:
            return False
    if filters.until:
        _validate_time_filter(filters.until)
        if acquired and acquired > filters.until:
            return False
    return True


def _matches_captured_filters(
    captured: CapturedObservation, filters: EvidenceFilters
) -> bool:
    if filters.evidence_ids and captured.evidence_id not in filters.evidence_ids:
        return False
    if filters.target_id and captured.target_id != filters.target_id:
        return False
    if filters.target and captured.target != filters.target:
        return False
    if filters.resource and filters.resource not in captured.resource:
        return False
    if filters.tool and captured.tool.name != filters.tool:
        return False
    if filters.producer_task_id and captured.producer.task_id != filters.producer_task_id:
        return False
    if filters.identity_label and (captured.identity.label or "") != filters.identity_label:
        return False
    if filters.outcome and captured.execution.outcome != filters.outcome:
        return False
    if filters.record_kind and captured.record_kind != filters.record_kind:
        return False
    return True


def _validate_time_filter(value: str) -> None:
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceArgumentError(f"invalid time filter: {value!r}") from exc


def _summary_text(observation: EvidenceObservation) -> str:
    tool = observation.tool.name or "tool"
    resource = observation.resource or observation.target or ""
    outcome = observation.execution.outcome
    return f"{tool} {outcome} {resource}".strip()


# ── health mirrors and writer lock ──────────────────────────────────────

SerializedHealth = dict[str, Any] | PersistenceHealth


def read_persistence_health(run_dir: str | Path, *, run_id: str = "") -> PersistenceHealth | None:
    """Read health.json plus checkpoint mirrors and return the largest valid total."""

    root = Path(run_dir).expanduser()
    candidates: list[PersistenceHealth] = []
    health_path = root / "evidence" / "health.json"
    try:
        raw = json.loads(health_path.read_text(encoding="utf-8"))
        candidates.append(PersistenceHealth.model_validate(raw))
    except Exception:
        pass
    for state_path in sorted(root.glob("targets/*/state/current.json")):
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        mirror = payload.get("persistence_health")
        if not isinstance(mirror, dict):
            agent_state = payload.get("agent_state")
            if isinstance(agent_state, dict):
                mirror = agent_state.get("persistence_health")
        if not isinstance(mirror, dict):
            continue
        try:
            candidates.append(PersistenceHealth.model_validate(mirror))
        except Exception:
            continue
    valid = [item for item in candidates if not item.run_id or not run_id or item.run_id == run_id]
    if not valid:
        return None
    best = max(valid, key=lambda item: item.failure_total)
    if best.run_id and run_id and best.run_id != run_id:
        return None
    return best


class _WriterLock:
    """Exclusive cross-process writer ownership for one run."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: Any = None

    def acquire(self, run_dir: Path) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise EvidenceWriterBusyError(run_dir) from exc
        self._handle = handle

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
