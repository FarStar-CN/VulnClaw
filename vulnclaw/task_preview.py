"""Read-only preparation of the native TUI's editable task overview."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from vulnclaw.config.domain_models import (
    ACTION_PRESETS,
    TEST_ACTIONS,
    TaskConstraints,
    complete_action_lists,
    validate_action_constraints,
)
from vulnclaw.input_service import InputRequest, parse_input
from vulnclaw.task_service import prepare_task, task_request_from_payload


def preview_task(arguments: dict, *, defaults: dict, config: Any) -> dict:
    """Resolve one draft without installing a target, running tools or saving config."""
    try:
        parsed = parse_input(InputRequest(**arguments))
        if parsed.kind != "task":
            raise ValueError("The overview requires a task command")
        request = task_request_from_payload(parsed.task, defaults=defaults)
    except ValidationError as exc:
        errors = {}
        for error in exc.errors():
            location = [str(item) for item in error["loc"] if item != "options"]
            errors.setdefault(location[0] if location else "target", error["msg"])
        return {"errors": errors}
    except (TypeError, ValueError) as exc:
        return {"errors": {"target": str(exc)}}

    if request.command == "codescan":
        task = prepare_task(request)
        return {
            "task": request.model_dump(mode="json", exclude_none=True),
            "constraints": task.constraints.model_dump(mode="json"),
            "actions": [], "presets": [], "defaults": {}, "errors": {},
        }

    options = request.options
    if "continuous" not in options.model_fields_set and defaults.get("command") == "persistent":
        options = options.model_copy(update={"continuous": True})
    selection = complete_action_lists(TaskConstraints(
        allowed_actions=options.allow_actions or [], blocked_actions=options.block_actions or [],
    ))
    session = config.session
    values = {
        "poc_verify": session.poc_verify_enabled,
        "auto_report": session.persistent_auto_report if options.continuous else True,
    }
    report_explicit = options.auto_report is not None
    updates = {key: value for key, value in values.items() if getattr(options, key) is None}
    updates.update(allow_actions=selection.allowed_actions, block_actions=selection.blocked_actions)
    if options.continuous:
        for key, value in (
            ("rounds_per_cycle", session.persistent_rounds_per_cycle),
            ("max_cycles", session.persistent_max_cycles),
        ):
            if getattr(options, key) is None:
                updates[key] = value
    options = options.model_copy(update=updates)
    request = request.model_copy(update={"options": options})
    parsed_target = None
    if request.command != "codescan" and not request.mount:
        try:
            parsed_target = urlsplit(
                request.target if "://" in request.target else f"//{request.target}"
            )
            host = parsed_target.hostname or ""
            port = parsed_target.port
            if not host or any(char.isspace() for char in host):
                raise ValueError("Enter a valid target URL, hostname or IP address")
            if parsed_target.scheme and parsed_target.scheme not in {"http", "https"}:
                raise ValueError("Target URLs must use HTTP or HTTPS")
        except ValueError as exc:
            return {"errors": {"target": str(exc)}}
    try:
        task = prepare_task(request)
    except ValueError as exc:
        return {"errors": {"allow_actions": str(exc)}}
    constraints = task.constraints
    errors = {}
    if constraints.allowed_ports and not set(constraints.allowed_ports).difference(
        constraints.blocked_ports
    ):
        errors["blocked_ports"] = "All allowed ports are blocked"
    if parsed_target is not None:
        if constraints.allowed_hosts and host.lower() not in constraints.allowed_hosts:
            errors["only_host"] = "The target host is outside Allow hosts"
        if host.lower() in constraints.blocked_hosts:
            errors["blocked_host"] = "The target host is blocked"
        if parsed_target.scheme:
            port = port or (443 if parsed_target.scheme == "https" else 80)
        if port is not None and constraints.allowed_ports and port not in constraints.allowed_ports:
            errors["allowed_ports"] = "The target port is outside Allow ports"
        if port in constraints.blocked_ports:
            errors["blocked_ports"] = "The target port is blocked"
        if parsed_target.path in constraints.blocked_paths:
            errors["blocked_path"] = "The target path is blocked"
        if (parsed_target.path not in {"", "/"} and constraints.allowed_paths
                and parsed_target.path not in constraints.allowed_paths):
            errors["only_path"] = "The target path is outside Allow paths"
    for field, paths in (
        ("only_path", constraints.allowed_paths), ("blocked_path", constraints.blocked_paths),
    ):
        if any(not path.startswith("/") for path in paths):
            errors[field] = "Paths must start with /; separate multiple paths with commas"
    if constraints.allowed_paths and not set(constraints.allowed_paths).difference(
        constraints.blocked_paths
    ):
        errors["blocked_path"] = "All allowed paths are blocked"
    actions = [
        action for action in TEST_ACTIONS
        if validate_action_constraints(action, constraints) is None
    ]
    if not actions:
        errors["allow_actions"] = "Select at least one allowed action"
    if options.poc_verify and "exploit" not in actions:
        errors["poc_verify"] = "PoC verification requires the exploit action"
    return {
        "task": request.model_dump(mode="json", exclude_none=True),
        "constraints": constraints.model_dump(mode="json"),
        "actions": actions,
        "presets": [{"name": name, "actions": list(actions)}
                    for name, actions in ACTION_PRESETS.items()],
        "defaults": {
            "rounds_per_cycle": session.persistent_rounds_per_cycle,
            "max_cycles": session.persistent_max_cycles,
            "auto_report": True,
            "continuous_auto_report": session.persistent_auto_report,
            "auto_report_explicit": report_explicit,
        },
        "errors": errors,
    }
