"""Native Responses options and documented provider differences.

Options use the provider's wire names. Transport credentials and routing stay
owned by the client; unsupported semantics are reported before network IO.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from .capabilities import Diagnostic, official_provider

# Fields accepted by the pinned OpenAI SDK. Provider extensions go in extra_body.
SDK_FIELDS = frozenset(
    """
background context_management conversation include input instructions max_output_tokens
max_tool_calls metadata model moderation parallel_tool_calls previous_response_id prompt
prompt_cache_key prompt_cache_options prompt_cache_retention reasoning safety_identifier
service_tier store stream_options temperature text tool_choice tools top_logprobs top_p
truncation user
""".split()
)
RESERVED = frozenset(
    {
        "model",
        "input",
        "stream",
        "extra_headers",
        "extra_query",
        "timeout",
        "api_key",
        "base_url",
        "max_tokens",
        "max_completion_tokens",
        "reasoning_effort",
    }
)
SUPPORTED = {
    "deepseek": frozenset(
        """
        input instructions model stream temperature top_p max_output_tokens top_logprobs
        tools tool_choice reasoning text user
    """.split()
    ),
    "moonshot": frozenset(
        """
        input instructions model stream max_output_tokens reasoning text tools tool_choice
        include prompt_cache_key prompt_cache_options safety_identifier
    """.split()
    ),
    "qwen": frozenset(
        """
        input instructions model stream previous_response_id conversation store tools
        tool_choice temperature top_p reasoning enable_thinking ocr_options max_output_tokens
    """.split()
    ),
}
TOOL_TYPES = {
    "deepseek": {"function", "custom"},
    "moonshot": {"function", "custom", "namespace", "web_search"},
    "qwen": {
        "function",
        "web_search",
        "web_extractor",
        "code_interpreter",
        "mcp",
        "file_search",
        "web_search_image",
        "image_search",
    },
}


def merge_options(config: Any, options: dict | None, overrides: dict) -> dict:
    result = copy.deepcopy(getattr(config, "responses", {}) or {})
    for source in (options or {}, overrides):
        for key, value in source.items():
            if key in {"reasoning", "text"} and isinstance(value, dict):
                result[key] = {**(result.get(key) or {}), **copy.deepcopy(value)}
            else:
                result[key] = copy.deepcopy(value)
    extra = result.pop("extra_body", {}) or {}
    if not isinstance(extra, dict):
        raise ValueError("Responses extra_body must be an object")
    overlap = result.keys() & extra.keys()
    if overlap:
        raise ValueError(f"Duplicate Responses options in extra_body: {sorted(overlap)}")
    result.update(extra)
    try:
        json.dumps(result, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ValueError("Responses options must contain finite JSON values") from exc
    forbidden = result.keys() & RESERVED
    if forbidden:
        raise ValueError(
            f"Responses options cannot override client-owned fields: {sorted(forbidden)}"
        )
    if result.get("previous_response_id") and result.get("conversation"):
        raise ValueError("previous_response_id and conversation are mutually exclusive")
    if result.get("background") and result.get("store") is False:
        raise ValueError("Background Responses require store=true")
    return result


def adapt_options(config: Any, payload: dict, diagnostics: list, *, subscription: bool) -> dict:
    provider = official_provider(config)
    result = copy.deepcopy(payload)

    def omit(key: str, reason: str) -> None:
        value = result.pop(key)
        diagnostics.append(Diagnostic(key, value, None, reason, "ignored"))

    allowed = SUPPORTED.get(provider)
    for key in ("ocr_options", "enable_thinking"):
        if key in result and provider not in {"qwen", "custom"}:
            omit(key, f"{key} is a Qwen extension")
    if allowed:
        for key in list(result):
            if key not in allowed:
                if key in {"previous_response_id", "conversation", "background"} and result[key]:
                    raise ValueError(f"{provider} does not support Responses {key}")
                omit(key, f"{provider} does not implement this Responses option")
    reasoning = result.get("reasoning")
    if isinstance(reasoning, dict) and provider in {"deepseek", "moonshot", "qwen"}:
        for key in list(reasoning):
            if key != "effort":
                diagnostics.append(
                    Diagnostic(
                        f"reasoning.{key}",
                        reasoning.pop(key),
                        None,
                        f"{provider} does not document this reasoning control",
                        "ignored",
                    )
                )
    if provider == "deepseek" and isinstance(result.get("text"), dict):
        value = result["text"].pop("verbosity", None)
        if value is not None:
            diagnostics.append(
                Diagnostic("text.verbosity", value, None, "DeepSeek ignores verbosity", "ignored")
            )
    if provider == "moonshot":
        if result.get("tool_choice", "auto") != "auto":
            raise ValueError("Kimi Responses supports tool_choice=auto only")
        if "include" in result:
            supported = {"web_search_call.action.sources", "web_search_call.results"}
            requested = result["include"] or []
            result["include"] = [item for item in requested if item in supported]
            if requested != result["include"]:
                diagnostics.append(
                    Diagnostic(
                        "include",
                        requested,
                        result["include"],
                        "Kimi supports search includes only",
                        "ignored",
                    )
                )
    if subscription:
        if (
            result.get("background")
            or result.get("previous_response_id")
            or result.get("conversation")
        ):
            raise ValueError("ChatGPT subscription requires synchronous requests with full history")
        result["store"] = False
        for key in ("temperature", "top_p", "max_output_tokens"):
            if key in result:
                omit(key, "ChatGPT subscription backend does not accept this option")
    tool_types = TOOL_TYPES.get(provider)
    pending_tools = list(result.get("tools") or [])
    while pending_tools:
        tool = pending_tools.pop()
        if tool.get("type") == "namespace":
            pending_tools.extend(tool.get("tools") or [])
        kind = tool.get("type", "")
        if kind.startswith("computer"):
            raise ValueError("Computer use is outside this integration")
        # Dropping a tool changes the task, unlike dropping a sampling hint.
        if tool_types is not None and kind not in tool_types:
            raise ValueError(f"{provider} does not support Responses tool type {kind}")
        if (
            provider in {"deepseek", "moonshot"}
            and kind == "custom"
            and tool.get("name") != "apply_patch"
        ):
            raise ValueError(f"{provider} custom tools support apply_patch only")
    return result


def sdk_options(payload: dict) -> dict:
    """Keep native extensions on the wire without passing unknown SDK keywords."""
    known = {key: value for key, value in payload.items() if key in SDK_FIELDS}
    extensions = {key: value for key, value in payload.items() if key not in SDK_FIELDS}
    if extensions:
        known["extra_body"] = extensions
    return known
