"""Describe the actual command tree and task inputs without executing callbacks."""

from __future__ import annotations

import shlex
from typing import Any

import typer

from vulnclaw.cli.output import CommandError, perform


def command_path(ctx) -> list[str]:
    path = []
    while ctx.parent is not None:
        path.append(ctx.info_name)
        ctx = ctx.parent
    return list(reversed(path))


def parameter_document(parameter) -> dict[str, Any]:
    kind = "option" if parameter.param_type_name == "option" else "argument"
    value_type = parameter.type
    name = value_type.name.split(" ", 1)[0]
    document = {
        "name": parameter.name,
        "kind": kind,
        "type": {
            "int": "integer",
            "integer": "integer",
            "float": "number",
            "boolean": "boolean",
        }.get(name, "string"),
        "required": parameter.required,
        "description": getattr(parameter, "help", None) or "",
    }
    if kind == "option":
        document["flags"] = parameter.opts + parameter.secondary_opts
    if not callable(parameter.default):
        document["default"] = parameter.default
    choices = getattr(value_type, "choices", None)
    if choices is not None:
        document["choices"] = list(choices)
    constraints = {
        key: getattr(value_type, attribute)
        for key, attribute in (("minimum", "min"), ("maximum", "max"))
        if getattr(value_type, attribute, None) is not None
    }
    if constraints:
        document["constraints"] = constraints
    if parameter.nargs != 1 or getattr(parameter, "multiple", False):
        document["items_type"] = document["type"]
        document["type"] = "array"
    return document


def _task_input(path: list[str]) -> dict[str, Any]:
    from vulnclaw.task_service import TASK_COMMANDS, TaskCreateRequest

    if len(path) == 1 and path[0] in TASK_COMMANDS | {"solve"}:
        expected = "run" if path[0] == "solve" else path[0]
        return {
            "input_json_schema": TaskCreateRequest.model_json_schema(),
            "input_requirements": [
                {
                    "field": "target",
                    "required": True,
                    "sources": ["argument:target", "input_json:target"],
                }
            ],
            "input_json_command": expected,
        }
    if path == ["network-scan"]:
        from vulnclaw.task_operations import NetworkScanRequest

        return {
            "input_json_schema": NetworkScanRequest.model_json_schema(),
            "input_requirements": [
                {
                    "field": "target",
                    "required": False,
                    "sources": [
                        "argument:target",
                        "input_json:task.target",
                        "connected_wifi_subnet",
                    ],
                }
            ],
        }
    if path == ["ask"]:
        return {
            "input_json_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            "input_requirements": [
                {"field": "text", "required": True, "sources": ["argument:text", "input_json:text"]}
            ],
        }
    return {}


def _summary(command, path: list[str]) -> str:
    if command.help or command.short_help:
        return (command.help or command.short_help).strip().split("\n")[0]
    from vulnclaw.config.cli_constants import COMMANDS

    for topic in COMMANDS:
        if topic.name == " ".join(path):
            return topic.summary
    return " ".join(path)


def _example(command, path: list[str]) -> list[str]:
    from vulnclaw.config.cli_constants import COMMANDS

    for topic in COMMANDS:
        if topic.name == " ".join(path):
            for example in topic.examples:
                if example.startswith("vulnclaw "):
                    return shlex.split(example)
    argv = ["vulnclaw", *path]
    for parameter in command.params:
        if parameter.required:
            if parameter.param_type_name == "option":
                argv.append(parameter.opts[0])
            argv.append(f"<{parameter.name}>")
    for requirement in _task_input(path).get("input_requirements", []):
        if requirement["required"] and f"<{requirement['field']}>" not in argv:
            argv.insert(len(path) + 1, f"<{requirement['field']}>")
    return argv


def group_document(command, path: list[str]) -> dict[str, Any]:
    operations = []
    for name, child in sorted(command.commands.items()):
        if child.hidden:
            continue
        child_path = [*path, name]
        required = [parameter.name for parameter in child.params if parameter.required]
        required.extend(
            item["field"]
            for item in _task_input(child_path).get("input_requirements", [])
            if item["required"] and item["field"] not in required
        )
        operations.append(
            {
                "name": name,
                "summary": _summary(child, child_path),
                "required": required,
                "example_argv": _example(child, child_path),
                "describe_argv": ["vulnclaw", "describe", *child_path],
            }
        )
    return {"kind": "command_group", "path": path, "operations": operations}


def describe_command(root, path: list[str]) -> dict[str, Any]:
    command = root
    for index, name in enumerate(path):
        children = getattr(command, "commands", {})
        if name not in children or children[name].hidden:
            raise CommandError(
                "invalid_arguments",
                "Unknown command path.",
                path=["command", *path[: index + 1]],
                constraints={"choices": sorted(children)},
                next_actions=[
                    {
                        "action": "inspect_command",
                        "describe_argv": ["vulnclaw", "describe", *path[:index]],
                    }
                ],
            )
        command = children[name]
    if hasattr(command, "commands"):
        return group_document(command, path)
    document = {
        "kind": "command",
        "path": path,
        "summary": _summary(command, path),
        "parameters": [parameter_document(parameter) for parameter in command.params],
        "example_argv": _example(command, path),
        **_task_input(path),
    }
    if path[:1] == ["config"] and path[1:] in (["get"], ["set"]):
        from vulnclaw.config.schema import VulnClawConfig

        document["configuration_schema"] = VulnClawConfig.model_json_schema()
        document["scope"] = "set saves configuration; environment variables override saved values."
    return document


def describe(
    ctx: typer.Context,
    command: list[str] = typer.Argument(
        None, help="Command path, or a protocol interaction/request name"
    ),
    protocol: bool = typer.Option(False, "--protocol", help="Describe the session protocol"),
    output_format: str = typer.Option("json", "--format"),
) -> None:
    """Discover commands, arguments and session reply contracts."""

    def operation():
        if output_format not in {"json", "jsonl"}:
            raise CommandError(
                "invalid_format",
                "--format must be json or jsonl",
                path=["format"],
                constraints={"choices": ["json", "jsonl"]},
            )
        path = command or []
        if protocol:
            from vulnclaw.interaction_contracts import describe_protocol
            from vulnclaw.session_protocol import CLIENT_MESSAGE_TYPES

            if (
                len(path) > 1
                or path
                and path[0] not in CLIENT_MESSAGE_TYPES | {"approval_required", "waiting_for_input"}
            ):
                raise CommandError(
                    "invalid_arguments",
                    "Unknown protocol name.",
                    path=["protocol"],
                    constraints={
                        "choices": sorted(
                            CLIENT_MESSAGE_TYPES | {"approval_required", "waiting_for_input"}
                        )
                    },
                )
            return describe_protocol(path[0] if path else None)
        return describe_command(ctx.find_root().command, path)

    perform(operation)
