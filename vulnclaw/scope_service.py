"""Pure validation and normalization of session scope drafts."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from vulnclaw.config.domain_models import (
    ACTION_PRESETS,
    TEST_ACTIONS,
    TaskConstraints,
    complete_action_lists,
    validate_action_constraints,
)
from vulnclaw.task_service import SCOPE_FIELDS, TaskOptions, build_scope_constraints


def scope_errors(constraints: TaskConstraints) -> dict[str, str]:
    errors = {}
    for allowed, blocked, field, label in (
        (constraints.allowed_ports, constraints.blocked_ports, "blocked_ports", "ports"),
        (constraints.allowed_paths, constraints.blocked_paths, "blocked_path", "paths"),
    ):
        if allowed and not set(allowed).difference(blocked):
            errors[field] = f"All allowed {label} are blocked"
    for field, paths in (
        ("only_path", constraints.allowed_paths),
        ("blocked_path", constraints.blocked_paths),
    ):
        if any(not path.startswith("/") for path in paths):
            errors[field] = "Paths must start with /; separate multiple paths with commas"
    return errors


def preview_scope(scope: dict[str, Any], *, target: str = "") -> dict[str, Any]:
    """Return a complete draft, preserving invalid input for field-level correction."""
    unknown = set(scope) - SCOPE_FIELDS
    if unknown:
        raise ValueError(f"unsupported scope field(s): {', '.join(sorted(unknown))}")
    draft = {key: scope.get(key) for key in sorted(SCOPE_FIELDS)}
    draft = {
        key: None if value == [] or isinstance(value, str) and not value.strip() else value
        for key, value in draft.items()
    }
    # Use one port-list representation, including drafts from legacy single-port defaults.
    if draft["allowed_ports"] is None and draft["only_port"] is not None:
        draft["allowed_ports"] = [draft["only_port"]]
    draft["only_port"] = None
    errors = {}
    try:
        options = TaskOptions.model_validate(draft)
    except ValidationError as exc:
        for error in exc.errors():
            errors.setdefault(str(error["loc"][0]), error["msg"])
        # Invalid fields remain editable; valid action choices still belong to the draft.
        options = TaskOptions.model_validate(
            {key: value for key, value in draft.items() if key not in errors}
        )
    normalized = {key: getattr(options, key) for key in sorted(SCOPE_FIELDS)}
    constraints = build_scope_constraints(target, normalized)
    normalized.update({key: draft[key] for key in errors})
    errors.update(scope_errors(constraints))
    selection = complete_action_lists(constraints)
    return {
        "scope": normalized,
        "target": target,
        "constraints": None if errors else constraints.model_dump(mode="json"),
        "actions": [
            action
            for action in TEST_ACTIONS
            if validate_action_constraints(action, constraints) is None
        ],
        "action_policy": {
            "allow_actions": selection.allowed_actions,
            "block_actions": selection.blocked_actions,
        },
        "available_actions": list(TEST_ACTIONS),
        "presets": [
            {"name": name, "actions": list(actions)} for name, actions in ACTION_PRESETS.items()
        ],
        "errors": errors,
    }
