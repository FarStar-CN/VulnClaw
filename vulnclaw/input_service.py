"""One command grammar for terminal text and structured application callers."""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Any

from vulnclaw.task_service import SCOPE_FIELDS, TASK_COMMANDS, TaskCreateRequest, TaskOptions

COMMAND_METADATA = [
    {
        "name": "run",
        "category": "Tasks",
        "description": "Execute a task; --allow-actions limits actions; --continuous repeats cycles",
        "syntax": "/run TARGET [--allow-actions ACTIONS] [--continuous] [OPTIONS]",
        "examples": ["/run https://example.test --allow-actions recon,scan"],
    },
    {
        "name": "recon",
        "category": "Tasks",
        "description": "run --allow-actions recon",
        "syntax": "/recon TARGET [OPTIONS]",
        "examples": ["/recon https://example.test"],
    },
    {
        "name": "scan",
        "category": "Tasks",
        "description": "run --allow-actions recon,scan",
        "syntax": "/scan TARGET [OPTIONS]",
        "examples": ["/scan https://example.test"],
    },
    {
        "name": "exploit",
        "category": "Tasks",
        "description": "run --allow-actions recon,scan,exploit",
        "syntax": "/exploit TARGET [OPTIONS]",
        "examples": ["/exploit https://example.test"],
    },
    {
        "name": "persistent",
        "category": "Tasks",
        "description": "run --continuous",
        "syntax": "/persistent TARGET [--rounds N] [--cycles N] [OPTIONS]",
        "examples": ["/persistent https://example.test --cycles 2"],
    },
    {
        "name": "codescan",
        "category": "Tasks",
        "description": "Scan local source without an LLM",
        "syntax": "/codescan PATH [OPTIONS]",
        "examples": ["/codescan ./src"],
    },
    {
        "name": "report",
        "category": "Tasks",
        "description": "Export existing results; --kind security | replay; --output PATH",
        "syntax": "/report [--kind security|replay] [--output PATH]",
        "examples": ["/report --kind security --output ./report.md"],
    },
    {
        "name": "target",
        "category": "Session",
        "description": "Show or set the current target",
        "syntax": "/target [TARGET]",
        "examples": ["/target https://example.test"],
    },
    {
        "name": "scope",
        "category": "Session",
        "description": "Preview and edit session scope defaults",
        "syntax": "/scope [reset | --only-host HOSTS --allowed-ports PORTS --blocked-ports PORTS --only-path PATHS --blocked-host HOSTS --blocked-path PATHS --allow-actions ACTIONS --block-actions ACTIONS]",
        "examples": ["/scope --allowed-ports 80,443 --allow-actions recon,scan"],
    },
    {
        "name": "permission",
        "category": "Session",
        "description": "Show or set read_only | ask | auto_review | full_access",
        "syntax": "/permission [read_only|ask|auto_review|full_access]",
        "examples": ["/permission ask"],
    },
    {
        "name": "new",
        "category": "Session",
        "description": "Start a new conversation; keep configuration and scope defaults",
        "syntax": "/new",
        "examples": ["/new"],
    },
    {
        "name": "clear",
        "category": "Session",
        "description": "Clear the display; keep the conversation",
        "syntax": "/clear",
        "examples": ["/clear"],
    },
    {
        "name": "compact",
        "category": "Session",
        "description": "Compact conversation context",
        "syntax": "/compact",
        "examples": ["/compact"],
    },
    {
        "name": "think",
        "category": "Session",
        "description": "Show or hide reasoning output",
        "syntax": "/think [on|off]",
        "examples": ["/think on"],
    },
    {
        "name": "config",
        "category": "Configuration",
        "description": "Configure the model provider",
        "syntax": "/config",
        "examples": ["/config"],
    },
    {
        "name": "language",
        "category": "Configuration",
        "description": "Set display language",
        "syntax": "/language auto|zh|en",
        "examples": ["/language zh"],
    },
    {
        "name": "experience",
        "category": "Management",
        "description": "Manage experience records",
        "syntax": "/experience [list [--status pending|approved|rejected|all]|show ID|approve ID|reject ID|edit ID --context TEXT --lesson TEXT]",
        "examples": ["/experience", "/experience list --status all", "/experience show LESSON_ID"],
    },
    {
        "name": "learn",
        "category": "Management",
        "description": "Learn from saved work",
        "syntax": "/learn RUN_NAME",
        "examples": ["/learn run-example"],
    },
    {
        "name": "feedback",
        "category": "Management",
        "description": "Record feedback",
        "syntax": "/feedback RUN_NAME RATING NOTES",
        "examples": ['/feedback run-example 3 "The finding needs more evidence"'],
    },
    {
        "name": "help",
        "category": "Management",
        "description": "List commands",
        "syntax": "/help [COMMAND]",
        "examples": ["/help scope"],
    },
    {
        "name": "quit",
        "category": "Management",
        "description": "Exit the client",
        "syntax": "/quit",
        "examples": ["/quit"],
    },
]
COMMANDS = {command["name"]: command["description"] for command in COMMAND_METADATA}


def command_help(topic: str = "") -> dict[str, Any]:
    topic = {"cfg": "config", "lang": "language", "exp": "experience"}.get(
        topic.lstrip("/").lower(),
        topic.lstrip("/").lower(),
    )
    commands = [command for command in COMMAND_METADATA if not topic or command["name"] == topic]
    return {
        "commands": commands,
        "topic": topic,
        "topics": list(COMMANDS),
        "error": f"Unknown help topic: {topic}" if topic and not commands else "",
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
        "help": "session.help",
        "think": "session.think",
    }
    argument_names = {"permission": "mode", "target": "target", "think": "value", "help": "topic"}
    args = {argument_names[command]: tail.strip()} if command in argument_names else {}
    if command not in argument_names and tail.strip():
        raise ValueError(f"/{command} does not accept arguments")
    return ParsedInput("control", operation=operations[command], arguments=args)
