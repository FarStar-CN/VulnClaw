"""One command grammar for terminal text and structured application callers."""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Any

from vulnclaw.task_service import SCOPE_FIELDS, TASK_COMMANDS, TaskCreateRequest, TaskOptions

COMMANDS = {
    "run": "Execute a task; --allow-actions limits actions; --continuous repeats cycles",
    "recon": "run --allow-actions recon",
    "scan": "run --allow-actions recon,scan",
    "exploit": "run --allow-actions recon,scan,exploit",
    "persistent": "run --continuous",
    "codescan": "Scan local source without an LLM",
    "clear": "Clear the display; keep the conversation",
    "new": "Start a new conversation; keep configuration and scope defaults",
    "permission": "Show or set read_only | ask | auto_review | full_access",
    "target": "Show or set the current target",
    "scope": "Show, update or reset scope defaults",
    "compact": "Compact conversation context",
    "report": "Export existing results; --kind security | replay; --output PATH",
    "tools": "List available tools",
    "help": "List commands",
    "config": "Configure the model provider",
    "language": "Set display language",
    "experience": "Manage experience records",
    "learn": "Learn from saved work",
    "feedback": "Record feedback",
    "think": "Show or hide reasoning output",
    "quit": "Exit the client",
}
ADAPTER_COMMANDS = frozenset({"config", "language", "experience", "learn", "feedback"})


@dataclass(frozen=True)
class InputRequest:
    text: str | None = None
    task: TaskCreateRequest | dict[str, Any] | None = None
    operation: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedInput:
    kind: str
    text: str = ""
    task: TaskCreateRequest | dict[str, Any] | None = None
    operation: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)


_OPTION_ALIASES = {
    "snapshot": "snapshot_id",
    "resume_run": "resume_run_name",
    "rounds": "rounds_per_cycle",
    "cycles": "max_cycles",
}
_BOOLEAN_OPTIONS = {"resume", "mount", "repair", "force_fresh", "no_import", "continuous"}


def parse_options(tokens: list[str]) -> dict[str, Any]:
    """Parse shell-quoted command arguments; Pydantic owns value validation."""
    root: dict[str, Any] = {}
    options: dict[str, Any] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("--"):
            raise ValueError(f"expected an option, got {token!r}")
        name, equal, inline = token[2:].partition("=")
        name = _OPTION_ALIASES.get(name.replace("-", "_"), name.replace("-", "_"))
        if name in {"no_resume", "no_report"}:
            if equal:
                raise ValueError(f"--{name.replace('_', '-')} does not accept a value")
            (root if name == "no_resume" else options)[
                "resume" if name == "no_resume" else "auto_report"
            ] = False
        elif name in _BOOLEAN_OPTIONS:
            if equal:
                raise ValueError(f"--{name} does not accept a value")
            (root if name in TaskCreateRequest.model_fields else options)[name] = True
        else:
            if equal:
                value = inline
            else:
                index += 1
                if index >= len(tokens) or tokens[index].startswith("--"):
                    raise ValueError(f"--{name.replace('_', '-')} requires a value")
                value = tokens[index]
            if name == "target":
                root.setdefault("additional_targets", []).append(value)
            elif name in TaskCreateRequest.model_fields and name not in {"command", "options"}:
                root[name] = value
            elif name in TaskOptions.model_fields:
                options[name] = value
            else:
                raise ValueError(f"unknown option: --{name.replace('_', '-')}")
        index += 1
    return {**root, "options": options}


def parse_input(request: InputRequest) -> ParsedInput:
    if sum(value is not None for value in (request.text, request.task, request.operation)) != 1:
        raise ValueError("provide exactly one of text, task or operation")
    if request.task is not None:
        return ParsedInput("task", task=request.task)
    if request.operation is not None:
        if not isinstance(request.operation, str) or not request.operation.strip():
            raise ValueError("control operation must be a non-empty string")
        if not isinstance(request.arguments, dict):
            raise ValueError("control arguments must be an object")
        return ParsedInput(
            "control", operation=request.operation.strip().lower(), arguments=request.arguments
        )
    text = (request.text or "").strip()
    if not text:
        raise ValueError("input must not be blank")
    if not text.startswith("/"):
        return ParsedInput("conversation", text=text)
    head, _, tail = text[1:].partition(" ")
    command = {"cfg": "config", "lang": "language", "exp": "experience"}.get(
        head.lower(), head.lower()
    )
    if command not in COMMANDS:
        raise ValueError(f"Unknown command: /{command}. Use /help.")
    if command in TASK_COMMANDS:
        tokens = shlex.split(tail)
        if not tokens or tokens[0].startswith("--"):
            raise ValueError(f"/{command} requires a target")
        return ParsedInput(
            "task",
            task={
                "command": command,
                "target": tokens[0],
                **parse_options(tokens[1:]),
            },
        )
    if command in {"clear", "quit"}:
        if tail.strip():
            raise ValueError(f"/{command} does not accept arguments")
        return ParsedInput("display", operation=command)
    if command in ADAPTER_COMMANDS:
        return ParsedInput("adapter", operation=command, text=tail.strip())
    if command == "scope":
        if not tail.strip():
            return ParsedInput("control", operation="session.scope.read")
        if tail.strip() == "reset":
            return ParsedInput("control", operation="session.scope.reset")
        fields = parse_options(shlex.split(tail))
        scope = fields.pop("options")
        if fields or set(scope) - SCOPE_FIELDS:
            raise ValueError("/scope accepts scope options only")
        return ParsedInput("control", operation="session.scope.update", arguments={"scope": scope})
    if command == "report":
        tokens = shlex.split(tail)
        args: dict[str, Any] = {}
        while tokens:
            key = tokens.pop(0)
            if key not in {"--kind", "--output"} or not tokens:
                raise ValueError("/report accepts --kind security|replay and --output PATH")
            args[key[2:]] = tokens.pop(0)
        return ParsedInput("control", operation="report.export", arguments=args)
    operations = {
        "new": "session.new",
        "permission": "session.permission.set",
        "target": "session.target.set",
        "compact": "session.compact",
        "tools": "session.tools",
        "help": "session.help",
        "think": "session.think",
    }
    argument_names = {"permission": "mode", "target": "target", "think": "value"}
    args = {argument_names[command]: tail.strip()} if command in argument_names else {}
    if command not in argument_names and tail.strip():
        raise ValueError(f"/{command} does not accept arguments")
    return ParsedInput("control", operation=operations[command], arguments=args)
