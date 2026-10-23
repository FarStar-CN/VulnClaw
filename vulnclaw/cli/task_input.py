"""Map explicit CLI fields and one JSON document into the shared task DTO."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer

from vulnclaw import headless
from vulnclaw.cli.output import CommandError
from vulnclaw.task_service import TaskCreateRequest, TaskOptions

ROOT_ALIASES = {
    "snapshot": "snapshot_id",
    "resume_run": "resume_run_name",
    "additional_targets": "additional_targets",
}


def read_input_json(path: str) -> dict[str, Any]:
    try:
        text = (
            sys.stdin.read() if path == "-" else Path(path).expanduser().read_text(encoding="utf-8")
        )
        value = json.loads(text)
    except (OSError, ValueError) as exc:
        raise CommandError("invalid_input_json", f"Could not read input JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise CommandError("invalid_input_json", "input JSON must be an object")
    return value


def explicit(ctx: typer.Context, name: str) -> bool:
    source = ctx.get_parameter_source(name)
    return source is not None and source.name == "COMMANDLINE"


def merge_task_fields(
    ctx: typer.Context,
    payload: dict[str, Any],
    expected: str,
    *,
    skip: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    values = ctx.params
    payload = dict(payload)
    if "command" in payload and payload["command"] != expected:
        raise CommandError("conflicting_input", f"input command must be {expected}")
    payload.setdefault("command", expected)
    if not isinstance(payload.get("options", {}), dict):
        raise CommandError("invalid_input_json", "task options must be an object")
    options = dict(payload.get("options") or {})
    payload["options"] = options
    for name, value in values.items():
        if name in skip or not explicit(ctx, name):
            continue
        field = ROOT_ALIASES.get(name, name)
        if field in TaskCreateRequest.model_fields and field not in {"command", "options"}:
            destination = payload
        elif field in TaskOptions.model_fields:
            destination = options
        elif name == "no_report":
            destination, field, value = options, "auto_report", False
        elif name == "output":
            destination, field = options, "report_output"
        else:
            continue
        if field in destination:
            raise CommandError(
                "conflicting_input", f"{field} was supplied in both CLI arguments and input JSON"
            )
        destination[field] = value
    return payload


def task_from_context(
    ctx: typer.Context, config: Any
) -> tuple[TaskCreateRequest, headless.ScanProfile]:
    values = ctx.params
    entry = ctx.info_name or "run"
    payload = read_input_json(values["input_json"]) if values.get("input_json") else {}
    payload = merge_task_fields(ctx, payload, "run" if entry == "solve" else entry)
    options = payload["options"]
    if not payload.get("target"):
        raise CommandError(
            "requires_input",
            "Provide a target argument or input JSON containing target.",
            status="requires_input",
        )
    if entry == "solve":
        from vulnclaw.i18n import _

        goal = values.get("goal") or _("cli.default_goal")
        payload.setdefault("prompt", _("cli.task_prompt", target=payload["target"], goal=goal))
        options.setdefault("engine", "solve")
        options.setdefault("auto_report", False)
        for field in ("max_steps", "max_directions", "max_tool_rounds"):
            options.setdefault(field, values[field])
    task = TaskCreateRequest.model_validate(payload)
    scan_mode = values.get("scan_mode", headless.DEFAULT_SCAN_MODE)
    if scan_mode not in headless.SCAN_MODES:
        raise CommandError(
            "invalid_arguments", f"--scan-mode must be one of: {', '.join(headless.SCAN_MODES)}"
        )
    if values.get("scope_mode", headless.DEFAULT_SCOPE_MODE) not in headless.SCOPE_MODES:
        raise CommandError("invalid_arguments", "--scope-mode must be auto or full")
    if values.get("fail_on", headless.DEFAULT_FAIL_ON) not in headless.FAIL_ON_MODES:
        raise CommandError("invalid_arguments", "--fail-on must be verified, any or never")
    profile = headless.resolve_scan_profile(
        config,
        scan_mode,
        **{
            field: getattr(task.options, field)
            for field in (
                "max_steps",
                "max_directions",
                "max_tool_rounds",
                "max_parallel",
                "max_rounds",
            )
        },
    )
    if task.command == "run":
        defaults = profile.as_dict()
        defaults.pop("scan_mode")
        task = TaskCreateRequest.model_validate(
            {
                **task.model_dump(exclude_unset=True),
                "options": {
                    **defaults,
                    **task.options.model_dump(exclude_unset=True),
                },
            }
        )
    return task, profile
