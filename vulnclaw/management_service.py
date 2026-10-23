"""Explicit management operations for the application session and headless CLI."""

from __future__ import annotations

import asyncio
import shlex
from typing import Any

from vulnclaw import experience_service
from vulnclaw.config_service import config_document, update_config
from vulnclaw.i18n import _, init_i18n

MANAGEMENT_OPERATIONS = frozenset({
    "config.document", "config.update", "language.set", "experience.list", "experience.show",
    "experience.approve", "experience.reject", "experience.edit", "learn", "feedback",
})


def adapter_operation(name: str, text: str) -> tuple[str, dict[str, Any]]:
    """Parse the retained REPL business commands in Python, beside InputRequest."""
    tokens = shlex.split(text)
    if name == "config":
        if tokens:
            raise ValueError(f"/{name} opens a form and does not accept arguments")
        return "config.document", {}
    if name == "language":
        if len(tokens) != 1:
            raise ValueError("/language requires en, zh or auto")
        return "language.set", {"language": tokens[0]}
    if name == "experience":
        action = tokens.pop(0) if tokens else "list"
        action = "list" if action == "review" else action
        if action == "list":
            if not tokens:
                return "experience.list", {}
            if len(tokens) == 2 and tokens[0] == "--status":
                return "experience.list", {"status": tokens[1]}
            raise ValueError("/experience list accepts --status pending|approved|rejected|all")
        if action not in {"show", "approve", "reject", "edit"} or not tokens:
            raise ValueError("/experience expects list|show|approve|reject|edit and a lesson ID")
        arguments: dict[str, Any] = {"lesson_id": tokens.pop(0)}
        while tokens:
            flag = tokens.pop(0)
            if action != "edit" or flag not in {"--context", "--lesson"} or not tokens:
                raise ValueError("experience edit accepts --context and --lesson")
            arguments[flag[2:]] = tokens.pop(0)
        return f"experience.{action}", arguments
    if name == "learn":
        if len(tokens) != 1:
            raise ValueError("/learn requires a run name")
        return "learn", {"run_name": tokens[0]}
    if name == "feedback":
        if len(tokens) < 3:
            raise ValueError("/feedback requires run name, rating and notes")
        return "feedback", {"run_name": tokens[0], "rating": int(tokens[1]), "notes": " ".join(tokens[2:])}
    raise ValueError(f"unsupported management command: {name}")


async def execute_management(application: Any, operation: str, args: dict[str, Any]) -> dict[str, Any]:
    config = application.config
    if operation == "config.document":
        return config_document(config)
    if operation == "config.update":
        changes = args.get("changes")
        updated = update_config(changes, config=config)
        language_changed = config.session.language != updated.session.language
        if all(path == "terminal" or path.startswith("terminal.") for path in changes):
            # Appearance edits must not reset the active model client or trim its context.
            config.terminal = updated.terminal
        else:
            await application.apply_config(updated)
        if language_changed:
            init_i18n(config=updated)
        return {"message": "Configuration saved.", **config_document(updated)}
    if operation == "language.set":
        language = args.get("language")
        if language not in {"en", "zh", "auto"}:
            raise ValueError("language must be en, zh or auto")
        updated = update_config({"session.language": language}, config=config)
        await application.apply_config(updated)
        init_i18n(lang=language if language != "auto" else None, config=updated)
        return {
            "language": language,
            "message": _("tui.language_switched", lang=_(f"tui.language_{language}")),
        }
    if operation.startswith("experience."):
        identifier = str(args.get("lesson_id") or "")
        if operation == "experience.list":
            result = experience_service.list_lessons(args.get("status", "pending"))
        elif operation == "experience.show":
            result = experience_service.get_lesson(identifier)
        elif operation in {"experience.approve", "experience.reject"}:
            result = experience_service.set_lesson_status(
                identifier, "approved" if operation.endswith("approve") else "rejected",
            )
        else:
            result = experience_service.edit_lesson(
                identifier, context=args.get("context"), lesson=args.get("lesson"),
            )
        if not result.ok:
            raise ValueError(result.message)
        return {"message": result.message, **result.data}
    if operation in {"learn", "feedback"}:
        name = str(args.get("run_name") or "")
        if not name:
            raise ValueError("run_name is required")
        if operation == "learn":
            result = await asyncio.to_thread(
                experience_service.distill_run, name, config=config, runs_dir=args.get("runs_dir"),
            )
        else:
            result = experience_service.save_run_feedback(
                name, rating=int(args.get("rating", 0)), notes=str(args.get("notes") or ""),
                config=config, runs_dir=args.get("runs_dir"),
            )
        if not result.ok:
            raise ValueError(result.message)
        return {"message": result.message, **result.data}
    raise ValueError(f"unsupported management operation: {operation}")
