"""Discover models through the configured LLM connection."""

from __future__ import annotations

from typing import Any

from .client import make_client, make_openai_client


def fetch_provider_models(base_url: str, api_key: str, timeout: float = 10.0, *, llm_config: Any = None) -> list[str]:
    """Fetch available models from a provider's OpenAI-compatible API.

    Uses the OpenAI SDK's ``client.models.list()`` endpoint.
    Returns a sorted list of model ID strings.  Returns an empty list
    on any error (network, auth, timeout, etc.).
    """
    if not base_url or (not api_key and llm_config is None):
        return []
    try:
        client = (make_client(llm_config, timeout=timeout) if llm_config is not None
                  else make_openai_client(api_key=api_key, base_url=base_url, timeout=timeout))
        with client:
            return sorted(m.id for m in client.models.list() if m.id)
    except Exception:
        return []
