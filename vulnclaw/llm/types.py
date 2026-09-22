"""Application results independent of an SDK's protocol objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ProtocolError(ValueError):
    """An incomplete or invalid protocol exchange; never retry as a network failure."""


@dataclass(frozen=True)
class Function:
    name: str
    arguments: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    function: Function
    type: str = "function"
    namespace: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def output_item(self, output: Any, *, failed: bool = False) -> dict[str, Any]:
        kind = {
            "function": "function_call_output", "custom": "custom_tool_call_output",
            "shell_call": "shell_call_output", "local_shell_call": "local_shell_call_output",
            "apply_patch_call": "apply_patch_call_output", "tool_search_call": "tool_search_output",
        }.get(self.type)
        if kind is None:
            raise ProtocolError(f"Unsupported local tool result: {self.type}")
        result = {"type": kind, "call_id": self.id, "output": output}
        if self.type == "shell_call" and not isinstance(output, list):
            result["output"] = [{"stdout": "", "stderr": str(output),
                                 "outcome": {"type": "exit", "exit_code": 1}}]
        if self.type == "tool_search_call":
            result.pop("output")
            result.update(tools=output if isinstance(output, list) else [], execution="client", status="completed")
        if self.type == "apply_patch_call":
            result["status"] = "failed" if failed or str(output).startswith("[!") else "completed"
        return result


@dataclass
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, value: Any) -> Usage | None:
        if value is None:
            return None
        raw = as_dict(value)
        return cls(
            raw.get("input_tokens", raw.get("prompt_tokens")),
            raw.get("output_tokens", raw.get("completion_tokens")),
            raw.get("total_tokens"),
            raw,
        )


@dataclass
class Result:
    output_items: list[dict[str, Any]]
    content: str = ""
    reasoning_content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    status: str = "completed"
    incomplete_reason: str | None = None
    error: Any = None
    response_id: str = ""
    usage: Usage | None = None
    retry_attempts: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def pending(self) -> bool:
        return self.status in {"queued", "in_progress"}

    @property
    def approval_requests(self) -> list[dict[str, Any]]:
        return [item for item in self.output_items if item.get("type") == "mcp_approval_request"]

    def require_complete(self) -> Result:
        if self.status != "completed":
            exc = ProtocolError(
                f"LLM response {self.status}: {self.incomplete_reason or self.error or 'unknown'}"
            )
            exc.result = self
            raise exc
        return self


def as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if hasattr(value, "__dict__"):
        return {key: to_plain(item) for key, item in vars(value).items() if not key.startswith("_")}
    raise ProtocolError("Expected an object in LLM response")


def to_plain(value: Any) -> Any:
    if isinstance(value, list):
        return [to_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: to_plain(item) for key, item in value.items()}
    if hasattr(value, "model_dump") or hasattr(value, "__dict__"):
        return as_dict(value)
    return value
