"""Documented endpoint rules. Unknown combinations are never silently certified."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)
UNSET = object()
# Checked 2026-09-20 against each linked model/endpoint reference.
SOURCES = {
    "openai": "https://developers.openai.com/api/docs/models/",
    "deepseek": "https://api-docs.deepseek.com/api/create-response/",
    "qwen": "https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses",
    "moonshot": "https://platform.kimi.com/docs/api/responses",
    "zhipu": "https://docs.bigmodel.cn/cn/coding-plan/tool/codex",
}
# Exact slugs (plus dated snapshots), never arbitrary gpt-5*/o* prefixes.
OPENAI_EFFORTS = {
    **dict.fromkeys(("o1", "o3", "o3-mini", "o4-mini"), ("low", "medium", "high")),
    **dict.fromkeys(("gpt-5", "gpt-5-mini", "gpt-5-nano"), ("minimal", "low", "medium", "high")),
    "gpt-5.1": ("none", "low", "medium", "high"),
    **dict.fromkeys(("gpt-5.2", "gpt-5.4", "gpt-5.5"), ("none", "low", "medium", "high", "xhigh")),
    **dict.fromkeys(
        ("gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"),
        ("none", "low", "medium", "high", "xhigh", "max"),
    ),
    "gpt-6-astra": ("low", "medium", "high", "xhigh", "max"),
}
QWEN_38 = {"qwen3.8-max", "qwen3.8-max-0902", "qwen3.8-flash", "qwen3.8-2.4t-a95b", "qwen3.8-27b"}
NON_REASONING_OPENAI = {
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "gpt-3.5-turbo",
}


def model_slug(model: str) -> str:
    return re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model.lower())


RESPONSES_PROVIDERS = frozenset({"openai", "deepseek", "zhipu", "qwen", "moonshot", "custom"})


def api_format(config: Any) -> str:
    explicit = getattr(config, "api_format", None)
    if explicit is not None:
        if explicit not in {"responses", "chat_completions"}:
            raise ValueError(f"Unknown llm.api_format: {explicit}")
        return explicit
    return "responses" if config.provider in RESPONSES_PROVIDERS else "chat_completions"


def endpoint(config: Any) -> str:
    from vulnclaw.config.url_utils import normalize_base_url

    base = normalize_base_url(config.base_url)
    parsed = urlsplit(base)
    if parsed.path.endswith(("/responses", "/chat/completions")):
        raise ValueError(
            "llm.base_url must be the API base, without /responses or /chat/completions"
        )
    if api_format(config) == "responses":
        if parsed.hostname in {"open.bigmodel.cn", "api.z.ai"}:
            if parsed.path == "/api/paas/v4":
                return base.removesuffix("/paas/v4") + "/v1"
            if "/coding/" in parsed.path:
                raise ValueError(
                    "GLM Coding endpoint requires explicit api_format=chat_completions"
                )
        if parsed.path == "/api/v2/apps/protocols/compatible-mode/v1":
            raise ValueError("Use Qwen base_url ending in /compatible-mode/v1")
    return base


def official_provider(config: Any) -> str:
    """A model name on an arbitrary gateway is not an official provider contract."""
    host = urlsplit(getattr(config, "base_url", "")).hostname or ""
    provider = str(config.provider)
    hosts = {
        "openai": {"api.openai.com"},
        "deepseek": {"api.deepseek.com"},
        "zhipu": {"open.bigmodel.cn", "api.z.ai"},
        "moonshot": {"api.moonshot.cn", "api.moonshot.ai"},
        "qwen": {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"},
    }
    if host in hosts.get(provider, set()):
        return provider
    if provider == "qwen" and host.endswith(".maas.aliyuncs.com"):
        return provider
    return "custom"


@dataclass(frozen=True)
class Diagnostic:
    parameter: str
    requested: Any
    effective: Any
    reason: str
    support: str = "unverified"
    source: str = ""
    checked_at: str = "2026-09-20"


def parameters(
    config: Any,
    *,
    max_tokens: Any = UNSET,
    temperature: Any = UNSET,
    top_p: Any = UNSET,
    reasoning_effort: Any = UNSET,
    subscription: bool = False,
) -> tuple[dict[str, Any], list[Diagnostic]]:
    protocol = api_format(config)
    provider = official_provider(config)
    model = model_slug(str(config.model))
    values = {
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "reasoning_effort": reasoning_effort,
    }
    values = {k: getattr(config, k, None) if v is UNSET else v for k, v in values.items()}
    # Reuse the same field validators for per-call overrides and persisted config.
    from vulnclaw.config.schema import DEFAULT_REASONING_EFFORT, LLMConfig

    values["reasoning_effort"] = values["reasoning_effort"] or DEFAULT_REASONING_EFFORT
    # The application selector keeps its five rungs; native Responses options
    # also expose the endpoint's none/minimal levels.
    native_effort = values["reasoning_effort"]
    if native_effort not in {"none", "minimal", "low", "medium", "high", "xhigh", "max"}:
        raise ValueError(f"Invalid reasoning effort: {native_effort}")
    validated = LLMConfig.model_validate({**values, "reasoning_effort": "low"})
    values = {key: getattr(validated, key) for key in values}
    effort = values["reasoning_effort"] = native_effort

    diagnostics: list[Diagnostic] = []

    def adjust(key: str, value: Any, reason: str) -> None:
        if values[key] != value:
            diagnostics.append(
                Diagnostic(
                    key,
                    values[key],
                    value,
                    reason,
                    "unverified"
                    if "unverified" in reason.lower()
                    else "ignored"
                    if value is None
                    else "supported",
                    SOURCES.get(provider, ""),
                )
            )
        values[key] = value

    if subscription:
        if protocol != "responses":
            raise ValueError("ChatGPT subscription proxy requires api_format=responses")
        if values["max_tokens"] is not None:
            adjust("max_tokens", None, "ChatGPT backend does not accept output limits")
        for key in ("temperature", "top_p"):
            adjust(key, None, "ChatGPT subscription backend does not accept this parameter")
    if provider == "custom":
        diagnostics.append(
            Diagnostic("capabilities", None, "unverified", "Custom endpoint contract")
        )
    if provider == "moonshot" and protocol == "responses" and model != "kimi-k3":
        raise ValueError("Kimi Responses currently documents kimi-k3 only; explicitly select Chat")
    if provider in {"moonshot", "zhipu"} and model in {
        "kimi-k3", "glm-5.3", "glm-5.3-flash", "glm-5.3-flashx",
    }:
        mapped = {"none": "low", "minimal": "low", "medium": "high", "xhigh": "max"}.get(effort, effort)
        adjust("reasoning_effort", mapped, "Mapping to native low/high/max")
        if provider == "moonshot":
            for key in ("temperature", "top_p"):
                adjust(key, None, "Kimi K3 sampling is not configurable on this endpoint")
    if provider == "deepseek" and model in {"deepseek-flash", "deepseek-v4-pro"}:
        mapped = {"minimal": "low", "medium": "high", "xhigh": "high"}.get(effort, effort)
        adjust("reasoning_effort", mapped, "Documented DeepSeek effort mapping")
        if effort != "none":
            adjust("temperature", None, "DeepSeek ignores temperature in thinking mode")
        elif values["top_p"] is not None:
            adjust("top_p", None, "DeepSeek fixes top_p to 1 in non-thinking mode")
        if effort != "none" and values["top_p"] is not None and values["top_p"] < 0.95:
            adjust("top_p", 0.95, "DeepSeek thinking top_p lower bound")
    if provider == "qwen" and model in QWEN_38:
        mapped = {"minimal": "low", "high": "xhigh", "max": "xhigh"}.get(effort, effort)
        adjust("reasoning_effort", mapped, "Documented Qwen3.8 effort mapping")
    if provider == "qwen":
        if values["temperature"] is not None and values["temperature"] >= 2:
            raise ValueError("Qwen temperature must be less than 2")
        if values["top_p"] == 0:
            raise ValueError("Qwen top_p must be greater than 0")
    if provider == "openai":
        supported = OPENAI_EFFORTS.get(model)
        if supported:
            # Our weakest rung is "low", which every documented effort set
            # accepts; a rung above the model's top one is clamped down to it.
            if effort not in supported:
                adjust(
                    "reasoning_effort",
                    supported[0] if effort in {"none", "minimal"} else supported[-1],
                    "VulnClaw mapping to a documented model effort",
                )
            # Sampling is available only in the documented non-thinking mode.
            if values["reasoning_effort"] != "none":
                for key in ("temperature", "top_p"):
                    adjust(key, None, "Sampling unavailable with this reasoning model")
        elif model in NON_REASONING_OPENAI:
            adjust("reasoning_effort", None, "This model has no reasoning effort control")
        else:
            diagnostics.append(
                Diagnostic("model", model, "unverified", "Unknown OpenAI model capabilities")
            )
    if provider == "zhipu" and protocol == "responses":
        diagnostics.append(Diagnostic(
            "capabilities", None, "unverified",
            "GLM Responses route/effort documented; remaining parameter contract unverified",
            source=SOURCES["zhipu"],
        ))
    limits = {"deepseek-flash": 393216, "deepseek-v4-pro": 393216, "kimi-k3": 1048576}
    ceiling = (
        128000
        if provider == "openai" and model in {"gpt-5.6", "gpt-5.6-sol"}
        else limits.get(model)
        if provider in {"deepseek", "moonshot"}
        else None
    )
    if ceiling and values["max_tokens"] is not None and values["max_tokens"] > ceiling:
        adjust("max_tokens", ceiling, "Clamped to documented model output ceiling")
    budget = values["max_tokens"]
    if budget is not None:
        if provider == "zhipu" or (
            provider == "qwen" and (protocol != "responses" or model not in QWEN_38)
        ):
            diagnostics.append(Diagnostic(
                "max_tokens", budget, budget,
                "Output limit forwarded; total reasoning+answer budget is unverified",
                source=SOURCES.get(provider, ""),
            ))
        if provider == "qwen" and budget < 16:
            raise ValueError("Qwen max_output_tokens must be at least 16")
    kwargs: dict[str, Any] = {}
    for key in ("temperature", "top_p"):
        if values[key] is not None:
            kwargs[key] = values[key]
    if budget is not None:
        budget_key = "max_output_tokens" if protocol == "responses" else "max_tokens"
        if protocol == "chat_completions" and provider == "openai" and model in OPENAI_EFFORTS:
            budget_key = "max_completion_tokens"
        kwargs[budget_key] = budget
    resolved_effort = values["reasoning_effort"]
    if resolved_effort:
        if protocol == "responses":
            kwargs["reasoning"] = {"effort": resolved_effort}
        elif provider in {"openai", "zhipu", "deepseek", "moonshot", "qwen", "custom"}:
            kwargs["reasoning_effort"] = resolved_effort
    if protocol == "responses" and provider in {"openai", "qwen"}:
        kwargs["store"] = False
    if protocol == "responses" and (provider == "openai" or subscription):
        kwargs["include"] = ["reasoning.encrypted_content"]
    if budget is None:
        diagnostics.append(
            Diagnostic(
                "max_tokens", None, None, "Server output budget explicitly omitted", "unsupported"
            )
        )
    return kwargs, diagnostics
