"""Shared, structured experience review, distillation and feedback operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class OpResult:
    """Outcome of a self-learning-loop operation.

    ``ok`` reports success; ``message`` is the message or rich object the
    caller should print. ``ok=False`` marks a user-facing failure (unknown
    lesson, missing credentials, unfinished run, invalid input).
    """

    ok: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)


def _experience_store() -> Any:
    """Create the human-gated lesson store only when a command needs it."""
    from vulnclaw.kb.experience import ExperienceStore

    return ExperienceStore()


def list_lessons(status: str = "pending") -> OpResult:
    """List reviewable lessons, retaining the pending-only command default."""
    from vulnclaw.kb.experience import LessonStatus

    if not isinstance(status, str) or status not in {item.value for item in LessonStatus} | {"all"}:
        return OpResult(False, "[!] status must be pending, approved, rejected or all.")
    try:
        lessons = _experience_store().list_by_status(None if status == "all" else status)
    except OSError as exc:
        return OpResult(False, f"[!] Could not read the experience store: {exc}")
    message = (f"{len(lessons)} {status} lesson(s)." if lessons
               else f"No {status} experience lessons.")
    return OpResult(True, message,
                    {"status": status, "lessons": [item.model_dump(mode="json") for item in lessons]})


def get_lesson(lesson_id: str) -> OpResult:
    """Show full lesson text and evidence provenance for one lesson."""
    try:
        item = _experience_store().get(lesson_id)
    except ValueError:
        # A malformed id (path separators, empty string) must stay REPL-safe.
        return OpResult(False, f"[!] Lesson not found: {lesson_id}")
    except OSError as exc:
        return OpResult(False, f"[!] Could not read the experience store: {exc}")
    if item is None:
        return OpResult(False, f"[!] Lesson not found: {lesson_id}")

    return OpResult(True, f"Lesson {item.id}", {"lesson": item.model_dump(mode="json")})


def set_lesson_status(lesson_id: str, status: str) -> OpResult:
    """Apply one human review decision (``approved`` or ``rejected``)."""
    try:
        store = _experience_store()
        item = store.approve(lesson_id) if status == "approved" else store.reject(lesson_id)
    except ValueError:
        return OpResult(False, f"[!] Lesson not found: {lesson_id}")
    except OSError as exc:
        # An unwritable or full KB must not unwind the interactive REPL.
        return OpResult(False, f"[!] Could not write the experience store: {exc}")
    if item is None:
        return OpResult(False, f"[!] Lesson not found: {lesson_id}")
    return OpResult(True, f"[+] Lesson {item.id} marked {item.status.value}.", {"lesson": item.model_dump(mode="json")})


def edit_lesson(
    lesson_id: str,
    *,
    context: Optional[str] = None,
    lesson: Optional[str] = None,
) -> OpResult:
    """Amend context and/or lesson text without changing provenance or status."""
    if context is None and lesson is None:
        return OpResult(False, "[!] Provide new context and/or lesson text.")
    try:
        item = _experience_store().update(lesson_id, context=context, lesson=lesson)
    except ValueError as exc:
        return OpResult(False, f"[!] Invalid lesson update: {exc}")
    except OSError as exc:
        return OpResult(False, f"[!] Could not write the experience store: {exc}")
    if item is None:
        return OpResult(False, f"[!] Lesson not found: {lesson_id}")
    return OpResult(True, f"[+] Lesson {item.id} updated.", {"lesson": item.model_dump(mode="json")})


def distill_run(
    run_name: str,
    *,
    config: Any,
    runs_dir: Optional[str] = None,
) -> OpResult:
    """Distill a completed run into pending lessons (the ``learn`` command)."""
    import json

    from vulnclaw.agent.context import SessionState
    from vulnclaw.agent.distiller import (
        RunArtifacts,
        configured_distiller,
        persist_distilled_lessons,
    )
    from vulnclaw.config.token_provider import has_llm_credentials
    from vulnclaw.feedback import feedback_for_distillation
    from vulnclaw.kb.experience import ExperienceStore
    from vulnclaw.run_context import RunContextError, load_run_context

    if not has_llm_credentials(config.llm):
        return OpResult(False, "[!] Configure LLM credentials first (api_key or auth_mode).")

    run_context = None
    try:
        run_context = load_run_context(run_name, runs_dir=runs_dir, config=config)
        state_data = json.loads(run_context.state_path().read_text(encoding="utf-8"))
        session = SessionState.model_validate(state_data)
        target = run_context.target_manifest()
        artifacts = RunArtifacts.from_session(
            run_context.run_name,
            session,
            target_key=str(target.get("target_id") or ""),
            feedback=feedback_for_distillation(run_context.run_dir),
        )
        lessons = persist_distilled_lessons(
            artifacts,
            configured_distiller(config),
            ExperienceStore(),
        )
        run_context.append_event("distillation_completed", {"lessons": len(lessons), "manual": True})
    except (OSError, ValueError, json.JSONDecodeError, RunContextError) as exc:
        return OpResult(False, f"[!] Could not distill run {run_name}: {exc}")
    except Exception as exc:
        # Keep a run-local audit event, but surface only a concise error type.
        if run_context is not None:
            try:
                run_context.append_event(
                    "distillation_failed", {"error": type(exc).__name__, "manual": True}
                )
            except Exception:
                pass
        return OpResult(False, f"[!] Distillation failed: {type(exc).__name__}")

    return OpResult(
        True,
        f"[+] Distilled {len(lessons)} pending lesson(s) from run {run_context.run_name}.",
        {"run_name": run_context.run_name, "lessons": [item.model_dump(mode="json") for item in lessons]},
    )


def save_run_feedback(
    run: str,
    *,
    rating: int,
    notes: str,
    config: Any,
    runs_dir: Optional[str] = None,
) -> OpResult:
    """Attach or update an operator assessment for a completed run."""
    from vulnclaw.feedback import FeedbackError, save_feedback
    from vulnclaw.run_context import RunContextError, load_run_context

    try:
        run_context = load_run_context(run, runs_dir=runs_dir, config=config)
    except (RunContextError, ValueError, OSError) as exc:
        return OpResult(False, f"[!] Unable to load run '{run}': {exc}")

    status = str(run_context.manifest.get("status") or "")
    if status not in {"completed", "interrupted", "failed"}:
        return OpResult(
            False, f"[!] Run '{run}' is not finished (status: {status or 'unknown'})."
        )
    if not notes.strip():
        return OpResult(False, "[!] Feedback notes must not be empty.")

    try:
        saved = save_feedback(run_context.run_dir, rating=rating, notes=notes)
        # Notes can carry sensitive operational detail; only record the rating.
        run_context.append_event("feedback_updated", {"rating": saved.rating})
    except FeedbackError as exc:
        return OpResult(False, f"[!] Invalid feedback: {exc}")
    except OSError as exc:
        # feedback.json or events.jsonl unwritable: stay REPL-safe.
        return OpResult(False, f"[!] Could not save feedback for '{run}': {exc}")

    return OpResult(True, f"[+] Feedback saved for {run}: rating={saved.rating}/5", {"run_name": run, "rating": saved.rating})
