"""One request boundary for every application surface."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from typing import Any

from .adapters import chat_completions, responses
from .capabilities import UNSET, Diagnostic, api_format, endpoint, official_provider, parameters
from .types import Result

logger = logging.getLogger(__name__)
_END = object()
DEFAULT_OPENAI_USER_AGENT = "Mozilla/5.0"


def openai_default_headers() -> dict[str, str]:
    return {"User-Agent": os.environ.get("VULNCLAW_LLM_USER_AGENT", DEFAULT_OPENAI_USER_AGENT)}


def subscription_enabled(config: Any) -> bool:
    if (
        not getattr(config, "chatgpt_auto_proxy", False)
        or getattr(config, "auth_mode", "") != "oauth"
    ):
        return False
    from vulnclaw.llm.token_provider import load_oauth_tokens

    return load_oauth_tokens().get("flow") == "chatgpt"


def make_openai_client(api_key: str, base_url: str, timeout: float | None = None):
    from openai import OpenAI

    kwargs: dict[str, Any] = {
        "api_key": api_key or "placeholder",
        "base_url": base_url,
        "default_headers": openai_default_headers(),
        "max_retries": 0,
    }
    if timeout is not None:
        kwargs["timeout"] = timeout
    return OpenAI(**kwargs)


def make_client(config: Any, *, api_key: Any = UNSET, timeout: float | None = None):
    from vulnclaw.llm.token_provider import resolve_llm_token

    if subscription_enabled(config):
        if api_format(config) != "responses":
            raise ValueError("ChatGPT subscription proxy requires api_format=responses")
        from .chatgpt_proxy import ensure_proxy_running

        return make_openai_client("local-proxy", ensure_proxy_running(), timeout)
    token = resolve_llm_token(config) if api_key is UNSET else api_key
    return make_openai_client(token, endpoint(config), timeout)


def build_request(
    config: Any,
    messages: list[dict],
    tools: list[dict] | None = None,
    *,
    max_tokens: Any = UNSET,
    temperature: Any = UNSET,
    top_p: Any = UNSET,
    reasoning_effort: Any = UNSET,
    response_format: dict | None = None,
    diagnostics: list | None = None,
) -> dict:
    route = endpoint(config)  # Validate routing before any network activity.
    kwargs, notices = parameters(
        config,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        reasoning_effort=reasoning_effort,
        subscription=subscription_enabled(config),
    )
    if subscription_enabled(config):
        route = "ChatGPT subscription backend (local Responses proxy)"
    notices.insert(
        0,
        Diagnostic(
            "api_format",
            getattr(config, "api_format", None),
            api_format(config),
            f"Effective API base: {route}",
        ),
    )
    if diagnostics is not None:
        diagnostics.extend(notices)
    for notice in notices:
        logger.info(
            "LLM %s: %r -> %r (%s)",
            notice.parameter,
            notice.requested,
            notice.effective,
            notice.reason,
        )
    kwargs["model"] = config.model
    protocol = api_format(config)
    flat_tools = responses.tools_for_wire(
        tools or [],
        limit=128 if official_provider(config) == "openai" else None,
    )
    if protocol == "responses":
        instructions, items = responses.input_items(messages)
        kwargs["input"] = items
        if instructions:
            kwargs["instructions"] = instructions
        if flat_tools:
            kwargs["tools"] = flat_tools
        if response_format is not None:
            if response_format.get("type") == "json_schema":
                schema = response_format.get("json_schema", response_format)
                kwargs["text"] = {"format": {"type": "json_schema", **schema}}
            else:
                kwargs["text"] = {"format": response_format}
    else:
        if flat_tools and official_provider(config) == "openai" and config.model == "gpt-6-astra":
            raise ValueError("GPT-6 Astra tool calling requires api_format=responses")
        kwargs["messages"] = chat_completions.messages_for_wire(messages)
        if flat_tools:
            kwargs["tools"] = [
                {"type": "function", "function": {k: v for k, v in tool.items() if k != "type"}}
                for tool in flat_tools
            ]
        if response_format is not None:
            kwargs["response_format"] = response_format
    return kwargs


def create(client: Any, config: Any, kwargs: dict) -> Result:
    adapter = responses if api_format(config) == "responses" else chat_completions
    return _authenticated_request(client, config, lambda: adapter.create(client, kwargs))


def start_stream(client: Any, config: Any, kwargs: dict) -> Any:
    adapter = responses if api_format(config) == "responses" else chat_completions
    return _authenticated_request(client, config, lambda: adapter.stream(client, kwargs))


def _authenticated_request(client: Any, config: Any, request: Any) -> Any:
    try:
        return request()
    except Exception as exc:
        if getattr(exc, "status_code", None) != 401 or getattr(config, "auth_mode", "") != "oauth":
            raise
        from vulnclaw.llm.token_provider import refresh_llm_token

        token = refresh_llm_token(config)
        if not subscription_enabled(config):
            client.api_key = token
        # Same endpoint/protocol. A second rejection goes directly to the caller.
        return request()


def complete(
    config: Any,
    messages: list[dict],
    tools: list[dict] | None = None,
    *,
    client: Any = None,
    **options: Any,
) -> Result:
    kwargs = build_request(config, messages, tools, **options)
    owned = client is None
    client = client or make_client(config)
    try:
        return create(client, config, kwargs).require_complete()
    finally:
        if owned:
            client.close()


def _next(iterator: Any) -> Any:
    return next(iterator, _END)


async def await_stream_open(pending: Any) -> Any:
    """Close a stream that finishes opening after its caller has been cancelled."""
    future = asyncio.ensure_future(pending)
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        loop = asyncio.get_running_loop()

        def close_late(done):
            if done.cancelled() or done.exception() is not None:
                return
            close = getattr(done.result(), "close", None)
            if callable(close):
                if inspect.iscoroutinefunction(close):
                    loop.create_task(close())
                else:
                    loop.run_in_executor(None, close)

        future.add_done_callback(close_late)
        raise


async def stream_result(
    client: Any, config: Any, kwargs: dict, sink: Any, *, open_stream: Any = None
) -> Result:
    adapter = responses if api_format(config) == "responses" else chat_completions
    collector = adapter.Collector()
    response = (
        await open_stream()
        if open_stream
        else await await_stream_open(asyncio.to_thread(start_stream, client, config, kwargs))
    )
    if inspect.isawaitable(response):
        response = await response
    try:

        async def events():
            if hasattr(response, "__aiter__"):
                async for event in response:
                    yield event
            else:
                iterator = iter(response)
                while True:
                    event = await asyncio.to_thread(_next, iterator)
                    if event is _END:
                        break
                    yield event

        async for event in events():
            deltas = collector.feed(event)
            if isinstance(deltas, tuple):
                deltas = [deltas]
            for kind, text in deltas or []:
                if kind == "thinking":
                    sink.on_thinking_token(text)
                else:
                    sink.on_content_token(text)
        return collector.finish()
    except (Exception, asyncio.CancelledError) as exc:
        exc.result = getattr(collector, "result", None) or Result(
            [],
            content=getattr(collector, "partial_content", "")
            or getattr(collector, "message", {}).get("content", ""),
            status="interrupted",
        )
        raise
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            result = await asyncio.to_thread(close)
            if inspect.isawaitable(result):
                await result
        sink.on_stream_end()


def format_attempts(config: Any, schema: dict) -> list[dict | None]:
    provider = official_provider(config)
    formats: list[dict | None] = []
    if provider == "openai" or (
        api_format(config) == "responses" and provider in {"deepseek", "moonshot"}
    ):
        formats.append({"type": "json_schema", "json_schema": schema})
    if provider in {"openai", "deepseek"} or api_format(config) == "chat_completions":
        formats.append({"type": "json_object"})
    formats.append(None)
    return formats
