"""Run-directory persistence foundation.

Runs are grouped by target and stored under a creation-time directory::

    <runs_root>/<target-slug>-<target_id>/<YYYYMMDDTHHMMSSZ>-<suffix>/

The time directory is the run.  ``run_name`` is a display/recovery label and no
longer a physical directory name.  Legacy flat runs (``<runs_root>/<name>/``)
remain readable; they are moved into the target/time layout on the first
writable resume.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shlex
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit
from uuid import uuid4

from vulnclaw.config.settings import CONFIG_DIR, ensure_dirs
from vulnclaw.targets import Target

RUN_SCHEMA_VERSION = 2
LEGACY_RUN_SCHEMA_VERSION = 1
RUN_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
_TIME_DIR_FORMAT = "%Y%m%dT%H%M%SZ"
_RUN_NAME_CONFLICT_LIMIT = 32

_PROCESS_LOCK = threading.RLock()
logger = logging.getLogger(__name__)


class RunContextError(RuntimeError):
    """Base class for run-context errors."""


class RunCollisionError(RunContextError):
    """Raised when an explicit run name would clobber or duplicate a run."""


class RunNotFoundError(RunContextError):
    """Raised when a run name does not match any stored run."""


class RunNameConflictError(RunContextError):
    """Raised when one run name matches several stored run directories."""

    def __init__(self, run_name: str, candidates: list[Path]) -> None:
        self.run_name = run_name
        self.candidates = candidates
        joined = ", ".join(str(path) for path in candidates)
        super().__init__(f"run name '{run_name}' matches multiple directories: {joined}")


class RunMigrationError(RunContextError):
    """Raised when a legacy flat run cannot be moved to the target/time layout."""


class RunCorruptError(RunContextError):
    """Raised when a run directory cannot be safely resumed."""

    def __init__(self, run_dir: Path, check: str) -> None:
        self.run_dir = run_dir
        self.check = check
        super().__init__(f"corrupt run state at {run_dir}: {check}")


@dataclass
class RunContext:
    """Loaded run directory and manifest."""

    run_dir: Path
    manifest: dict[str, Any]
    runs_root: Path | None = None
    targets: list[Target] = field(default_factory=list)
    legacy_layout: bool = False

    @property
    def run_id(self) -> str:
        return str(self.manifest.get("run_id") or "")

    @property
    def created_at(self) -> str:
        return str(self.manifest.get("created_at") or "")

    @property
    def run_name(self) -> str:
        return str(self.manifest.get("run_name") or self.run_dir.name)

    @property
    def schema_version(self) -> int:
        try:
            return int(self.manifest.get("schema_version", -1))
        except (TypeError, ValueError):
            return -1

    # ── evidence layout helpers ─────────────────────────────────────────
    @property
    def evidence_dir(self) -> Path:
        return self.run_dir / "evidence"

    @property
    def records_dir(self) -> Path:
        return self.evidence_dir / "records"

    @property
    def payloads_dir(self) -> Path:
        return self.evidence_dir / "payloads"

    @property
    def imports_dir(self) -> Path:
        return self.evidence_dir / "imports"

    @property
    def health_path(self) -> Path:
        return self.evidence_dir / "health.json"

    @property
    def evidence_index_path(self) -> Path:
        return self.evidence_dir / "index.json"

    def agent_dir(self, task_id: str) -> Path:
        return self.run_dir / "agents" / _safe_component(task_id, fallback="main")

    def target_manifest(self, target: Target | str | None = None) -> dict[str, Any]:
        items = self.manifest.get("targets", [])
        if not isinstance(items, list) or not items:
            raise RunCorruptError(self.run_dir, "run.json targets[] is empty")
        if target is None:
            first = items[0]
            if not isinstance(first, dict):
                raise RunCorruptError(self.run_dir, "run.json target entry is invalid")
            return first

        target_id = target.target_id if isinstance(target, Target) else str(target)
        for item in items:
            if not isinstance(item, dict):
                continue
            if target_id in {
                str(item.get("target_id") or ""),
                str(item.get("state_key") or ""),
                str(item.get("input") or ""),
                str(item.get("canonical") or ""),
            }:
                return item
        raise RunCorruptError(self.run_dir, f"target {target_id} is not listed in run.json")

    def state_dir(self, target: Target | str | None = None) -> Path:
        item = self.target_manifest(target)
        state_path = item.get("state_path")
        if not isinstance(state_path, str) or not state_path:
            target_id = str(item.get("target_id") or item.get("state_key") or "")
            if not target_id:
                raise RunCorruptError(self.run_dir, "target state_path is missing")
            return self.run_dir / "targets" / target_id / "state"
        path = Path(state_path)
        if path.is_absolute():
            return path.parent
        return (self.run_dir / path).parent

    def state_path(self, target: Target | str | None = None) -> Path:
        item = self.target_manifest(target)
        state_path = item.get("state_path")
        if isinstance(state_path, str) and state_path:
            path = Path(state_path)
            return path if path.is_absolute() else self.run_dir / path
        return self.state_dir(target) / "current.json"

    def snapshot_path(self, snapshot_id: str, target: Target | str | None = None) -> Path:
        return self.state_dir(target) / "snapshots" / f"{snapshot_id}.json"

    def target_json_path(self, target: Target | str | None = None) -> Path:
        item = self.target_manifest(target)
        target_id = str(item.get("target_id") or item.get("state_key") or "")
        if not target_id:
            raise RunCorruptError(self.run_dir, "target id is missing")
        return self.run_dir / "targets" / target_id / "target.json"

    def append_event(self, kind: str, payload: dict[str, Any] | None = None) -> None:
        event = {
            "timestamp": _now_iso(),
            "kind": kind,
            "payload": payload or {},
        }
        path = self.run_dir / "events" / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def update_manifest(self, **updates: Any) -> None:
        self.manifest.update(updates)
        self.manifest["updated_at"] = _now_iso()
        write_manifest(self)

    def record_checkpoint(self, snapshot_id: str, *, reason: str, target_id: str = "") -> None:
        resume = self.manifest.setdefault("resume", {})
        if not isinstance(resume, dict):
            resume = {}
            self.manifest["resume"] = resume
        resume["last_checkpoint_at"] = _now_iso()
        resume["last_snapshot_id"] = snapshot_id
        if target_id:
            resume["last_target_id"] = target_id
        resume["last_reason"] = reason
        self.manifest["status"] = "running"
        self.update_manifest()
        self.append_event(
            "checkpoint",
            {"snapshot_id": snapshot_id, "reason": reason, "target_id": target_id},
        )


def resolve_runs_root(runs_dir: str | Path | None = None, config: Any | None = None) -> Path:
    ensure_dirs()
    if runs_dir:
        return _absolute_root(runs_dir)
    if env_value := os.environ.get("VULNCLAW_RUNS_DIR"):
        return _absolute_root(env_value)
    config_runs_dir = getattr(getattr(config, "session", None), "runs_dir", None)
    if config_runs_dir:
        return _absolute_root(config_runs_dir)
    return _absolute_root(CONFIG_DIR / "runs")


def create_run_context(
    *,
    command: str,
    targets: list[Target],
    runs_dir: str | Path | None = None,
    config: Any | None = None,
    run_name: str | None = None,
) -> RunContext:
    """Create a new run under the target/time layout.

    ``run_name`` is a durable label used for ``--resume``; the physical
    directory always uses the creation time plus a short suffix from the run
    UUID.  The default name is the generated time-directory name.
    """

    if not targets:
        raise ValueError("at least one target is required")
    explicit_name = bool(run_name)
    if run_name:
        _validate_run_name(run_name)
    runs_root = _absolute_root(resolve_runs_root(runs_dir, config))
    runs_root.mkdir(parents=True, exist_ok=True)

    created_dt = datetime.now(timezone.utc)
    created_at = _iso(created_dt)
    target_group = target_group_dir(runs_root, targets[0])

    with _creation_lock(runs_root):
        if explicit_name and _find_run_dirs_by_name(runs_root, run_name or ""):
            raise RunCollisionError(
                f"run name '{run_name}' already exists under {runs_root}; use --resume-run"
            )
        if explicit_name and (runs_root / str(run_name)).exists():
            raise RunCollisionError(
                f"run name '{run_name}' collides with an existing legacy directory at "
                f"{runs_root / str(run_name)}"
            )

        run_id = str(uuid4())
        run_dir = target_group / _run_dir_name(created_dt, run_id)
        attempts = 0
        while run_dir.exists():
            attempts += 1
            if attempts > _RUN_NAME_CONFLICT_LIMIT:
                raise RunCollisionError(f"cannot allocate a unique run directory under {target_group}")
            run_id = str(uuid4())
            run_dir = target_group / _run_dir_name(created_dt, run_id)

        effective_name = run_name or run_dir.name
        if not explicit_name and _find_run_dirs_by_name(runs_root, effective_name):
            raise RunCollisionError(f"generated run name '{effective_name}' already exists")

        _create_run_layout(run_dir, targets)
        manifest = _build_manifest(
            command=command,
            run_id=run_id,
            run_name=effective_name,
            created_at=created_at,
            targets=targets,
        )
        context = RunContext(
            run_dir=run_dir,
            manifest=manifest,
            runs_root=runs_root,
            targets=list(targets),
        )
        write_manifest(context)
        for target in targets:
            atomic_write_json(context.target_json_path(target), target.to_manifest())
        context.append_event("run_created", {"command": command, "run_name": effective_name})
    return context


def load_run_context(
    run_name: str,
    *,
    runs_dir: str | Path | None = None,
    config: Any | None = None,
    repair: bool = False,
) -> RunContext:
    """Locate a run by its durable ``run_name`` and load it."""

    _validate_run_name(run_name)
    runs_root = _absolute_root(resolve_runs_root(runs_dir, config))
    if repair:
        return repair_run_context(run_name, runs_dir=runs_root, config=config)
    run_dir = _locate_run_dir(runs_root, run_name)
    return load_run_context_from_dir(run_dir, runs_root=runs_root)


def load_run_context_from_dir(
    run_dir: str | Path,
    *,
    runs_root: str | Path | None = None,
    expected_run_id: str | None = None,
    resume_request: bool = True,
    validate: bool = True,
) -> RunContext:
    """Load a run from an already-known directory.

    Callers that already know the physical run directory must use this entry
    instead of re-deriving a runs root from ``run_dir.parent``.
    """

    path = Path(run_dir).expanduser().resolve()
    manifest = _read_manifest(path)
    context = RunContext(
        run_dir=path,
        manifest=manifest,
        runs_root=_absolute_root(runs_root) if runs_root is not None else None,
        targets=targets_from_manifest(manifest, run_dir=path),
        legacy_layout=is_legacy_layout(path, _absolute_root(runs_root) if runs_root is not None else None),
    )
    if expected_run_id and context.run_id != expected_run_id:
        raise RunCorruptError(
            path,
            f"run_id mismatch: expected {expected_run_id}, found {context.run_id or 'missing'}",
        )
    if validate:
        validate_run_context(context, resume_request=resume_request)
    return context


def validate_run_context(context: RunContext, *, resume_request: bool = False) -> None:
    """Validate manifest identity and every referenced target state.

    Empty optional artifact directories (reports, logs, temp, ...) are not run
    corruption: the run layout is created on demand.  Referenced state files
    and a supported manifest schema are required.
    """

    manifest = context.manifest
    schema_version = context.schema_version
    if schema_version not in {RUN_SCHEMA_VERSION, LEGACY_RUN_SCHEMA_VERSION}:
        raise RunCorruptError(context.run_dir, "unsupported run.json schema_version")
    if not context.run_id:
        raise RunCorruptError(context.run_dir, "run.json run_id is missing")
    target_items = manifest.get("targets")
    if not isinstance(target_items, list) or not target_items:
        raise RunCorruptError(context.run_dir, "run.json targets[] is missing")

    for item in target_items:
        if not isinstance(item, dict):
            raise RunCorruptError(context.run_dir, "target entry is invalid")
        state_path = item.get("state_path")
        if not isinstance(state_path, str) or not state_path:
            raise RunCorruptError(context.run_dir, "target state_path is missing")
        path = Path(state_path)
        resolved = path if path.is_absolute() else context.run_dir / path
        if not resolved.exists():
            raise RunCorruptError(context.run_dir, f"target state is missing: {state_path}")

    resume = manifest.get("resume", {})
    if isinstance(resume, dict):
        snapshot_id = str(resume.get("last_snapshot_id") or "")
        if snapshot_id and not _snapshot_exists(context, snapshot_id):
            raise RunCorruptError(
                context.run_dir, f"resume.last_snapshot_id dangles: {snapshot_id}"
            )

    del resume_request


def repair_run_context(
    run_name: str,
    *,
    runs_dir: str | Path | None = None,
    config: Any | None = None,
) -> RunContext:
    """Locate one run by name, then roll its current state back to the latest snapshot."""

    _validate_run_name(run_name)
    runs_root = _absolute_root(resolve_runs_root(runs_dir, config))
    run_dir = _locate_run_dir(runs_root, run_name)
    context = load_run_context_from_dir(run_dir, runs_root=runs_root, validate=False)
    error_limit = int(getattr(getattr(config, "session", None), "persistence_error_limit", 5))
    with _run_write_guard(context, error_limit=error_limit):
        context = _ensure_writable_run_layout(context)
        targets = context.manifest.get("targets", [])
        if not isinstance(targets, list) or not targets:
            raise RunCorruptError(context.run_dir, "cannot repair run without targets[]")

        latest_snapshot = ""
        for item in targets:
            if not isinstance(item, dict):
                continue
            target_id = str(item.get("target_id") or item.get("state_key") or "")
            if not target_id:
                continue
            state_dir = context.run_dir / "targets" / target_id / "state"
            snapshots = sorted((state_dir / "snapshots").glob("*.json"))
            if not snapshots:
                continue
            snapshot = snapshots[-1]
            latest_snapshot = snapshot.stem
            atomic_write_text(state_dir / "current.json", snapshot.read_text(encoding="utf-8"))

        if not latest_snapshot:
            raise RunCorruptError(context.run_dir, "cannot repair run because no valid snapshots exist")
        resume = context.manifest.setdefault("resume", {})
        if isinstance(resume, dict):
            resume["last_snapshot_id"] = latest_snapshot
            resume["last_checkpoint_at"] = _now_iso()
        context.manifest["status"] = "interrupted"
        context.update_manifest()
        context.append_event("repair", {"last_snapshot_id": latest_snapshot})
        validate_run_context(context, resume_request=True)
        return context


def ensure_writable_run_layout(context: RunContext, *, error_limit: int = 5) -> RunContext:
    """Move a legacy flat run into the target/time layout on first writable use."""

    if not context.legacy_layout and context.schema_version >= RUN_SCHEMA_VERSION:
        return context
    with _run_write_guard(context, error_limit=error_limit):
        return _ensure_writable_run_layout(context)


def _ensure_writable_run_layout(context: RunContext) -> RunContext:
    if context.legacy_layout:
        return _migrate_legacy_run_layout(context)
    if context.schema_version < RUN_SCHEMA_VERSION:
        return _finish_interrupted_layout_migration(context)
    return context


@contextmanager
def _run_write_guard(context: RunContext, *, error_limit: int) -> Iterator[None]:
    # Share the evidence writer lock so a directory cannot move beneath an agent.
    from vulnclaw.evidence.models import PersistenceErrorLimitExceeded
    from vulnclaw.evidence.store import _WriterLock, read_persistence_health

    if not context.run_dir.is_dir():
        raise RunMigrationError(f"run directory disappeared: {context.run_dir}")
    lock = _WriterLock(context.run_dir / ".evidence-writer.lock")
    lock.acquire(context.run_dir)
    try:
        health = read_persistence_health(context.run_dir, run_id=context.run_id)
        if health is not None and health.failure_total > error_limit:
            last = health.last_error or {}
            raise PersistenceErrorLimitExceeded(
                failure_total=health.failure_total, limit=error_limit,
                stage=str(last.get("stage") or "unknown"),
                last_error=str(last.get("message") or "persistence limit reached"),
                unsaved_ids=health.pending_ids,
            )
        yield
    finally:
        lock.release()


def _manifest_schema_version(manifest: dict[str, Any]) -> int:
    try:
        return int(manifest.get("schema_version") or LEGACY_RUN_SCHEMA_VERSION)
    except (TypeError, ValueError):
        return LEGACY_RUN_SCHEMA_VERSION


def _finish_interrupted_layout_migration(context: RunContext) -> RunContext:
    """Complete a move that happened before the manifest/navigation update.

    The old directory no longer exists, so the recorded import source is the
    only reliable prefix for rebasing old absolute references.
    """

    runs_root = context.runs_root
    if runs_root is None:
        raise RunMigrationError("cannot finish a run layout migration without its runs root")
    source_record: dict[str, Any] = {}
    source_path = context.run_dir / "evidence" / "imports" / "layout-migration.json"
    try:
        loaded = json.loads(source_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            source_record = loaded
    except (OSError, json.JSONDecodeError):
        pass
    old_raw = str(source_record.get("old_run_dir") or "").strip()
    old_run_dir = Path(old_raw).expanduser() if old_raw else context.run_dir
    old_run_name = str(
        source_record.get("old_run_name") or context.manifest.get("run_name") or context.run_dir.name
    )
    source_record.setdefault("schema_version", 1)
    source_record.setdefault("run_id", context.run_id)
    source_record.setdefault("old_run_dir", str(old_run_dir.resolve()))
    source_record.setdefault("old_run_name", old_run_name)
    return _finish_legacy_migration(
        context.run_dir, runs_root, old_run_dir, old_run_name, source_record
    )


def migrate_legacy_run_layout(context: RunContext) -> RunContext:
    return ensure_writable_run_layout(context)


def _migrate_legacy_run_layout(context: RunContext) -> RunContext:
    """Move ``<runs_root>/<old-name>/`` to ``<runs_root>/<target>/<time>/``.

    The run keeps its ``run_id``, ``created_at`` and every file.  A small
    import-source record is committed inside the old directory before the move
    so interrupted migrations can be explained and resumed.
    """

    runs_root = context.runs_root
    if runs_root is None:
        raise RunMigrationError("cannot migrate a legacy run without its runs root")
    manifest = context.manifest
    run_id = str(manifest.get("run_id") or "").strip()
    if not run_id:
        raise RunMigrationError(f"legacy run at {context.run_dir} has no run_id")
    created_at = _parse_created_at(manifest.get("created_at"))
    targets = targets_from_manifest(manifest, run_dir=context.run_dir)
    destination = target_group_dir(runs_root, targets[0]) / _run_dir_name(created_at, run_id)
    old_run_dir = context.run_dir
    old_run_name = str(manifest.get("run_name") or old_run_dir.name)
    source_record = {
        "schema_version": 1,
        "run_id": run_id,
        "old_run_dir": str(old_run_dir.resolve()),
        "old_run_name": old_run_name,
        "migrated_at": _now_iso(),
    }

    with _creation_lock(runs_root):
        if not old_run_dir.exists():
            if destination.exists():
                # The move completed but the manifest update did not.
                return _finish_legacy_migration(
                    destination, runs_root, old_run_dir, old_run_name, source_record
                )
            raise RunMigrationError(f"legacy run directory disappeared: {old_run_dir}")
        if destination.exists():
            raise RunMigrationError(
                f"cannot migrate legacy run {old_run_dir}: destination already exists at "
                f"{destination}; both directories were kept"
            )
        atomic_write_json(old_run_dir / "evidence" / "imports" / "layout-migration.json", source_record)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.rename(old_run_dir, destination)
        except OSError as exc:
            raise RunMigrationError(
                f"cannot move legacy run {old_run_dir} to {destination}: {exc}"
            ) from exc

    return _finish_legacy_migration(
        destination, runs_root, old_run_dir, old_run_name, source_record
    )


def _finish_legacy_migration(
    destination: Path,
    runs_root: Path,
    old_run_dir: Path,
    old_run_name: str,
    source_record: dict[str, Any],
) -> RunContext:
    manifest = _read_manifest(destination)
    manifest["schema_version"] = RUN_SCHEMA_VERSION
    manifest.setdefault("run_name", old_run_name)
    manifest = _rebase_manifest_paths(manifest, old_run_dir=old_run_dir, new_run_dir=destination)
    manifest["updated_at"] = _now_iso()
    context = RunContext(
        run_dir=destination,
        manifest=manifest,
        runs_root=runs_root,
        targets=targets_from_manifest(manifest, run_dir=destination),
        legacy_layout=False,
    )
    _rebase_current_state_paths(context, old_run_dir=old_run_dir, new_run_dir=destination)
    # The schema update is the completion marker; leave schema 1 on partial failure.
    write_manifest(context)
    try:
        from vulnclaw.target_state.store import refresh_run_index

        refresh_run_index(context, previous_run_dir=old_run_dir)
    except Exception:
        logger.warning("could not refresh target index after migrating %s", destination, exc_info=True)
    context.append_event(
        "layout_migrated",
        {
            "run_id": source_record.get("run_id", ""),
            "old_run_dir": source_record.get("old_run_dir", ""),
            "new_run_dir": str(destination),
        },
    )
    return context


def is_legacy_layout(run_dir: Path, runs_root: Path | None) -> bool:
    """A legacy run sits directly under the runs root, not two levels down."""

    if runs_root is None:
        return False
    try:
        relative = run_dir.resolve().relative_to(runs_root.resolve())
    except (OSError, ValueError):
        return False
    return len(relative.parts) == 1


def target_group_dir(runs_root: Path, target: Target) -> Path:
    """Return the outer grouping directory for one target."""

    return Path(runs_root) / f"{target_dir_slug(target)}-{target.target_id}"


def target_dir_slug(target: Target) -> str:
    """Readable short name for the target grouping directory.

    URLs use the parsed hostname, local repositories their directory basename,
    and domains/IPs their normalized canonical value.  Display labels never
    participate, so renaming a label cannot split the grouping.
    """

    canonical = target.canonical or target.raw
    if target.kind in {"web_url", "repo_url"}:
        name = urlsplit(canonical).hostname or ""
    elif target.kind == "local_repo":
        name = Path(canonical).name
    else:
        name = canonical
    return _slugify(name or "target")


def targets_from_manifest(manifest: dict[str, Any], *, run_dir: Path | None = None) -> list[Target]:
    """Reconstruct ordered targets from a manifest, preserving target order."""

    targets: list[Target] = []
    items = manifest.get("targets", [])
    if not isinstance(items, list):
        return targets
    for item in items:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("input") or item.get("canonical") or "")
        canonical = str(item.get("canonical") or raw)
        if not canonical:
            continue
        targets.append(
            Target(
                kind=str(item.get("type") or "domain"),  # type: ignore[arg-type]
                raw=raw,
                canonical=canonical,
                label=item.get("label") if isinstance(item.get("label"), str) else None,
                ingress_mode=str(item.get("ingress_mode") or "copy"),  # type: ignore[arg-type]
                scope_mode=str(item.get("scope_mode") or "auto"),  # type: ignore[arg-type]
                diff_base=item.get("diff_base") if isinstance(item.get("diff_base"), str) else None,
            )
        )
    if not targets and run_dir is not None:
        raise RunCorruptError(run_dir, "run.json targets[] contains no usable target")
    return targets


def write_manifest(context: RunContext) -> None:
    atomic_write_json(context.run_dir / "run.json", context.manifest)


def mark_run_status(
    context: RunContext,
    status: str,
    *,
    exit_code: int | None = None,
    message: str = "",
) -> None:
    updates: dict[str, Any] = {"status": status}
    if status in {"completed", "interrupted", "failed"}:
        updates["ended_at"] = _now_iso()
    if exit_code is not None:
        updates["exit_code"] = exit_code
    if message:
        updates["message"] = message
    context.update_manifest(**updates)
    context.append_event("run_status", updates)


def build_completion_summary(
    *,
    context: RunContext | None,
    session: Any,
    command: str,
    restored: bool,
    snapshot_id: str = "",
    status: str = "completed",
    exit_code: int = 0,
) -> dict[str, Any]:
    findings = list(getattr(session, "findings", []) or [])
    verified = (
        session.get_verified_findings()
        if hasattr(session, "get_verified_findings")
        else [f for f in findings if getattr(f, "verified", False)]
    )
    pending = (
        session.get_pending_findings()
        if hasattr(session, "get_pending_findings")
        else [f for f in findings if getattr(f, "verification_status", "pending") == "pending"]
    )
    summary: dict[str, Any] = {
        "target": getattr(session, "target", "") or "",
        "command": command,
        "restored": restored,
        "snapshot_id": snapshot_id,
        "status": status,
        "exit_code": exit_code,
        "exit_meaning": _exit_meaning(exit_code),
        "run_name": "",
        "run_id": "",
        "run_dir": "",
        "resume_command": "",
        "artifact_locations": {},
        "findings_count": len(findings),
        "verified_count": len(verified),
        "pending_count": len(pending),
        "candidate_count": len(
            session.get_candidate_findings() if hasattr(session, "get_candidate_findings") else []
        ),
        "quarantined_count": 0,
    }
    if context is not None:
        resume_command = f"vulnclaw --resume {shlex.quote(context.run_name)}"
        if context.runs_root is not None and not _is_default_runs_root(context.runs_root):
            resume_command += f" --runs-dir {shlex.quote(str(context.runs_root))}"
        summary.update(
            {
                "run_name": context.run_name,
                "run_id": context.run_id,
                "run_dir": str(context.run_dir),
                "resume_command": resume_command,
                "artifact_locations": {
                    "manifest": str(context.run_dir / "run.json"),
                    "events": str(context.run_dir / "events" / "events.jsonl"),
                    "findings": str(context.run_dir / "evidence" / "findings"),
                    "reports": str(context.run_dir / "reports"),
                    "evidence": str(context.evidence_dir),
                },
            }
        )
    return summary


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)
    dir_flags = getattr(os, "O_DIRECTORY", None)
    if dir_flags is None:
        return
    try:
        dir_fd = os.open(str(path.parent), dir_flags)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomically commit raw bytes without a text round-trip."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    with tmp_path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)
    dir_flags = getattr(os, "O_DIRECTORY", None)
    if dir_flags is None:
        return
    try:
        dir_fd = os.open(str(path.parent), dir_flags)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


# ── location helpers ────────────────────────────────────────────────────


def iter_run_dirs(runs_root: Path) -> Iterator[Path]:
    """Yield committed run directories in the two-level layout plus legacy flat runs."""

    if not runs_root.exists():
        return
    for entry in sorted(runs_root.iterdir(), key=lambda item: item.name):
        if not entry.is_dir():
            continue
        if (entry / "run.json").is_file():
            yield entry
            continue
        try:
            children = sorted(entry.iterdir(), key=lambda item: item.name)
        except OSError:
            continue
        for child in children:
            if child.is_dir() and (child / "run.json").is_file():
                yield child


def _locate_run_dir(runs_root: Path, run_name: str) -> Path:
    matches = _find_run_dirs_by_name(runs_root, run_name)
    if not matches:
        legacy_corrupt = _legacy_corrupt_candidate(runs_root, run_name)
        if legacy_corrupt is not None:
            path, error = legacy_corrupt
            raise RunCorruptError(path, f"cannot read candidate manifest: {error}")
        raise RunNotFoundError(f"run '{run_name}' was not found under {runs_root}")
    if len(matches) > 1:
        raise RunNameConflictError(run_name, matches)
    return matches[0]


def _find_run_dirs_by_name(runs_root: Path, run_name: str) -> list[Path]:
    matches: list[Path] = []
    legacy_flat = runs_root / run_name
    if (legacy_flat / "run.json").is_file():
        matches.append(legacy_flat)
    if not runs_root.exists():
        return matches
    for entry in sorted(runs_root.iterdir(), key=lambda item: item.name):
        if not entry.is_dir() or entry == legacy_flat:
            continue
        if (entry / "run.json").is_file():
            # Legacy flat run: old semantics allowed the directory basename to be
            # the recovery name.
            if entry.name == run_name:
                matches.append(entry)
                continue
        try:
            children = sorted(entry.iterdir(), key=lambda item: item.name)
        except OSError:
            continue
        for child in children:
            if not child.is_dir():
                continue
            manifest = _try_read_manifest(child)
            if manifest is None:
                continue
            if str(manifest.get("run_name") or "") == run_name:
                matches.append(child)
    return matches


def _legacy_corrupt_candidate(runs_root: Path, run_name: str) -> tuple[Path, str] | None:
    candidate = runs_root / run_name
    if not candidate.exists() or not candidate.is_dir():
        return None
    try:
        _read_manifest(candidate)
    except RunCorruptError as exc:
        return candidate, exc.check
    return None


def _read_manifest(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "run.json"
    if not run_dir.exists():
        raise RunCorruptError(run_dir, "run directory does not exist")
    if not path.exists():
        raise RunCorruptError(run_dir, "run.json is missing")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunCorruptError(run_dir, f"run.json is invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise RunCorruptError(run_dir, "run.json root must be an object")
    return raw


def _try_read_manifest(run_dir: Path) -> dict[str, Any] | None:
    try:
        return _read_manifest(run_dir)
    except RunCorruptError:
        return None


def _create_run_layout(run_dir: Path, targets: list[Target]) -> None:
    for relative in (
        "events",
        "logs",
        "agents",
        "evidence/records",
        "evidence/payloads",
        "evidence/imports",
        "reports",
        "temp",
    ):
        (run_dir / relative).mkdir(parents=True, exist_ok=True)
    (run_dir / "events" / "events.jsonl").touch(exist_ok=True)
    for target in targets:
        (run_dir / "targets" / target.target_id / "state" / "snapshots").mkdir(
            parents=True, exist_ok=True
        )


def _build_manifest(
    *,
    command: str,
    run_id: str,
    run_name: str,
    created_at: str,
    targets: list[Target],
) -> dict[str, Any]:
    target_entries = []
    for target in targets:
        state_path = f"targets/{target.target_id}/state/current.json"
        entry = target.to_manifest(state_path=state_path)
        target_entries.append(entry)
    return {
        "schema_version": RUN_SCHEMA_VERSION,
        "run_id": run_id,
        "run_name": run_name,
        "created_at": created_at,
        "updated_at": created_at,
        "started_at": created_at,
        "ended_at": "",
        "status": "created",
        "command": command,
        "mode": command,
        "targets": target_entries,
        "artifacts": {
            "events": "events/events.jsonl",
            "logs": "logs/",
            "agents": "agents/",
            "evidence": "evidence/",
            "findings": "evidence/findings/",
            "reports": "reports/",
        },
        "resume": {
            "last_checkpoint_at": "",
            "last_snapshot_id": "",
            "last_target_id": "",
            "last_reason": "",
        },
    }


def _rebase_manifest_paths(
    manifest: dict[str, Any], *, old_run_dir: Path, new_run_dir: Path
) -> dict[str, Any]:
    """Convert absolute references that point inside the moved run to relative paths."""

    old_root = old_run_dir.resolve()

    def rebase(value: Any) -> Any:
        if not isinstance(value, str) or not value:
            return value
        path = Path(value)
        if not path.is_absolute():
            return value
        try:
            relative = path.resolve().relative_to(old_root)
        except (OSError, ValueError):
            return value
        return relative.as_posix()

    target_items = manifest.get("targets")
    if not isinstance(target_items, list):
        target_items = []
    for item in target_items:
        if isinstance(item, dict) and "state_path" in item:
            item["state_path"] = rebase(item.get("state_path"))
    artifacts = manifest.get("artifacts")
    if isinstance(artifacts, dict):
        for key, value in list(artifacts.items()):
            artifacts[key] = rebase(value)
    return manifest


def _rebase_current_state_paths(context: RunContext, *, old_run_dir: Path, new_run_dir: Path) -> None:
    """Rebase current and historical checkpoints so snapshot restores survive the move."""

    from vulnclaw.agent.context import VulnerabilityFinding
    from vulnclaw.findings.types import finding_input_digest

    old_root = old_run_dir.resolve()
    for state_dir in context.run_dir.glob("targets/*/state"):
        paths = [state_dir / "current.json", *sorted((state_dir / "snapshots").glob("*.json"))]
        for path in paths:
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                # Repair can still restore a damaged current.json from a valid snapshot.
                continue
            if not isinstance(payload, dict):
                continue
            changed = False
            meta = payload.get("resume_meta")
            if isinstance(meta, dict) and meta.get("run_dir") == str(old_root):
                meta["run_dir"] = str(new_run_dir)
                changed = True
            for finding in payload.get("findings", []):
                assessment = finding.get("assessment")
                assessment_current = assessment is not None and assessment.get("input_digest") == (
                    finding_input_digest(VulnerabilityFinding.model_validate(finding))
                )
                relocated = False
                for ref in finding.get("evidence_snapshots", []):
                    root = Path(str(ref.get("root") or ""))
                    if not root.is_absolute() or not root.is_relative_to(old_root):
                        continue
                    relative = root.relative_to(old_root)
                    ref["root"] = "evidence" if relative == Path("evidence") else str(
                        new_run_dir / relative
                    )
                    changed = True
                    relocated = True
                if relocated and assessment_current:
                    assessment["input_digest"] = finding_input_digest(
                        VulnerabilityFinding.model_validate(finding)
                    )
            if changed:
                atomic_write_json(path, payload)


def _parse_created_at(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise RunMigrationError("legacy run has no created_at; refusing to guess a directory time")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RunMigrationError(f"legacy run has invalid created_at: {text!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _run_dir_name(created_at: datetime, run_id: str) -> str:
    suffix = run_id.replace("-", "")[:8]
    return f"{created_at.astimezone(timezone.utc).strftime(_TIME_DIR_FORMAT)}-{suffix}"


def _absolute_root(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def _is_default_runs_root(runs_root: Path) -> bool:
    try:
        return Path(runs_root).resolve() == (CONFIG_DIR / "runs").resolve()
    except OSError:
        return False


@contextmanager
def _creation_lock(runs_root: Path) -> Iterator[None]:
    """Serialize run creation and directory moves under one runs root."""

    runs_root.mkdir(parents=True, exist_ok=True)
    lock_path = runs_root / ".runs.lock"
    with _PROCESS_LOCK, lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _snapshot_exists(context: RunContext, snapshot_id: str) -> bool:
    targets = context.manifest.get("targets", [])
    if not isinstance(targets, list):
        return False
    for item in targets:
        if not isinstance(item, dict):
            continue
        target_id = str(item.get("target_id") or item.get("state_key") or "")
        if not target_id:
            continue
        if (
            context.run_dir
            / "targets"
            / target_id
            / "state"
            / "snapshots"
            / f"{snapshot_id}.json"
        ).exists():
            return True
    return False


def _validate_run_name(run_name: str) -> None:
    if not RUN_NAME_PATTERN.fullmatch(run_name):
        raise ValueError("run name must match [A-Za-z0-9_.-]{1,120}")


def _safe_component(value: str, *, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "")).strip("-._")
    return cleaned[:80] or fallback


def _now_iso() -> str:
    return _iso(datetime.now(timezone.utc))


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip().lower()).strip("-._")
    return slug[:40] or "target"


def _exit_meaning(exit_code: int) -> str:
    if exit_code == 0:
        return "completed"
    if exit_code == 130:
        return "interrupted"
    return "failed"
