"""Backend readiness and tool calls share cancellable MCP connection owners."""

import asyncio
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from vulnclaw.application import ApplicationRuntime, ApplicationSession
from vulnclaw.config.schema import MCPServerConfig, MCPTransportConfig, VulnClawConfig
from vulnclaw.mcp.lifecycle import MCPLifecycleManager


def manager_for(transport):
    config = VulnClawConfig()
    config.mcp.servers["fixture"] = MCPServerConfig(
        name="fixture", transport=MCPTransportConfig(
            type=transport, command=sys.executable, url="http://fixture.invalid/mcp",
        ),
    )
    return MCPLifecycleManager(config)


def fake_transport(monkeypatch, transport, phase="initialize", failure=False, tool_name="echo"):
    import vulnclaw.mcp.lifecycle as lifecycle

    entered, release = asyncio.Event(), asyncio.Event()
    events = []

    @asynccontextmanager
    async def connection(*_args, **_kwargs):
        owner = asyncio.current_task()
        events.append("transport opened")
        try:
            yield (None, None, None) if transport == "streamable-http" else (None, None)
        finally:
            assert asyncio.current_task() is owner
            events.append("transport closed")

    class Session:
        def __init__(self, *_args):
            self.owner = None

        async def __aenter__(self):
            self.owner = asyncio.current_task()
            return self

        async def __aexit__(self, *_args):
            assert asyncio.current_task() is self.owner
            events.append("session closed")

        async def initialize(self):
            events.append("initialize")
            if phase == "initialize":
                entered.set()
                await release.wait()
            if failure:
                raise RuntimeError("fixture unavailable")

        async def list_tools(self):
            if phase == "list_tools":
                entered.set()
                await release.wait()
            return SimpleNamespace(tools=[SimpleNamespace(name=tool_name, description="", inputSchema={})])

        async def call_tool(self, name, arguments):
            assert name == tool_name
            if phase == "warmup":
                entered.set()
                await release.wait()
            events.append("call")
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="connected")])

    api = {"stdio": "stdio_client", "sse": "sse_client", "streamable-http": "streamablehttp_client"}[transport]
    monkeypatch.setattr(lifecycle, api, connection)
    monkeypatch.setattr(lifecycle, "ClientSession", Session)
    return entered, release, events


@pytest.mark.parametrize("command,args", [
    ("npx", ["-y", "fixture"]), ("pnpx", ["fixture"]),
    ("bunx", ["fixture"]), ("npm", ["exec", "fixture"]),
    ("yarn", ["dlx", "fixture"]),
])
async def test_package_commands_connect_and_discover_without_input(command, args, monkeypatch):
    manager = manager_for("stdio")
    manager.config.mcp.servers["fixture"].transport.command = command
    manager.config.mcp.servers["fixture"].transport.args = args
    entered, release, events = fake_transport(monkeypatch, "stdio")
    manager.start_enabled_servers(background=True)
    try:
        await asyncio.wait_for(entered.wait(), 1)
        release.set()
        await asyncio.wait_for(manager._startup_ready["fixture"].wait(), 1)
        assert "echo" in manager.list_available_tools()
        assert events.count("initialize") == 1
        assert "call" not in events
    finally:
        await manager.astop_all()


async def test_devtools_is_not_ready_until_browser_warmup_finishes(monkeypatch):
    manager = manager_for("stdio")
    config = manager.config.mcp.servers.pop("fixture")
    config.transport.command = "npx"
    manager.config.mcp.servers["chrome-devtools"] = config
    entered, release, events = fake_transport(monkeypatch, "stdio", "warmup", tool_name="list_pages")
    manager.start_enabled_servers(background=True)
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert manager.registry.get_all_servers()["chrome-devtools"].health_status == "starting"
        assert not manager._startup_ready["chrome-devtools"].is_set()
        release.set()
        await asyncio.wait_for(manager._startup_ready["chrome-devtools"].wait(), 1)
        assert manager.registry.get_all_servers()["chrome-devtools"].health_status == "healthy"
        assert events.count("call") == 1
    finally:
        await manager.astop_all()


@pytest.mark.parametrize("transport", ["stdio", "sse", "streamable-http"])
async def test_discovery_failure_never_reports_ready(transport, monkeypatch):
    import vulnclaw.mcp.lifecycle as lifecycle

    manager = manager_for(transport)
    fake_transport(monkeypatch, transport, phase="none")

    async def unavailable(_session):
        raise RuntimeError("tool discovery unavailable")

    monkeypatch.setattr(lifecycle.ClientSession, "list_tools", unavailable)
    manager.start_enabled_servers(background=True)
    try:
        await asyncio.wait_for(manager.wait_for_startup(), 1)
        state = manager.registry.get_all_servers()["fixture"]
        assert state.health_status == "degraded"
        assert state.error == "tool discovery unavailable"
        assert manager._mcp_clients == {}
    finally:
        await manager.astop_all()


async def test_devtools_browser_error_is_reported_before_any_input(monkeypatch):
    import vulnclaw.mcp.lifecycle as lifecycle

    manager = manager_for("stdio")
    manager.config.mcp.servers["chrome-devtools"] = manager.config.mcp.servers.pop("fixture")
    fake_transport(monkeypatch, "stdio", phase="none", tool_name="list_pages")

    async def unavailable(_session, _tool, arguments):
        assert arguments == {}
        return SimpleNamespace(isError=True, content=[SimpleNamespace(text="browser unavailable")])

    monkeypatch.setattr(lifecycle.ClientSession, "call_tool", unavailable)
    manager.start_enabled_servers(background=True)
    try:
        await asyncio.wait_for(manager.wait_for_startup(), 1)
        state = manager.registry.get_all_servers()["chrome-devtools"]
        assert state.health_status == "degraded"
        assert state.error == "browser unavailable"
    finally:
        await manager.astop_all()


@pytest.mark.parametrize("transport", ["stdio", "sse", "streamable-http"])
async def test_ready_precedes_connection_and_calls_reuse_the_same_session(transport, monkeypatch):
    manager = manager_for(transport)
    entered, release, events = fake_transport(monkeypatch, transport)
    emitted = []

    def runtime():
        started = manager.start_enabled_servers(background=True)
        # A known tool can be called while discovery is still in progress.
        manager.registry.register_tool("fixture", {"name": "echo"})
        return ApplicationRuntime(manager.config, manager, None, started)

    session = ApplicationSession(lambda kind, **fields: emitted.append((kind, fields)), runtime_factory=runtime)
    try:
        await session.handle("initialize", request_id="init")
        assert emitted[0][0] == "ready"
        assert emitted[0][1]["runtime"]["mcp_ready"] == 0
        await asyncio.wait_for(entered.wait(), 1)
        call = asyncio.create_task(manager.call_tool("echo", {}))
        await asyncio.sleep(0)
        assert not call.done()
        release.set()
        result = await asyncio.wait_for(call, 1)
        assert result["ok"] is True
        assert events.count("initialize") == 1
        assert events.count("call") == 1
        await asyncio.sleep(0)
        assert any(kind == "state" and fields["state"]["runtime"]["mcp_ready"] == 1 for kind, fields in emitted)
    finally:
        await session.close()
    assert events[-2:] == ["session closed", "transport closed"]
    assert manager._startup_tasks == {}
    assert manager._mcp_clients == {}


@pytest.mark.parametrize("transport", ["stdio", "sse", "streamable-http"])
@pytest.mark.parametrize("phase", ["initialize", "list_tools"])
async def test_shutdown_cancels_connection_and_discovery_in_the_owner_task(transport, phase, monkeypatch):
    manager = manager_for(transport)
    entered, _release, events = fake_transport(monkeypatch, transport, phase)
    manager.start_enabled_servers(background=True)
    await asyncio.wait_for(entered.wait(), 1)
    await asyncio.wait_for(manager.astop_all(), 1)
    assert events[-2:] == ["session closed", "transport closed"]
    assert manager._startup_tasks == {}
    assert manager._mcp_clients == {}


async def test_failed_connection_degrades_once_and_keeps_backend_usable(monkeypatch):
    manager = manager_for("stdio")
    entered, release, events = fake_transport(monkeypatch, "stdio", failure=True)
    manager.start_enabled_servers(background=True)
    manager.registry.register_tool("fixture", {"name": "echo"})
    await asyncio.wait_for(entered.wait(), 1)
    release.set()
    await asyncio.wait_for(manager._startup_ready["fixture"].wait(), 1)
    assert manager.registry.get_all_servers()["fixture"].health_status == "degraded"
    assert (await manager.call_tool("echo", {}))["ok"] is False
    assert events.count("initialize") == 1
    await manager.astop_all()


async def test_connections_obey_existing_concurrency_limit_and_config_edits_replace_owners(monkeypatch):
    manager = manager_for("stdio")
    manager.config.safety.tool_max_concurrent = 2
    template = manager.config.mcp.servers["fixture"]
    for name in ["second", "third"]:
        manager.config.mcp.servers[name] = template.model_copy(deep=True, update={"name": name})
    _entered, release, events = fake_transport(monkeypatch, "stdio")
    manager.start_enabled_servers(background=True)

    async def two_connecting():
        while events.count("initialize") < 2:
            await asyncio.sleep(0)

    try:
        await asyncio.wait_for(two_connecting(), 1)
        await asyncio.sleep(0)
        assert events.count("initialize") == 2
        release.set()
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in manager._startup_ready.values())), 1)
        assert events.count("initialize") == 3
        old_owner = manager._startup_tasks["fixture"]
        updated = manager.config.model_copy(deep=True)
        updated.mcp.servers["fixture"].transport.args = ["changed"]
        await manager.apply_config(updated)
        await asyncio.wait_for(manager._startup_ready["fixture"].wait(), 1)
        assert old_owner.done()
        assert manager._startup_tasks["fixture"] is not old_owner
        assert events.count("initialize") == 4
    finally:
        await manager.astop_all()
    assert events.count("session closed") == 4
    assert events.count("transport closed") == 4


async def test_real_stdio_peer_is_discovered_and_reused_until_shutdown(tmp_path):
    peer = tmp_path / "peer.py"
    peer.write_text(
        "import os\nfrom mcp.server.fastmcp import FastMCP\n"
        "server = FastMCP('startup-test')\n"
        "@server.tool()\ndef identity() -> str:\n    return str(os.getpid())\n"
        "server.run(transport='stdio')\n"
    )
    manager = manager_for("stdio")
    manager.config.mcp.servers["fixture"].transport.args = [str(peer)]
    manager.start_enabled_servers(background=True)
    owner = manager._startup_tasks["fixture"]
    try:
        await asyncio.wait_for(manager._startup_ready["fixture"].wait(), 10)
        assert "identity" in manager.list_available_tools()
        first = await manager.call_tool("identity", {})
        second = await manager.call_tool("identity", {})
        assert first["ok"] and second["ok"]
        assert int(first["content"]) == int(second["content"])
        assert not owner.done()
    finally:
        await asyncio.wait_for(manager.astop_all(), 5)
    assert owner.done()
    assert manager._mcp_clients == {}
