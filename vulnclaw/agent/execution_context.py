"""Per-operation tool policy and conversation report integration."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterator


@dataclass(frozen=True)
class ExecutionContext:
    mode: str = "agent"
    report: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None


_CONTEXT: ContextVar[ExecutionContext] = ContextVar("execution_context", default=ExecutionContext())


def current_execution() -> ExecutionContext:
    return _CONTEXT.get()


@contextmanager
def execution_context(context: ExecutionContext) -> Iterator[None]:
    token = _CONTEXT.set(context)
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def report_tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "conversation_report",
            "description": (
                "Express the user's report intent for the current engagement. Use default to "
                "classify the work as security testing or replay/CTF; required schedules a "
                "report on completion; suppress means no report for this engagement; export "
                "exports existing results now without testing again. Explanations, plans and "
                "progress summaries are conversation replies, not report exports. Honor negation "
                "and earlier user requests. Call before doing work when the user specifies a "
                "report preference. A replay report describes solving a CTF or reproducing proof; "
                "a security report describes assessed findings."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["default", "required", "suppress", "export"],
                    },
                    "kind": {"type": "string", "enum": ["security", "replay"]},
                    "output": {"type": "string"},
                },
                "required": ["action", "kind"],
                "additionalProperties": False,
            },
        },
    }
