"""Responses wire format and lossless output collection."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from jsonschema import Draft202012Validator

from ..types import Function, ProtocolError, Result, ToolCall, Usage, as_dict


def tools_for_wire(tools: list[dict[str, Any]], *, limit: int | None = None) -> list[dict]:
    if limit is not None and len(tools) > limit:
        raise ValueError(f"Endpoint supports at most {limit} tools; received {len(tools)}")
    result, names = [], set()
    for tool in tools:
        kind = tool.get("type")
        if not isinstance(kind, str) or not kind:
            raise ValueError("Tool type is required")
        if kind.startswith("computer"):
            raise ValueError("Computer use is outside this integration")
        if kind != "function":
            native = copy.deepcopy(tool)
            if kind in {"custom", "namespace"}:
                name = native.get("name", "")
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or name in names:
                    raise ValueError(f"Invalid or duplicate tool name: {name!r}")
                names.add(name)
            if kind == "namespace":
                native["tools"] = tools_for_wire(native.get("tools") or [])
            result.append(native)
            continue
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
                **{k: v for k, v in fn.items() if k not in {"type", "name", "parameters", "description"}},
                "strict": fn.get("strict", False),
            }
        )
    return result


def input_items(messages: list[dict[str, Any]] | str, *, allow_external_calls: bool = False) -> tuple[str, list[dict[str, Any]]]:
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    instructions, items = [], []
    for source in messages:
        item = copy.deepcopy(source)
        if "_chat_message" in item:
            raise ProtocolError("Cannot replay Chat history through Responses; start a new session")
        if item.get("role") == "system" and isinstance(item.get("content", ""), str):
            instructions.append(item.get("content", ""))
            continue
        if str(item.get("type", "")).startswith("computer"):
            raise ProtocolError("Computer use is outside this integration")
        if item.get("type") and item["type"] != "message":
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
            elif part.get("type") == "image_url":
                image = part.get("image_url")
                part.clear()
                part.update(type="input_image", image_url=image.get("url") if isinstance(image, dict) else image)
                if isinstance(image, dict) and "detail" in image:
                    part["detail"] = image["detail"]
        if role not in {"user", "assistant", "developer", "system"}:
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
    validate_history(items, allow_external_calls=allow_external_calls)
    return "\n\n".join(instructions), items


CALL_OUTPUTS = {
    "function_call": "function_call_output", "custom_tool_call": "custom_tool_call_output",
    "shell_call": "shell_call_output", "local_shell_call": "local_shell_call_output",
    "apply_patch_call": "apply_patch_call_output", "tool_search_call": "tool_search_output",
}


def server_executed(item: dict) -> bool:
    return item.get("execution") == "server" or (
        item.get("type") == "shell_call"
        and (item.get("environment") or {}).get("type") in {"container_reference", "container_auto"}
    )


def validate_history(items: list[dict], *, allow_external_calls: bool = False) -> None:
    pending: dict[str, str] = {}
    seen: set[str] = set()
    hosted: set[str] = set()
    for item in items:
        kind = item.get("type")
        if kind in CALL_OUTPUTS and server_executed(item):
            if item.get("call_id"):
                hosted.add(item["call_id"])
            continue
        if kind in CALL_OUTPUTS:
            call_id = item.get("call_id")
            if not call_id or call_id in seen:
                raise ProtocolError("Missing or duplicate function call_id in history")
            pending[call_id] = CALL_OUTPUTS[kind]
            seen.add(call_id)
        elif kind in CALL_OUTPUTS.values():
            call_id = item.get("call_id")
            if server_executed(item) or call_id in hosted:
                continue
            if not call_id or call_id in seen and call_id not in pending:
                raise ProtocolError("Orphan or duplicate function_call_output")
            if call_id not in pending and not allow_external_calls:
                raise ProtocolError("Orphan or duplicate function_call_output")
            if call_id in pending and pending[call_id] != kind:
                raise ProtocolError("Tool result type does not match call")
            pending.pop(call_id, None)
            seen.add(call_id)
        elif item.get("role") == "user" and pending:
            raise ProtocolError("Function calls must have results before the next user turn")
    if pending:
        raise ProtocolError("Function call results missing from history")


def parse(value: Any) -> Result:
    raw = as_dict(value)
    status = raw.get("status")
    if status not in {"queued", "in_progress", "completed", "incomplete", "failed", "cancelled"}:
        raise ProtocolError(f"Missing terminal Responses status: {status}")
    output = copy.deepcopy(raw.get("output") or [])
    text, final, reasoning, calls = [], [], [], []
    ids: set[str] = set()
    for item in output:
        kind = item.get("type")
        if kind == "message":
            parts = item.get("content") or []
            message_text = "".join(p.get("text", p.get("refusal", "")) or "" for p in parts)
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
            calls.append(ToolCall(call_id, Function(name, args), namespace=item.get("namespace"), raw=item))
        elif kind in CALL_OUTPUTS and not server_executed(item):
            call_id = item.get("call_id")
            if not call_id or call_id in ids:
                raise ProtocolError("Incomplete or conflicting native tool call")
            ids.add(call_id)
            if kind == "custom_tool_call":
                name, raw_input = item.get("name"), item.get("input")
                if not name or not isinstance(raw_input, str):
                    raise ProtocolError("Invalid custom tool input")
                calls.append(ToolCall(call_id, Function(name, json.dumps({"input": raw_input})),
                                      "custom", item.get("namespace"), item))
            else:
                arguments = {key: value for key, value in item.items()
                             if key not in {"id", "call_id", "status", "created_by"}}
                calls.append(ToolCall(call_id, Function(kind.removesuffix("_call"), json.dumps(arguments)),
                                      kind, raw=item))
        elif isinstance(kind, str) and kind.startswith("computer"):
            raise ProtocolError("Computer use is outside this integration")
        elif not isinstance(kind, str) or not kind:
            raise ProtocolError("Responses output item has no type")
        # Hosted tools and new provider items remain available verbatim in
        # output_items/raw; they are not mistaken for locally executable tools.
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
        raw=copy.deepcopy(raw),
    )


class Collector:
    """Use complete terminal output as truth, deltas only for live presentation."""

    def __init__(self) -> None:
        self.accepted_event = False
        self.result: Result | None = None
        self.done: dict[int, dict] = {}
        self.sequences: dict[int, str] = {}
        self.partial_content = ""

    def feed(self, event: Any) -> tuple[str, str] | None:
        self.accepted_event = False
        data = as_dict(event)
        sequence = data.get("sequence_number")
        if isinstance(sequence, int):
            fingerprint = json.dumps(data, sort_keys=True)
            if sequence in self.sequences:
                if self.sequences[sequence] != fingerprint:
                    raise ProtocolError("Conflicting duplicate stream sequence")
                return None
            self.sequences[sequence] = fingerprint
        self.accepted_event = True
        kind = data.get("type", "")
        if self.result is not None:
            raise ProtocolError("Event received after terminal response")
        if kind in {"response.completed", "response.incomplete", "response.failed", "response.cancelled"}:
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
        elif kind in {"response.output_text.delta", "response.refusal.delta"}:
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
