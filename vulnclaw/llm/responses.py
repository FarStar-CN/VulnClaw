"""Responses wire format and lossless output collection."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from jsonschema import Draft202012Validator

from .types import Function, ProtocolError, Result, ToolCall, Usage, as_dict


def tools_for_wire(tools: list[dict[str, Any]], *, limit: int | None = None) -> list[dict]:
    if limit is not None and len(tools) > limit:
        raise ValueError(f"Endpoint supports at most {limit} tools; received {len(tools)}")
    result, names = [], set()
    for tool in tools:
        if tool.get("type") != "function":
            raise ValueError("Only local function tools are supported")
        fn = copy.deepcopy(tool.get("function", tool))
        name = fn.get("name", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            raise ValueError(f"Invalid function name: {name!r}")
        if name in names:
            raise ValueError(f"Duplicate function name: {name}")
        names.add(name)
        schema = fn.get("parameters", {})
        Draft202012Validator.check_schema(schema)
        if schema.get("type") != "object":
            raise ValueError(f"Function {name} parameters must be an object schema")
        result.append(
            {
                "type": "function",
                "name": name,
                "description": fn.get("description", ""),
                "parameters": schema,
                "strict": False,
            }
        )
    return result


def input_items(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    instructions, items = [], []
    for source in messages:
        item = copy.deepcopy(source)
        if "_chat_message" in item:
            raise ProtocolError("Cannot replay Chat history through Responses; start a new session")
        if item.get("role") == "system":
            instructions.append(str(item.get("content", "")))
            continue
        if item.get("type") in {"reasoning", "function_call", "function_call_output"}:
            items.append({k: v for k, v in item.items() if not k.startswith("_")})
            continue
        role = item.get("role")
        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": item["tool_call_id"],
                    "output": item.get("content", ""),
                }
            )
            continue
        # Chat adapter's private raw message must never leak onto the wire.
        content = item.get("content", "")
        if isinstance(content, str):
            content = [
                {"type": "output_text" if role == "assistant" else "input_text", "text": content}
            ]
        if not isinstance(content, list):
            raise ProtocolError("Message content must be text or content parts")
        for part in content:
            if part.get("type") == "text":
                part["type"] = "output_text" if role == "assistant" else "input_text"
        if role not in {"user", "assistant", "developer"}:
            raise ProtocolError(f"Unsupported input item: {item.get('type') or role}")
        if content:
            projected = {"type": "message", "role": role, "content": content}
            for key in ("id", "phase", "status"):
                if key in item:
                    projected[key] = item[key]
            items.append(projected)
        for call in item.get("tool_calls") or []:
            fn = call["function"]
            items.append(
                {
                    "type": "function_call",
                    "call_id": call["id"],
                    "name": fn["name"],
                    "arguments": fn["arguments"],
                }
            )
    validate_history(items)
    return "\n\n".join(instructions), items


def validate_history(items: list[dict]) -> None:
    pending: set[str] = set()
    seen: set[str] = set()
    for item in items:
        if item.get("type") == "function_call":
            call_id = item.get("call_id")
            if not call_id or call_id in seen:
                raise ProtocolError("Missing or duplicate function call_id in history")
            pending.add(call_id)
            seen.add(call_id)
        elif item.get("type") == "function_call_output":
            call_id = item.get("call_id")
            if call_id not in pending:
                raise ProtocolError("Orphan or duplicate function_call_output")
            pending.remove(call_id)
        elif item.get("role") == "user" and pending:
            raise ProtocolError("Function calls must have results before the next user turn")
    if pending:
        raise ProtocolError("Function call results missing from history")


def parse(value: Any) -> Result:
    raw = as_dict(value)
    status = raw.get("status")
    if status not in {"completed", "incomplete", "failed", "cancelled"}:
        raise ProtocolError(f"Missing terminal Responses status: {status}")
    output = copy.deepcopy(raw.get("output") or [])
    text, final, reasoning, calls = [], [], [], []
    ids: set[str] = set()
    for item in output:
        kind = item.get("type")
        if kind == "message":
            parts = item.get("content") or []
            message_text = "".join(p.get("text", p.get("refusal", "")) for p in parts)
            text.append(message_text)
            if item.get("phase") == "final_answer":
                final.append(message_text)
        elif kind == "reasoning":
            parts = item.get("content") or item.get("summary") or []
            reasoning.append("".join(p.get("text", "") for p in parts))
        elif kind == "function_call":
            call_id, name, args = item.get("call_id"), item.get("name"), item.get("arguments")
            if not call_id or call_id in ids or not name or not isinstance(args, str):
                raise ProtocolError("Incomplete or conflicting function_call")
            ids.add(call_id)
            calls.append(ToolCall(call_id, Function(name, args)))
        else:
            raise ProtocolError(f"Unsupported Responses output item: {kind}")
    return Result(
        output,
        "".join(final or text),
        "".join(reasoning),
        calls,
        status,
        (raw.get("incomplete_details") or {}).get("reason"),
        raw.get("error"),
        str(raw.get("id", "")),
        Usage.parse(raw.get("usage")),
    )


class Collector:
    """Use complete terminal output as truth, deltas only for live presentation."""

    def __init__(self) -> None:
        self.result: Result | None = None
        self.done: dict[int, dict] = {}
        self.sequences: dict[int, str] = {}
        self.partial_content = ""

    def feed(self, event: Any) -> tuple[str, str] | None:
        data = as_dict(event)
        sequence = data.get("sequence_number")
        if isinstance(sequence, int):
            fingerprint = json.dumps(data, sort_keys=True)
            if sequence in self.sequences:
                if self.sequences[sequence] != fingerprint:
                    raise ProtocolError("Conflicting duplicate stream sequence")
                return None
            self.sequences[sequence] = fingerprint
        kind = data.get("type", "")
        if self.result is not None:
            raise ProtocolError("Event received after terminal response")
        if kind in {"response.completed", "response.incomplete", "response.failed"}:
            response = data.get("response")
            if not isinstance(response, dict):
                raise ProtocolError("Terminal event missing response")
            if response.get("status") != kind.split(".")[1]:
                raise ProtocolError("Terminal status does not match event")
            self.result = parse(response)
        elif kind in {"error", "response.error"}:
            raise ProtocolError(str(data.get("error") or data.get("message") or "Stream error"))
        elif kind == "response.output_item.done":
            index = data.get("output_index", 0)
            item = data.get("item", {})
            if index in self.done and self.done[index] != item:
                raise ProtocolError("Conflicting output item")
            self.done[index] = item
        elif kind == "response.output_text.delta":
            self.partial_content += data.get("delta", "")
            return "content", data.get("delta", "")
        elif kind in {"response.reasoning_text.delta", "response.reasoning_summary_text.delta"}:
            return "thinking", data.get("delta", "")
        return None

    def finish(self) -> Result:
        if self.result is None:
            raise ProtocolError("Responses stream ended without a terminal event")
        for index, item in self.done.items():
            if index >= len(self.result.output_items) or self.result.output_items[index] != item:
                raise ProtocolError("Terminal output conflicts with completed stream item")
        return self.result


def create(client: Any, kwargs: dict[str, Any]) -> Result:
    return parse(client.responses.create(**kwargs))


def stream(client: Any, kwargs: dict[str, Any]) -> Any:
    return client.responses.create(**kwargs, stream=True)
