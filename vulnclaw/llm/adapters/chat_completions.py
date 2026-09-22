"""Explicit Chat Completions boundary; never a runtime Responses fallback."""

from __future__ import annotations

import copy
from typing import Any

from ..types import Function, ProtocolError, Result, ToolCall, Usage, as_dict


def messages_for_wire(messages: list[dict]) -> list[dict]:
    result: list[dict] = []
    for source in messages:
        item = copy.deepcopy(source)
        if "_chat_message" in item:
            result.append(item["_chat_message"])
        elif item.get("type") == "function_call_output":
            result.append(
                {"role": "tool", "tool_call_id": item["call_id"], "content": item["output"]}
            )
        elif item.get("type") not in {None, "message"}:
            raise ProtocolError(
                "Cannot replay Responses reasoning/tools through Chat; start a new session"
            )
        else:
            for key in ("type", "id", "status", "phase"):
                item.pop(key, None)
            content = item.get("content")
            if isinstance(content, list):
                for part in content:
                    if part.get("type") in {"input_text", "output_text"}:
                        part["type"] = "text"
            result.append({k: v for k, v in item.items() if not k.startswith("_")})
    return result


def parse(value: Any) -> Result:
    raw = as_dict(value)
    choices = raw.get("choices") or []
    if not choices:
        raise ProtocolError("Chat response has no choices")
    choice = choices[0]
    message = choice.get("message") or {}
    finish = choice.get("finish_reason")
    if finish not in {"stop", "tool_calls", "length", "content_filter"}:
        raise ProtocolError(f"Chat response missing/unsupported finish_reason: {finish}")
    status = "incomplete" if finish in {"length", "content_filter"} else "completed"
    calls = []
    ids = set()
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        call_id = call.get("id")
        if not call_id or call_id in ids or not fn.get("name"):
            raise ProtocolError("Incomplete or conflicting Chat tool call")
        ids.add(call_id)
        calls.append(ToolCall(call_id, Function(fn["name"], fn.get("arguments", ""))))
    # Carry the original message for same-protocol replay, including reasoning_content.
    item = {
        "role": "assistant",
        "content": message.get("content") or "",
        "_chat_message": copy.deepcopy(message),
    }
    if message.get("tool_calls"):
        item["tool_calls"] = copy.deepcopy(message["tool_calls"])
    if message.get("reasoning_content"):
        item["reasoning_content"] = message["reasoning_content"]
    return Result(
        [item],
        message.get("content") or "",
        message.get("reasoning_content") or "",
        calls,
        status,
        finish if status == "incomplete" else None,
        response_id=str(raw.get("id", "")),
        usage=Usage.parse(raw.get("usage")),
    )


class Collector:
    def __init__(self) -> None:
        self.message: dict[str, Any] = {"role": "assistant", "content": "", "reasoning_content": ""}
        self.calls: dict[int, dict] = {}
        self.finish_reason: str | None = None
        self.usage = None

    def feed(self, value: Any) -> list[tuple[str, str]]:
        chunk = as_dict(value)
        if chunk.get("usage") is not None:
            self.usage = chunk["usage"]
        events = []
        for choice in chunk.get("choices") or []:
            if choice.get("finish_reason"):
                self.finish_reason = choice["finish_reason"]
            delta = choice.get("delta") or {}
            for key, kind in (("content", "content"), ("reasoning_content", "thinking")):
                if delta.get(key):
                    self.message[key] += delta[key]
                    events.append((kind, delta[key]))
            for call in delta.get("tool_calls") or []:
                item = self.calls.setdefault(
                    call.get("index", 0),
                    {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    },
                )
                if call.get("id"):
                    if item["id"] and item["id"] != call["id"]:
                        raise ProtocolError("Conflicting Chat call id")
                    item["id"] = call["id"]
                for key in ("name", "arguments"):
                    part = (call.get("function") or {}).get(key) or ""
                    if key == "name" and part == item["function"][key]:
                        continue
                    item["function"][key] += part
        return events

    def finish(self) -> Result:
        if self.finish_reason is None:
            raise ProtocolError("Chat stream ended without finish_reason")
        self.message["tool_calls"] = list(self.calls.values())
        return parse(
            {
                "choices": [{"message": self.message, "finish_reason": self.finish_reason}],
                "usage": self.usage,
            }
        )


def create(client: Any, kwargs: dict[str, Any]) -> Result:
    return parse(client.chat.completions.create(**kwargs))


def stream(client: Any, kwargs: dict[str, Any]) -> Any:
    return client.chat.completions.create(**kwargs, stream=True)
