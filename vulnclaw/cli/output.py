"""The machine output and process-exit boundary for business commands."""

from __future__ import annotations

import asyncio
import dataclasses
import signal
import sys
import threading
from contextlib import redirect_stdout
from contextvars import ContextVar
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Coroutine, TextIO
from uuid import uuid4

import typer
from typer.core import TyperGroup

try:
    from typer._click.exceptions import ClickException, MissingParameter
    from typer._click.globals import get_current_context
except ImportError:  # Typer versions before the bundled Click implementation.
    from click import get_current_context
    from click.exceptions import ClickException, MissingParameter

from vulnclaw.session_protocol import JsonlWriter, make_event


class CommandError(ValueError):
    def __init__(
        self, code: str, message: str, *, status: str = "failed",
        path: list[str] | None = None, constraints: dict[str, Any] | None = None,
        next_actions: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.field_path = path
        self.constraints = constraints
        self.next_actions = next_actions


def json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return json_value(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [json_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    return value


class CommandOutput:
    def __init__(self, format: str = "json", stream: TextIO | None = None) -> None:
        if format not in {"json", "jsonl"}:
            raise CommandError("invalid_format", "output format must be json or jsonl")
        self.format = format
        self.stream = stream if stream is not None else sys.stdout
        self.writer = JsonlWriter(self.stream)
        self.request_id = f"command-{uuid4().hex}"
        self.finished = False
        self.exit_code = 0

    def event(self, event_type: str, **fields: Any) -> None:
        # Headless consumers do not perform the interactive initialize handshake.
        if self.format == "jsonl" and event_type != "ready":
            self.writer.event(event_type, **json_value(fields))

    def finish(
        self,
        *,
        status: str = "completed",
        exit_code: int = 0,
        result: Any = None,
        error: dict[str, str] | None = None,
        task_id: str | None = None,
        findings: dict[str, int] | None = None,
        artifacts: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        if self.finished:
            raise RuntimeError("command result was already written")
        fields: dict[str, Any] = {"status": status, "exit_code": exit_code, "error": error}
        if result is not None:
            fields["result"] = json_value(result)
        if findings is not None:
            fields["findings"] = findings
        if artifacts is not None:
            fields["artifacts"] = artifacts
        event = make_event("command_result", request_id=self.request_id, task_id=task_id, **fields)
        self.writer.write(event)
        self.finished = True
        self.exit_code = exit_code
        return event


_OUTPUT: ContextVar[CommandOutput] = ContextVar("vulnclaw_command_output")


def current_output() -> CommandOutput:
    return _OUTPUT.get()


def run_async(operation: Coroutine[Any, Any, Any]) -> Any:
    async def owned() -> Any:
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        previous = {}
        if threading.current_thread() is threading.main_thread():
            for name in (signal.SIGINT, signal.SIGTERM):
                previous[name] = signal.getsignal(name)
                signal.signal(name, lambda _signal, _frame: loop.call_soon_threadsafe(task.cancel))
        try:
            return await operation
        finally:
            for name, handler in previous.items():
                signal.signal(name, handler)

    return asyncio.run(owned())


def perform(operation: Callable[[], Any]) -> None:
    """Keep library diagnostics on stderr and serialize only the command result."""
    output = current_output()
    try:
        with redirect_stdout(sys.stderr):
            result = operation()
    except Exception as exc:
        from vulnclaw.cli.feedback import build_feedback, error_message

        output.finish(
            status=getattr(exc, "status", "failed"), exit_code=1,
            result={"feedback": build_feedback(exc, get_current_context())},
            error={"code": getattr(exc, "code", "command_failed"), "message": error_message(exc)},
        )
        raise typer.Exit(1) from exc
    if not output.finished:
        output.finish(result=result)
    raise typer.Exit(output.exit_code)


def requested_format(args: list[str]) -> str:
    # code scan's --format describes its artifact; --output-format owns stdout.
    for flag in ("--output-format", "--format"):
        if flag == "--format" and "--stream" in args:
            return "jsonl"
        for index, arg in enumerate(args):
            if arg == flag and index + 1 < len(args) and args[index + 1] in {"json", "jsonl"}:
                return args[index + 1]
            for value in ("json", "jsonl"):
                if arg == f"{flag}={value}":
                    return value
    return "json"


class HeadlessGroup(TyperGroup):
    """Normalize Click parse errors before they can render a Rich error panel."""

    def main(self, args=None, prog_name=None, complete_var=None, standalone_mode=True, **extra):
        args = list(sys.argv[1:] if args is None else args)
        output = CommandOutput(requested_format(args))
        token = _OUTPUT.set(output)
        code = 0
        result = None
        previous_term = None
        if threading.current_thread() is threading.main_thread():
            previous_term = signal.getsignal(signal.SIGTERM)

            def terminate(_signal, _frame):
                raise KeyboardInterrupt

            signal.signal(signal.SIGTERM, terminate)
        try:
            result = super().main(
                args=args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                **extra,
            )
            if isinstance(result, int):
                code = result
        except ClickException as exc:
            from vulnclaw.cli.feedback import build_feedback
            code = 1
            missing = isinstance(exc, MissingParameter)
            output.finish(
                status="requires_input" if missing else "failed",
                exit_code=1,
                error={
                    "code": "requires_input" if missing else "invalid_arguments",
                    "message": exc.format_message(),
                },
                result={"feedback": build_feedback(exc, getattr(exc, "ctx", None))},
            )
        except (asyncio.CancelledError, KeyboardInterrupt, typer.Abort):
            code = 1
            output.finish(
                status="cancelled",
                exit_code=1,
                error={"code": "cancelled", "message": "Command cancelled."},
            )
        except Exception as exc:
            from vulnclaw.cli.feedback import build_feedback, error_message
            code = 1
            if not output.finished:
                output.finish(
                    status=getattr(exc, "status", "failed"),
                    exit_code=1,
                    error={"code": getattr(exc, "code", "command_failed"), "message": error_message(exc)},
                    result={"feedback": build_feedback(exc)},
                )
            else:
                raise
        finally:
            if previous_term is not None:
                signal.signal(signal.SIGTERM, previous_term)
            _OUTPUT.reset(token)
        if standalone_mode:
            raise SystemExit(code)
        return result if code == 0 else code
