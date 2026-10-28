"""Settings changes reach the live runtime and its cached consumers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from vulnclaw.agent.core import AgentCore
from vulnclaw.agent.exec_gate import GateRequest, get_execution_gate, reset_execution_gate
from vulnclaw.application import ApplicationRuntime, ApplicationSession
from vulnclaw.config.llm_utils import build_chat_completion_kwargs
from vulnclaw.config.schema import MCPServerConfig, MCPTransportConfig, VulnClawConfig
from vulnclaw.management_service import execute_management
from vulnclaw.mcp.lifecycle import MCPLifecycleManager


@pytest.fixture
def session(monkeypatch, tmp_path):
    reset_execution_gate()
    monkeypatch.setattr(AgentCore, "_report_kb_status", lambda _self: None)
    monkeypatch.setattr("vulnclaw.config_service.save_config", lambda _config: None)
    config = VulnClawConfig()
    config.session.output_dir = tmp_path / "old-output"
    config.mcp.servers["fixture"] = MCPServerConfig(
        name="fixture",
        transport=MCPTransportConfig(
            type="streamable-http", url="http://fixture.invalid/old",
        ),
    )
    manager = MCPLifecycleManager(config)
    # Represent an already attached server without starting processes or network calls.
    manager.registry.register_server("fixture")
    manager.registry.set_server_running("fixture", True)
    manager.registry.set_server_health("fixture", "healthy")
    manager.registry.register_tool("fixture", {"name": "fixture_tool"})

    def start(name, _config):
        manager.registry.set_server_running(name, True)
        manager.registry.set_server_health(name, "healthy")
        manager.registry.register_tool(name, {"name": name + "_tool"})
        return True

    monkeypatch.setattr(manager, "_start_server", Mock(side_effect=start))
    agent = AgentCore(config, manager)
    application = ApplicationSession(lambda *_args, **_fields: None)
    application.runtime = ApplicationRuntime(config, manager, agent)
    get_execution_gate(config)
    yield application
    reset_execution_gate()


async def test_saved_model_settings_recreate_client_and_request_parameters(session, monkeypatch):
    client = SimpleNamespace(api_key="synthetic-key")
    create = Mock(return_value=client)
    monkeypatch.setattr("vulnclaw.agent.core.make_async_openai_client", create)
    session.agent._client = SimpleNamespace(close=AsyncMock())
    await execute_management(session, "config.update", {"changes": {
        "llm.provider": "custom", "llm.base_url": "https://fixture.invalid/v1",
        "llm.model": "replacement", "llm.api_keys": ["synthetic-key"],
        "llm.max_tokens": 512, "llm.temperature": 0.4,
    }})
    assert session.agent._get_client() is client
    create.assert_called_once_with(
        api_key="synthetic-key", base_url="https://fixture.invalid/v1",
    )
    assert build_chat_completion_kwargs(session.agent.config.llm, []) == {
        "model": "replacement", "messages": [], "max_tokens": 512, "temperature": 0.4,
    }


async def test_saved_context_limits_reach_live_context_and_archive(session):
    await execute_management(session, "config.update", {"changes": {
        "session.context_hot_max_messages": 8, "session.context_hot_max_tokens": 2048,
        "session.memory_search_max_chars": 1024, "session.memory_archive_max_bytes": 1048576,
        "session.memory_archive_max_files": 2,
    }})
    context = session.agent.context
    assert (context.max_history, context.max_tokens, context.search_max_chars) == (8, 2048, 1024)
    assert context.memory_store.archive_max_bytes == 1048576
    assert context.memory_store.archive_max_files == 2


async def test_saved_permission_recon_and_subagent_limits_reach_next_consumers(session):
    from vulnclaw.agent.recon_tools import _get_recon_cfg
    from vulnclaw.agent.subagent.integration import get_task_runtime
    from vulnclaw.agent.subagent.solve import reset_root_context
    from vulnclaw.agent.tool_call_manager import _resolve_parallel_settings

    await execute_management(session, "config.update", {"changes": {
        "safety.permission_mode": "read_only", "safety.tool_max_concurrent": 2,
        "recon.http_timeout": 4.0, "recon.space_size": 7,
        "subagent.max_background_groups": 1,
    }})
    assert get_execution_gate().mode == "read_only"
    assert _resolve_parallel_settings(session.agent) == (True, 2)
    recon = _get_recon_cfg(session.agent)
    assert (recon.http_timeout, recon.space_size) == (4.0, 7)
    # Each new solve resets its task runtime before reading the current limits.
    reset_root_context(session.agent)
    assert get_task_runtime(session.agent).service.max_background_groups == 1


async def test_saved_approval_timeout_reaches_gate(session):
    result = await execute_management(session, "config.update", {
        "changes": {"safety.approval_timeout_seconds": 7},
    })
    assert result["config"]["safety"]["approval_timeout_seconds"] == 7
    assert get_execution_gate().timeout_seconds == 7


async def test_removed_trusted_prefix_stops_auto_approval(session):
    session.config.safety.trusted_commands = ["nmap"]
    session.config.safety.permission_mode = "auto_review"
    reset_execution_gate()
    gate = get_execution_gate(session.config)
    request = GateRequest(kind="shell", display="nmap fixture.invalid")
    # Only ask for an approval decision; never execute this command.
    assert (await gate.authorize(request)).approved
    await execute_management(session, "config.update", {
        "changes": {"safety.trusted_commands": []},
    })
    assert not (await gate.authorize(request)).approved


async def test_disabling_mcp_removes_its_tools(session):
    assert session.runtime.mcp_manager.get_tool_schemas()
    result = await execute_management(session, "config.update", {
        "changes": {"mcp.servers.fixture.enabled": False},
    })
    assert result["config"]["mcp"]["servers"]["fixture"]["enabled"] is False
    assert session.runtime.mcp_manager.get_tool_schemas() == []


async def test_mcp_transport_edits_reach_manager(session):
    url = "http://fixture.invalid/new"
    await execute_management(session, "config.update", {
        "changes": {"mcp.servers.fixture.transport.url": url},
    })
    assert session.runtime.mcp_manager.config.mcp.servers["fixture"].transport.url == url
    session.runtime.mcp_manager._start_server.assert_called_once()


async def test_mcp_concurrency_edits_reach_existing_gate(session):
    manager = session.runtime.mcp_manager
    manager._server_call_gate("fixture")
    await execute_management(session, "config.update", {
        "changes": {"safety.tool_max_concurrent": 2},
    })
    assert manager._server_call_gate("fixture")._value == 2
    manager._start_server.assert_not_called()


@pytest.mark.parametrize("operation", ["enable", "add", "remove"])
async def test_saved_mcp_server_changes_reconcile_registration(session, operation):
    manager = session.runtime.mcp_manager
    if operation == "enable":
        await execute_management(session, "config.update", {
            "changes": {"mcp.servers.fixture.enabled": False},
        })
        changes = {"mcp.servers.fixture.enabled": True}
        name = "fixture"
    elif operation == "add":
        changes = {"mcp.servers.added": {
            "name": "added", "transport": {"type": "stdio", "command": "fixture"},
        }}
        name = "added"
    else:
        changes = {"mcp.servers": {}}
        name = "fixture"
    await execute_management(session, "config.update", {"changes": changes})
    assert (name in manager.registry.get_all_servers()) is (operation != "remove")
    assert (name + "_tool" in manager.list_available_tools()) is (operation != "remove")


async def test_failed_mcp_restart_drops_stale_tools_and_reports_the_error(session):
    manager = session.runtime.mcp_manager
    manager._runtime_tool_aliases[("fixture", "fixture_tool")] = "old_tool"
    manager._start_server.side_effect = RuntimeError("synthetic connection failure")
    await execute_management(session, "config.update", {
        "changes": {"mcp.servers.fixture.transport.url": "http://fixture.invalid/new"},
    })
    assert manager.get_tool_schemas() == []
    assert manager._runtime_tool_aliases == {}
    assert manager.registry.get_all_servers()["fixture"].error == "synthetic connection failure"


@pytest.mark.parametrize("key", ["synthetic-key", ""])
async def test_config_update_returns_current_model_readiness(session, key):
    session.config.llm.api_key = "" if key else "synthetic-old-key"
    assert session.state_snapshot()["runtime"]["config_ready"] is (not bool(key))
    document, state = await session._execute_control_operation("config.update", {
        "changes": {"llm.api_key": key, "llm.model": "replacement"},
    })
    assert document["config"]["llm"]["api_key"] == ("***" if key else "")
    assert state["runtime"]["config_ready"] is bool(key)
    assert state["runtime"]["model"] == "replacement"


@pytest.mark.parametrize("store", ["memory", "vault"])
async def test_output_directory_edits_move_future_writes(session, tmp_path, store):
    context = session.agent.context
    vault = context.ensure_vault()
    output = tmp_path / "new-output"
    result = await execute_management(session, "config.update", {
        "changes": {"session.output_dir": str(output)},
    })
    assert result["config"]["session"]["output_dir"] == str(output)
    if store == "memory":
        context.memory_store.archive_messages([{"role": "user", "content": "synthetic message"}])
        relative = "memory/conversation_archive.jsonl"
    else:
        vault.persist()
        relative = "context_vault.jsonl"
    assert (output / relative).exists()


async def test_output_directory_change_preserves_old_files_and_loads_destination_memory(
    session, tmp_path,
):
    from vulnclaw.agent.memory import MemoryStore

    context = session.agent.context
    old_store = context.memory_store
    old_store.save("old", "old value")
    old_archive = old_store.store_dir / "long_term.json"
    before = old_archive.read_bytes()
    output = tmp_path / "new-output"
    MemoryStore(output / "memory").save("destination", "saved destination value")
    context.add_user_message("keep the current conversation")
    await execute_management(session, "config.update", {
        "changes": {"session.output_dir": str(output)},
    })
    assert old_archive.read_bytes() == before
    assert context.memory_store.retrieve("destination") == "saved destination value"
    assert context.memory_store.session_id == old_store.session_id
    assert context.messages[-1]["content"] == "keep the current conversation"
