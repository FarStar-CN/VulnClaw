"""Minimal LLM endpoint probe used by ``vulnclaw doctor``.

A configured base URL can point at a mock/dead endpoint that still returns
HTTP 200 with prose-only completions. The autonomous solve loop then spins
through turns without any tool call. This probe sends one tiny chat
completion that carries a ``ping`` tool schema and checks whether the
endpoint is reachable and capable of emitting a tool call, so broken
endpoints are caught before a task starts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vulnclaw.config.llm_utils import build_chat_completion_kwargs
from vulnclaw.config.settings import make_openai_client
from vulnclaw.config.token_provider import resolve_llm_token

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
    """Send one tool-bearing completion and classify the outcome.

    Never raises: every failure mode is mapped onto :class:`LLMProbeResult`
    so callers can render it directly.
    """
    try:
        api_key = resolve_llm_token(llm_config)
    except Exception as exc:  # OAuth refresh failure, unknown auth mode, ...
        return LLMProbeResult(False, "auth", f"credential resolution failed: {str(exc)[:200]}")

    try:
        client = make_openai_client(api_key, llm_config.base_url, timeout=timeout)
    except Exception as exc:
        return LLMProbeResult(False, "unknown", f"client construction failed: {str(exc)[:200]}")

    kwargs = build_chat_completion_kwargs(
        llm_config,
        _PROBE_MESSAGES,
        [_PING_TOOL],
        max_tokens=200,
        temperature=0.0,
    )
    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as exc:
        category, detail = _classify_status_error(exc)
        return LLMProbeResult(False, category, detail)

    try:
        choice = response.choices[0]
    except (IndexError, AttributeError, TypeError) as exc:
        return LLMProbeResult(False, "unknown", f"malformed response: {str(exc)[:200]}")

    tool_calls = list(getattr(choice.message, "tool_calls", None) or [])
    content = (getattr(choice.message, "content", None) or "").strip()
    if tool_calls:
        return LLMProbeResult(
            True,
            "ok",
            "endpoint reachable and emitted a tool call",
            tool_call_emitted=True,
        )
    preview = content[:160] or "(empty completion)"
    return LLMProbeResult(
        False,
        "no_tool_call",
        "endpoint replied without a tool call; it is not usable for autonomous "
        f"agent mode (last reply: {preview})",
        tool_call_emitted=False,
    )
