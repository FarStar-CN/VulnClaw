"""Model discovery preserves routing, failure handling, and connection cleanup."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from vulnclaw.config.schema import LLMConfig
from vulnclaw.llm import discovery


@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("failure", [None, "creation", "listing"])
def test_discovery_uses_connection_and_closes_it(monkeypatch, configured, failure):
    client = MagicMock()
    client.__enter__.return_value = client
    client.models.list.return_value = [
        SimpleNamespace(id="z-model"),
        SimpleNamespace(id=""),
        SimpleNamespace(id="a-model"),
    ]
    factory = MagicMock(return_value=client)
    if failure == "creation":
        factory.side_effect = RuntimeError("credentials unavailable")
    elif failure == "listing":
        client.models.list.side_effect = RuntimeError("provider unavailable")
    unused_factory = MagicMock(side_effect=AssertionError("unexpected connection path"))
    monkeypatch.setattr(discovery, "make_client", factory if configured else unused_factory)
    monkeypatch.setattr(discovery, "make_openai_client", unused_factory if configured else factory)
    config = LLMConfig(auth_mode="oauth") if configured else None
    key = "" if configured else "test-key"

    result = discovery.fetch_provider_models(
        "https://example.test/v1", key, timeout=3.0, llm_config=config,
    )

    assert result == ([] if failure else ["a-model", "z-model"])
    if configured:
        factory.assert_called_once_with(config, timeout=3.0)
    else:
        factory.assert_called_once_with(
            api_key=key, base_url="https://example.test/v1", timeout=3.0,
        )
    unused_factory.assert_not_called()
    if failure == "creation":
        client.__enter__.assert_not_called()
    else:
        client.__exit__.assert_called_once()


@pytest.mark.parametrize("base_url,key", [("", "test-key"), ("https://example.test", "")])
def test_discovery_without_connection_details_does_not_connect(monkeypatch, base_url, key):
    factory = MagicMock(side_effect=AssertionError("unexpected network request"))
    monkeypatch.setattr(discovery, "make_client", factory)
    monkeypatch.setattr(discovery, "make_openai_client", factory)

    assert discovery.fetch_provider_models(base_url, key) == []
    factory.assert_not_called()
