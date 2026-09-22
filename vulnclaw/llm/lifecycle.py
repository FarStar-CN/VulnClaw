"""Responses lifecycle operations for callers that manage server-side state.

The Agent loop owns local history. Integrations using background responses or
server conversations use these operations and retain the returned response ID.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .adapters.responses import input_items, parse
from .capabilities import api_format, official_provider
from .client import (
    _authenticated_request,
    await_stream_open,
    build_request,
    create,
    make_client,
    stream_result,
    subscription_enabled,
)
from .types import Result, as_dict


def submit(
    config: Any,
    messages: list[dict] | str,
    tools: list[dict] | None = None,
    *,
    client: Any = None,
    **options: Any,
) -> Result:
    """Create a response, including queued/in-progress background responses."""
    request = build_request(config, messages, tools, **options)
    owned = client is None
    client = client or make_client(config)
    try:
        return create(client, config, request)
    finally:
        if owned:
            client.close()


def _validate_operation(config: Any, method: str) -> None:
    if api_format(config) != "responses":
        raise ValueError("Responses lifecycle requires api_format=responses")
    if subscription_enabled(config):
        raise ValueError("ChatGPT subscription does not expose Responses lifecycle operations")
    provider = official_provider(config)
    if provider in {"deepseek", "moonshot"}:
        raise ValueError(f"{provider} Responses is stateless; {method} is unavailable")
    if provider in {"qwen", "zhipu"} and method in {
        "cancel",
        "compact",
        "input_tokens.count",
        "resume_stream",
    }:
        raise ValueError(f"{provider} does not document Responses {method}")


def _operation(config: Any, method: str, *, client: Any = None, **kwargs: Any) -> Any:
    _validate_operation(config, method)
    owned = client is None
    client = client or make_client(config)
    try:
        resource = client.responses
        for part in method.split("."):
            resource = getattr(resource, part)
        value = _authenticated_request(client, config, lambda: resource(**kwargs))
        if method == "input_items.list":
            return [as_dict(item) for item in value]
        return None if method == "delete" else as_dict(value)
    finally:
        if owned:
            client.close()


def retrieve(config: Any, response_id: str, *, client: Any = None, **options: Any) -> Result:
    if options.get("stream"):
        raise ValueError("Use resume_stream for streaming retrieval")
    return parse(_operation(config, "retrieve", client=client, response_id=response_id, **options))


def cancel(config: Any, response_id: str, *, client: Any = None) -> Result:
    return parse(_operation(config, "cancel", client=client, response_id=response_id))


def delete(config: Any, response_id: str, *, client: Any = None) -> None:
    return _operation(config, "delete", client=client, response_id=response_id)


def list_input_items(
    config: Any, response_id: str, *, client: Any = None, **options: Any
) -> list[dict]:
    return _operation(config, "input_items.list", client=client, response_id=response_id, **options)


def compact(config: Any, messages: list[dict] | str, *, client: Any = None, **options: Any) -> dict:
    instructions, items = input_items(messages)
    if instructions:
        options.setdefault("instructions", instructions)
    return _operation(config, "compact", client=client, model=config.model, input=items, **options)


def count_input_tokens(
    config: Any,
    messages: list[dict] | str,
    tools: list[dict] | None = None,
    *,
    client: Any = None,
    **options: Any,
) -> dict:
    """Count the adapted request input with a provider that implements this endpoint."""
    request = build_request(config, messages, tools, **options)
    fields = {
        "model",
        "input",
        "instructions",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "reasoning",
        "text",
        "conversation",
        "previous_response_id",
        "truncation",
    }
    return _operation(
        config,
        "input_tokens.count",
        client=client,
        **{key: value for key, value in request.items() if key in fields},
    )


async def resume_stream(
    config: Any,
    response_id: str,
    sink: Any,
    *,
    starting_after: int | None = None,
    client: Any = None,
) -> Result:
    """Explicitly resume a stored background response; never replay local tools."""
    _validate_operation(config, "resume_stream")
    owned = client is None
    client = client or make_client(config)
    options = {"response_id": response_id, "stream": True}
    if starting_after is not None:
        options["starting_after"] = starting_after

    async def open_stream():
        return await await_stream_open(
            asyncio.to_thread(
                _authenticated_request,
                client,
                config,
                lambda: client.responses.retrieve(**options),
            )
        )

    try:
        return await stream_result(client, config, {}, sink, open_stream=open_stream)
    finally:
        if owned:
            await asyncio.to_thread(client.close)
