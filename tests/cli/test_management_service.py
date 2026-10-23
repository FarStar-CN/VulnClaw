from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from vulnclaw.config.schema import VulnClawConfig
from vulnclaw.config_service import config_document, update_config
from vulnclaw.management_service import adapter_operation, execute_management


def test_config_document_masks_credentials_and_transport_headers():
    from vulnclaw.config.schema import MCPServerConfig, MCPTransportConfig

    config = VulnClawConfig()
    config.llm.api_key = "stored-test-secret"
    config.llm.api_keys = ["other-test-secret"]
    config.mcp.servers["custom"] = MCPServerConfig(
        name="custom", transport=MCPTransportConfig(type="stdio", env={"X-Custom": "header-secret"}),
    )
    document = config_document(config)
    assert document["config"]["llm"]["api_key"] == "***"
    assert document["config"]["llm"]["api_keys"] == ["***"]
    assert document["config"]["mcp"]["servers"]["custom"]["transport"]["env"] == {"X-Custom": "***"}
    assert config.llm.api_key == "stored-test-secret"
    assert document["schema"]["properties"]["llm"]


def test_partial_config_edit_preserves_omitted_secrets_and_validates_before_save(monkeypatch):
    save = Mock()
    monkeypatch.setattr("vulnclaw.config_service.save_config", save)
    config = VulnClawConfig()
    config.llm.api_key = "stored-test-secret"
    updated = update_config({"session.language": "zh"}, config=config)
    assert updated.llm.api_key == config.llm.api_key
    assert updated.session.language == "zh"
    assert config.session.language != "zh"
    save.assert_called_once_with(updated)
    save.reset_mock()
    with pytest.raises(ValueError):
        update_config({"llm.temperature": {"invalid": True}}, config=config)
    save.assert_not_called()
    with pytest.raises(ValueError, match="unknown configuration path"):
        update_config({"llm.unknown": "bad"}, config=config)


@pytest.mark.parametrize(("name", "text", "operation", "arguments"), [
    ("config", "", "config.document", {}),
    ("language", "zh", "language.set", {"language": "zh"}),
    ("experience", "approve lesson-1", "experience.approve", {"lesson_id": "lesson-1"}),
    ("experience", 'edit one --lesson "new lesson"', "experience.edit", {"lesson_id": "one", "lesson": "new lesson"}),
    ("learn", "run-1", "learn", {"run_name": "run-1"}),
    ("feedback", 'run-1 4 "operator notes"', "feedback", {"run_name": "run-1", "rating": 4, "notes": "operator notes"}),
])
def test_repl_management_commands_share_explicit_operations(name, text, operation, arguments):
    assert adapter_operation(name, text) == (operation, arguments)


async def test_saved_config_applies_to_live_application(monkeypatch):
    monkeypatch.setattr("vulnclaw.config_service.save_config", lambda _config: None)
    application = SimpleNamespace(config=VulnClawConfig(), apply_config=AsyncMock())
    result = await execute_management(application, "config.update", {"changes": {"session.language": "zh"}})
    assert result["config"]["session"]["language"] == "zh"
    application.apply_config.assert_awaited_once()


async def test_long_management_operation_keeps_state_available_and_is_cancelled_on_close(monkeypatch):
    import asyncio
    import io

    from tests.cli.test_session_backend import FakeRuntime, request
    from vulnclaw.session_backend import BackendSession
    from vulnclaw.session_protocol import JsonlWriter, ProtocolError

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def management(*_args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr("vulnclaw.application.execute_management", management)
    backend = BackendSession(JsonlWriter(io.StringIO()), runtime_factory=FakeRuntime)
    await backend.handle(request("initialize", "init"))
    control = asyncio.create_task(backend.handle(request(
        "control", "learn", payload={"operation": "learn", "arguments": {"run_name": "run-1"}},
    )))
    await started.wait()
    await backend.handle(request("get_state", "state"))
    with pytest.raises(ProtocolError, match="management operation"):
        await backend.handle(request("start_task", "task", task_id="t1", payload={
            "task": {"command": "run", "target": "example.test"},
        }))
    await backend.close()
    assert cancelled.is_set()
    assert control.cancelled()


def test_container_edits_preserve_masked_secrets_and_allow_server_deletion(monkeypatch):
    from vulnclaw.config.schema import MCPServerConfig, MCPTransportConfig, VulnClawConfig
    from vulnclaw.config_service import masked_config, update_config

    config = VulnClawConfig()
    config.llm.api_keys = ["synthetic-key-one", "synthetic-key-two"]
    config.mcp.servers["retained"] = MCPServerConfig(name="retained", transport=MCPTransportConfig(type="stdio", command="synthetic", env={"CREDENTIAL": "synthetic-value"}))
    config.mcp.servers["deleted"] = config.mcp.servers["retained"].model_copy(update={"name": "deleted"})
    saved = []
    monkeypatch.setattr("vulnclaw.config_service.save_config", saved.append)
    servers = masked_config(config.model_dump(mode="json"))["mcp"]["servers"]
    del servers["deleted"]
    servers["retained"]["enabled"] = True
    updated = update_config({"mcp.servers": servers, "llm.api_keys": ["***", "replacement"]}, config=config)
    assert "deleted" not in updated.mcp.servers
    assert updated.mcp.servers["retained"].enabled
    assert updated.mcp.servers["retained"].transport.env["CREDENTIAL"] == "synthetic-value"
    assert updated.llm.api_keys == ["synthetic-key-one", "replacement"]
    assert len(saved) == 1


def test_secret_list_edits_retain_the_selected_saved_key_after_removal(monkeypatch):
    monkeypatch.setattr("vulnclaw.config_service.save_config", lambda _config: None)
    config = VulnClawConfig()
    config.llm.api_keys = ["first-synthetic-key", "second-synthetic-key"]
    updated = update_config({"llm.api_keys": [{"$keep": 1}, "replacement"]}, config=config)
    assert updated.llm.api_keys == ["second-synthetic-key", "replacement"]
    assert config.llm.api_keys == ["first-synthetic-key", "second-synthetic-key"]
    assert config_document(updated)["config"]["llm"]["api_keys"] == ["***", "***"]


@pytest.mark.parametrize("reference", [-1, 2, True, "1"])
def test_invalid_saved_key_reference_does_not_save(monkeypatch, reference):
    save = Mock()
    monkeypatch.setattr("vulnclaw.config_service.save_config", save)
    config = VulnClawConfig()
    config.llm.api_keys = ["synthetic-key"]
    with pytest.raises(ValueError, match="invalid saved credential reference"):
        update_config({"llm.api_keys": [{"$keep": reference}]}, config=config)
    save.assert_not_called()
