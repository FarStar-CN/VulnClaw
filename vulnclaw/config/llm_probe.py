"""Minimal LLM endpoint probe used by ``vulnclaw doctor``.

A configured base URL can point at a mock/dead endpoint that still returns
HTTP 200 with prose-only completions. The autonomous solve loop then spins
through turns without any tool call. This probe sends one tiny chat
completion that carries a ``ping`` tool schema and checks whether the
endpoint is reachable and capable of emitting a tool call, so broken
endpoints are caught before a task starts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from vulnclaw.config.token_provider import TokenResolutionError
from vulnclaw.llm.capabilities import api_format, endpoint
from vulnclaw.llm.client import complete, make_client

_PING_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "ping",
        "description": "Reply with this tool call to acknowledge the probe.",
        "parameters": {
            "type": "object",
            "properties": {
                "ok": {"type": "boolean", "description": "Always true."},
            },
            "required": ["ok"],
        },
    },
}

_PROBE_MESSAGES: list[dict[str, Any]] = [
    {
        "role": "user",
        "content": (
            "This is an endpoint health probe. Call the `ping` tool with "
            '{"ok": true}. Do not reply with plain text.'
        ),
    }
]


@dataclass
class LLMProbeResult:
    """Outcome of one endpoint probe."""

    ok: bool
    category: str  # "ok" | "unreachable" | "auth" | "not_found" | "no_tool_call" | "unknown"
    detail: str = ""
    tool_call_emitted: bool = False


def _classify_status_error(exc: Exception) -> tuple[str, str]:
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    detail = ""
    try:
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict) and err.get("message"):
                detail = str(err["message"])[:200]
    except Exception:
        detail = ""
    if status in (401, 403):
        return "auth", f"HTTP {status}: credentials rejected by the endpoint"
    if status == 404:
        return "not_found", f"HTTP 404: model or endpoint path not found ({detail or 'no detail'})"
    if isinstance(status, int):
        return "unknown", f"HTTP {status}: {detail or 'endpoint returned an error status'}"
    text = str(exc)
    lowered = text.lower()
    if any(marker in lowered for marker in ("connect", "timeout", "timed out", "resolve", "tunnel", "reset")):
        return "unreachable", text[:200]
    return "unknown", text[:200]


def probe_llm_endpoint(llm_config: Any, *, timeout: float = 20.0) -> LLMProbeResult:
    """Check the configured route with a harmless two-request function exchange."""
    client = None
    try:
        client = make_client(llm_config, timeout=timeout)
        options = {"temperature": None, "reasoning_effort": "low"}
        # A nullable configured budget is an explicit opt-out, including OAuth.
        options["max_tokens"] = None if llm_config.max_tokens is None else min(llm_config.max_tokens, 4096)
        first = complete(llm_config, _PROBE_MESSAGES, [_PING_TOOL], client=client, **options)
        if not first.tool_calls:
            return LLMProbeResult(False, "no_tool_call",
                                  "Endpoint replied; tool calling was not observed in this sample")
        outputs = []
        for call in first.tool_calls:
            if call.function.name != "ping" or json.loads(call.function.arguments) != {"ok": True}:
                return LLMProbeResult(False, "unknown", "Probe returned an invalid ping call")
            outputs.append({"type": "function_call_output", "call_id": call.id,
                            "output": '{"ok": true}'})
        complete(llm_config, [*_PROBE_MESSAGES, *first.output_items, *outputs], client=client, **options)
        return LLMProbeResult(True, "ok", f"{api_format(llm_config)} {endpoint(llm_config)}: "
                              "two-turn ping exchange completed (probe effort=low)", True)
    except TokenResolutionError as exc:
        return LLMProbeResult(False, "auth", f"credential resolution failed: {exc}")
    except Exception as exc:
        category, detail = _classify_status_error(exc)
        return LLMProbeResult(False, category, detail)
    finally:
        if client is not None:
            client.close()
