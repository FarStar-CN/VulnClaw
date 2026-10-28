"""Translate structured parser and service failures into actionable feedback."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError


def error_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(map(str, error['loc']))}: {error['msg']}"
            for error in exc.errors(include_input=False, include_url=False)
        )
    return str(exc)


def interaction_feedback(status: str) -> dict[str, Any]:
    name = "approval_required" if status == "requires_approval" else "waiting_for_input"
    return {
        "issues": [
            {
                "code": status,
                "path": ["interaction"],
                "message": "Continue this interaction in a live session backend.",
            }
        ],
        "next_actions": [
            {
                "action": "inspect_protocol",
                "describe_argv": [
                    "vulnclaw",
                    "describe",
                    "--protocol",
                    name,
                ],
            }
        ],
    }


def build_feedback(exc: Exception, ctx=None) -> dict[str, Any]:
    from vulnclaw.cli.discovery import command_path, parameter_document

    path = command_path(ctx) if ctx is not None else []
    actions = getattr(exc, "next_actions", None) or [
        {"action": "inspect_command", "describe_argv": ["vulnclaw", "describe", *path]}
    ]
    issues = []
    if isinstance(exc, ValidationError):
        for error in exc.errors(include_input=False, include_url=False):
            kind = error["type"]
            constraints = {
                key: value
                for key, value in error.get("ctx", {}).items()
                if isinstance(value, (str, int, float, bool, list, tuple))
            }
            issues.append(
                {
                    "code": "missing_field" if kind == "missing" else "invalid_value",
                    "path": list(error["loc"]),
                    "message": error["msg"],
                    **({"constraints": constraints} if constraints else {}),
                }
            )
    else:
        code = getattr(exc, "code", "invalid_arguments")
        parameter = getattr(exc, "param", None)
        field_path = getattr(exc, "field_path", None)
        constraints = getattr(exc, "constraints", None)
        if parameter is not None:
            document = parameter_document(parameter)
            field_path = [parameter.name]
            constraints = {
                key: document[key] for key in ("choices", "constraints", "type") if key in document
            }
            if type(exc).__name__ == "MissingParameter":
                code = "missing_field"
        elif hasattr(exc, "option_name"):
            field_path = [exc.option_name]
        elif ctx is not None and hasattr(ctx.command, "commands"):
            constraints = constraints or {"choices": sorted(ctx.command.commands)}
            field_path = field_path or ["command"]
        code = {
            "requires_input": "missing_field",
            "invalid_config_key": "unknown_config_key",
            "invalid_input_json": "invalid_json",
            "invalid_format": "invalid_value",
        }.get(code, code)
        if field_path == ["llm", "credentials"]:
            code = "missing_credentials"
            actions = [
                {
                    "action": "provide_credentials",
                    "environment_variables": ["VULNCLAW_LLM_API_KEY"],
                    "example_argv": ["vulnclaw", "login", "--token-file", "<token_bundle.json>"],
                }
            ]
        issues.append(
            {
                "code": code,
                "path": field_path or ["input"],
                "message": str(exc),
                **({"constraints": constraints} if constraints else {}),
            }
        )
    if ctx is not None and any(issue["code"] == "missing_field" for issue in issues):
        from vulnclaw.cli.discovery import describe_command

        example = describe_command(ctx.find_root().command, path).get("example_argv")
        if example:
            actions = [{"action": "provide_arguments", "example_argv": example}, *actions]
    return {"issues": issues, "next_actions": actions}
