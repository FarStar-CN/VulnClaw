"""Capability counters describe successful loads and ready MCP services."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from vulnclaw.application import ApplicationSession, _runtime_metadata
from vulnclaw.config.schema import MCPServerConfig, MCPTransportConfig, VulnClawConfig
from vulnclaw.mcp.registry import MCPRegistry


def test_runtime_counts_successfully_loaded_skills_and_ready_servers(monkeypatch):
    import vulnclaw.skills.loader as loader

    monkeypatch.setattr(loader, "list_core_skills", lambda: ["valid", "unreadable"])
    monkeypatch.setattr(loader, "list_specialized_skills", lambda: ["missing"])
    monkeypatch.setattr(loader, "list_custom_skills", lambda: ["custom", "valid"])

    def load(name):
        if name == "unreadable":
            raise OSError("synthetic unreadable file")
        return None if name == "missing" else {"name": name}

    monkeypatch.setattr(loader, "load_skill_by_name", load)
    states = {
        "local": SimpleNamespace(running=False, execution_mode="local", health_status="healthy"),
        "remote": SimpleNamespace(running=True, execution_mode="sse", health_status="healthy"),
        "starting": SimpleNamespace(running=True, execution_mode="sdk", health_status="starting"),
        "placeholder": SimpleNamespace(running=False, execution_mode="placeholder", health_status="unknown"),
        "stopped": SimpleNamespace(running=False, execution_mode="sdk", health_status="healthy"),
        "disabled": SimpleNamespace(running=True, execution_mode="sdk", health_status="healthy"),
        "orphan": SimpleNamespace(running=True, execution_mode="sdk", health_status="healthy"),
    }
    config = VulnClawConfig()
    config.mcp.servers = {
        name: MCPServerConfig(name=name, enabled=name != "disabled",
                              transport=MCPTransportConfig(type="stdio", command="fixture"))
        for name in ["local", "remote", "starting", "placeholder", "stopped", "disabled", "unregistered"]
    }
    runtime = SimpleNamespace(config=config, mcp_started=4,
                              mcp_manager=SimpleNamespace(registry=SimpleNamespace(
                                  get_all_servers=lambda: states)))
    result = _runtime_metadata(runtime)
    assert result["mcp_ready"] == 2
    assert result["mcp_servers"] == [
        {"name": "local", "ready": True},
        {"name": "placeholder", "ready": False},
        {"name": "remote", "ready": True},
        {"name": "starting", "ready": False},
        {"name": "stopped", "ready": False},
        {"name": "unregistered", "ready": False},
    ]
    assert result["skills_loaded"] == 2
    assert result["skills"] == ["custom", "valid"]


async def test_mcp_state_changes_push_runtime_updates_and_unsubscribe_on_close():
    config = VulnClawConfig()
    config.mcp.servers = {
        "custom": MCPServerConfig(name="custom", transport=MCPTransportConfig(type="sse")),
    }
    registry = MCPRegistry()
    registry.register_server("custom")
    runtime = SimpleNamespace(config=config, mcp_manager=SimpleNamespace(registry=registry),
                              stop=AsyncMock())
    events = []
    updated = asyncio.Event()

    def emit(kind, **fields):
        events.append({"type": kind, **fields})
        if kind == "state":
            updated.set()

    application = ApplicationSession(emit, runtime_factory=lambda: runtime)
    try:
        await application.handle("initialize", request_id="init")
        assert events[-1]["state"]["runtime"]["mcp_servers"] == [{"name": "custom", "ready": False}]

        def connect():
            registry.set_server_execution_mode("custom", "sse")
            registry.set_server_running("custom", True)
            registry.set_server_health("custom", "healthy")

        await asyncio.to_thread(connect)

        async def wait_for_ready(ready):
            while not events or events[-1]["type"] != "state" or events[-1]["state"]["runtime"]["mcp_ready"] != ready:
                await updated.wait()
                updated.clear()

        await asyncio.wait_for(wait_for_ready(1), timeout=2)
        assert events[-1]["state"]["runtime"]["mcp_servers"] == [{"name": "custom", "ready": True}]
        registry.set_server_error("custom", "connection lost")
        await asyncio.wait_for(wait_for_ready(0), timeout=2)
        assert events[-1]["state"]["runtime"]["mcp_servers"] == [{"name": "custom", "ready": False}]
    finally:
        await application.close()
    assert registry.on_state_change is None
    count = len(events)
    registry.set_server_health("custom", "healthy")
    await asyncio.sleep(0)
    assert len(events) == count
