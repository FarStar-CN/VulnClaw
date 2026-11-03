"""Shared task orchestration helpers for CLI and Web flows."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from vulnclaw.agent.context import SessionState
from vulnclaw.agent.core import AgentCore
from vulnclaw.run_context import (
    RunContext,
    RunCorruptError,
    build_completion_summary,
    create_run_context,
    ensure_writable_run_layout,
    load_run_context,
    mark_run_status,
    targets_from_manifest,
)
from vulnclaw.target_state.store import (
    SessionRestoreResult,
    apply_target_state_to_agent,
    build_task_session_summary,
    import_legacy_target_state,
    legacy_target_state_exists,
    save_target_state,
)
from vulnclaw.targets import Target, build_targets

logger = logging.getLogger(__name__)


@dataclass
class OrchestratorRunResult:
    restore_result: SessionRestoreResult
    summary: dict[str, Any]
    run_context: RunContext | None = None
    status: str = "completed"
    exit_code: int = 0
    audited_session: SessionState | None = None


async def run_agent_task(
    *,
    agent: AgentCore,
    command: str,
    target: str,
    resume: bool = True,
    snapshot_id: Optional[str] = None,
    run_name: Optional[str] = None,
    resume_run_name: Optional[str] = None,
    runs_dir: Optional[str] = None,
    additional_targets: Optional[list[str]] = None,
    target_type: Optional[str] = None,
    mount: bool = False,
    repair: bool = False,
    force_fresh: bool = False,
    no_import: bool = False,
    continuous: bool = False,
    poc_verify: bool | None = None,
    before_restore: Optional[Callable[[SessionRestoreResult | None], None]] = None,
    on_restored: Optional[Callable[[SessionRestoreResult], None]] = None,
    on_legacy_import: Optional[Callable[[SessionRestoreResult], None]] = None,
    runner: Callable[[AgentCore], Awaitable[Any]],
) -> OrchestratorRunResult:
    """Run a shared task flow with optional restore and summary generation."""
    targets = build_targets(
        target,
        additional_targets,
        target_type=target_type,
        mount=mount,
    )
    legacy_read_only = bool(
        no_import
        and resume
        and not resume_run_name
        and legacy_target_state_exists(targets[0].raw, targets[0])
    )
    run_context = (
        None
        if legacy_read_only
        else _resolve_run_context(
            command=command,
            targets=targets,
            run_name=run_name,
            resume_run_name=resume_run_name,
            runs_dir=runs_dir,
            config=getattr(agent, "config", None),
            repair=repair,
            force_fresh=force_fresh,
        )
    )
    if resume_run_name:
        if run_context is None:
            raise RunCorruptError(Path("."), "resume run context was not loaded")
        # First writable resume moves a legacy flat run into the target/time
        # layout before any state is written.
        run_context = ensure_writable_run_layout(
            run_context, error_limit=agent.config.session.persistence_error_limit
        )
        targets = _targets_from_run_context(run_context)
        target = targets[0].raw

    prior_run_id = agent.session_state.run_id
    prior_run_dir = agent.session_state.resume_meta.get("run_dir")
    try:
        # Establish writable ownership and restore the health counter before any
        # operation that can consume the save-failure budget.
        if run_context is not None and not legacy_read_only:
            _bind_run_context(agent, run_context)
            store = getattr(agent, "evidence_store", None)
            if store is not None:
                store.check_writable_execution_allowed()

        restore_result = None
        if before_restore is not None:
            before_restore(None)

        primary_target = targets[0]
        if resume_run_name:
            if run_context is None:
                raise RunCorruptError(Path("."), "resume run context was not loaded")
            restore_result = apply_target_state_to_agent(
                agent,
                primary_target.raw,
                snapshot_id=snapshot_id,
                run_context=run_context,
                target_model=primary_target,
            )
            if restore_result.restored and on_restored is not None:
                on_restored(restore_result)
        elif resume:
            if legacy_target_state_exists(primary_target.raw, primary_target):
                if no_import:
                    restore_result = apply_target_state_to_agent(
                        agent,
                        primary_target.raw,
                        snapshot_id=snapshot_id,
                    )
                else:
                    if run_context is None:
                        raise RunCorruptError(Path("."), "run context was not created")
                    import_result = import_legacy_target_state(
                        primary_target.raw,
                        run_context=run_context,
                        target_model=primary_target,
                        command=command,
                        runtime=agent.runtime,
                    )
                    if import_result.restored and on_legacy_import is not None:
                        on_legacy_import(import_result)
                    restore_result = apply_target_state_to_agent(
                        agent,
                        primary_target.raw,
                        snapshot_id=snapshot_id,
                        run_context=run_context,
                        target_model=primary_target,
                    )
            else:
                restore_result = apply_target_state_to_agent(
                    agent, primary_target.raw, snapshot_id=snapshot_id
                )
            if restore_result.restored and on_restored is not None:
                on_restored(restore_result)
        else:
            agent.context.state.target = primary_target.raw
            restore_result = SessionRestoreResult(
                restored=False,
                target=primary_target.raw,
                phase=getattr(agent.context.state.phase, "value", str(agent.context.state.phase)),
                snapshot_id=snapshot_id or "",
                preview={"target": primary_target.raw},
            )

        checkpoint = (
            _install_checkpoint_hook(agent, command, run_context, targets)
            if run_context is not None
            else lambda _reason: None
        )
        # Restore may have replaced agent.context.state; bind the store and scope
        # again to the restored AgentState.
        if run_context is not None and not legacy_read_only:
            source_run_id = agent.session_state.run_id if restore_result.restored else prior_run_id
            source_run_dir = (
                agent.session_state.resume_meta.get("run_dir")
                if restore_result.restored else prior_run_dir
            )
            _bind_run_context(agent, run_context)
            migrator = getattr(agent, "migrate_legacy_state", None)
            if callable(migrator):
                if source_run_dir and Path(source_run_dir).resolve() != run_context.run_dir:
                    migrator(source_run_dir=source_run_dir, source_run_id=source_run_id)
                else:
                    migrator()
            agent.session_state.resume_meta.update(
                run_dir=str(run_context.run_dir), run_name=run_context.run_name
            )
        checkpoint("run_start")

        def save_failure(reason: str, status: str, exit_code: int, message: str = "") -> None:
            # Cleanup must not mask the exception that stopped execution.
            try:
                checkpoint(reason)
            except Exception:
                logger.exception("could not checkpoint failed run (%s)", reason)
            if run_context is not None:
                try:
                    mark_run_status(run_context, status, exit_code=exit_code, message=message)
                except Exception:
                    logger.exception("could not mark run status as %s", status)

        status = "completed"
        exit_code = 0
        audited_session = None
        try:
            await runner(agent)
            # Persist the final boundary before findings/evidence snapshots read
            # from the store.  A pending save failure here consumes the run budget.
            if run_context is not None:
                store = getattr(agent, "evidence_store", None)
                if store is not None:
                    await asyncio.to_thread(store.flush_pending, boundary_id="run_complete")
                    await asyncio.to_thread(store.flush_index)
            from vulnclaw.findings.types import require_assessed
            from vulnclaw.findings.verification import verify_findings

            # Persistent cycles already audit before their summaries and reports.
            # Do not immediately retry an incomplete audit from the final cycle.
            if command == "persistent" or continuous:
                try:
                    require_assessed(agent.session_state)
                except ValueError:
                    audited_session = await verify_findings(
                        agent.session_state, config=agent.config, poc_verify=poc_verify,
                        run_root=run_context.run_dir if run_context is not None else None,
                    )
                else:
                    audited_session = agent.session_state.model_copy(deep=True)
            else:
                audited_session = await verify_findings(
                    agent.session_state, config=agent.config, poc_verify=poc_verify,
                    run_root=run_context.run_dir if run_context is not None else None,
                )
            checkpoint("run_complete")
            if run_context is not None:
                mark_run_status(run_context, "completed", exit_code=0)
                _schedule_completed_run_distillation(agent, run_context, primary_target)
        except KeyboardInterrupt:
            status = "interrupted"
            exit_code = 130
            save_failure("interrupt", status, exit_code)
        except asyncio.CancelledError:
            status = "interrupted"
            exit_code = 130
            save_failure("cancelled", status, exit_code)
            raise
        except Exception as exc:
            status = "failed"
            exit_code = 1
            save_failure("failed", status, exit_code, str(exc))
            raise

        summary = _build_summary(
            agent=agent,
            command=command,
            restore_result=restore_result,
            run_context=run_context,
            status=status,
            exit_code=exit_code,
        )
        return OrchestratorRunResult(
            restore_result=restore_result or SessionRestoreResult(target=target),
            summary=summary,
            run_context=run_context,
            status=status,
            exit_code=exit_code,
            audited_session=audited_session,
        )
    finally:
        _release_run_binding(agent)
        agent.session_state.set_checkpoint_callback(None)


def _schedule_completed_run_distillation(
    agent: AgentCore,
    run_context: RunContext,
    target: Target,
) -> None:
    """Launch lesson distillation after persistence; never affect run completion."""

    if not agent.config.session.experience_enabled:
        return
    try:
        from vulnclaw.agent.distiller import (
            RunArtifacts,
            configured_distiller,
            schedule_run_distillation,
        )
        from vulnclaw.config.token_provider import has_llm_credentials
        from vulnclaw.feedback import feedback_for_distillation
        from vulnclaw.kb.experience import ExperienceStore

        if not has_llm_credentials(agent.config.llm):
            run_context.append_event("distillation_skipped", {"reason": "missing_llm_credentials"})
            return
        artifacts = RunArtifacts.from_session(
            run_context.run_name,
            agent.session_state,
            target_key=target.target_id,
            feedback=feedback_for_distillation(run_context.run_dir),
        )
        schedule_run_distillation(
            artifacts=artifacts,
            llm=configured_distiller(agent.config),
            store=ExperienceStore(),
            run_context=run_context,
        )
    except Exception as exc:
        # Completion is already durable. Any learning failure is diagnostic-only.
        try:
            run_context.append_event("distillation_failed", {"error": type(exc).__name__})
        except Exception:
            pass


def _resolve_run_context(
    *,
    command: str,
    targets: list[Target],
    run_name: str | None,
    resume_run_name: str | None,
    runs_dir: str | None,
    config: Any,
    repair: bool,
    force_fresh: bool,
) -> RunContext:
    if resume_run_name:
        try:
            return load_run_context(
                resume_run_name,
                runs_dir=runs_dir,
                config=config,
                repair=repair,
            )
        except RunCorruptError:
            if not force_fresh:
                raise
    return create_run_context(
        command=command,
        targets=targets,
        runs_dir=runs_dir,
        config=config,
        run_name=run_name,
    )


def _bind_run_context(agent: AgentCore, run_context: RunContext) -> None:
    binder = getattr(agent, "bind_run_context", None)
    if callable(binder):
        binder(run_context)


def _release_run_binding(agent: AgentCore) -> None:
    releaser = getattr(agent, "release_run_binding", None)
    if callable(releaser):
        releaser()


def _targets_from_run_context(run_context: RunContext) -> list[Target]:
    targets = targets_from_manifest(run_context.manifest, run_dir=run_context.run_dir)
    if not targets:
        raise RunCorruptError(run_context.run_dir, "run.json targets[] is empty")
    run_context.targets = targets
    return targets


def _install_checkpoint_hook(
    agent: AgentCore,
    command: str,
    run_context: RunContext,
    targets: list[Target],
) -> Callable[[str], None]:
    primary_target = targets[0]
    secondary_targets = targets[1:]
    checkpointing = False
    secondaries_seeded = False

    def _seed_secondary_targets() -> None:
        """Initialize a state file for every non-primary manifest target.

        The agent loop only drives the primary target, but a multi-target run
        lists every target in ``run.json`` and ``validate_run_context`` requires
        a ``current.json`` per entry. Without this, ``--resume-run`` rejects the
        run as corrupt. Seed a fresh session for any secondary target that has
        no state yet so the whole manifest stays resumable.

        The agent loop never drives these targets in this run, so the seed
        writes only the run-local ``current.json`` and does not touch the global
        per-target index mirror. That keeps a plain ``load_target_state()`` /
        default resume for the target pointed at whatever real state it already
        had, instead of shadowing it with this empty placeholder snapshot.
        """
        for target in secondary_targets:
            if run_context.state_path(target).exists():
                continue
            save_target_state(
                target.raw,
                SessionState(target=target.raw),
                command=command,
                runtime=agent.runtime,
                run_context=run_context,
                target_model=target,
                checkpoint_reason="seed",
                merge_existing=False,
                update_index=False,
            )

    def save_checkpoint(reason: str) -> None:
        nonlocal secondaries_seeded
        if not secondaries_seeded:
            _seed_secondary_targets()
            secondaries_seeded = True
        state = agent.session_state
        state.target = state.target or primary_target.raw
        checkpoint_state = getattr(agent, "checkpoint_evidence_state", None)
        if callable(checkpoint_state):
            checkpoint_state()
        save_target_state(
            primary_target.raw,
            state,
            command=command,
            runtime=agent.runtime,
            run_context=run_context,
            target_model=primary_target,
            checkpoint_reason=reason,
            merge_existing=False,
        )

    def checkpoint(reason: str) -> None:
        nonlocal checkpointing
        if checkpointing:
            return
        checkpointing = True
        try:
            store = getattr(agent, "evidence_store", None)
            if store is not None:
                store.flush_index()
                store.save_checkpoint(
                    lambda: save_checkpoint(reason), operation_id=reason,
                    required=reason == "run_start",
                )
            else:
                save_checkpoint(reason)
        finally:
            checkpointing = False

    agent.session_state.set_checkpoint_callback(lambda _state, reason: checkpoint(reason))
    return checkpoint


def _build_summary(
    *,
    agent: AgentCore,
    command: str,
    restore_result: SessionRestoreResult | None,
    run_context: RunContext | None,
    status: str,
    exit_code: int,
) -> dict[str, Any]:
    base = build_task_session_summary(
        agent.session_state,
        command=command,
        restored=bool(restore_result and restore_result.restored),
        snapshot_id=restore_result.snapshot_id if restore_result else "",
    )
    completion = build_completion_summary(
        context=run_context,
        session=agent.session_state,
        command=command,
        restored=bool(restore_result and restore_result.restored),
        snapshot_id=restore_result.snapshot_id if restore_result else "",
        status=status,
        exit_code=exit_code,
    )
    return {**base, **completion}
