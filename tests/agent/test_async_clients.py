"""Client replacement and ownership follow the asynchronous lifetime."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from vulnclaw.agent.core import AgentCore
from vulnclaw.agent.subagent.integration import _close_child_resources
from vulnclaw.application import ApplicationRuntime
from vulnclaw.config.schema import VulnClawConfig


@pytest.mark.parametrize("operation", ["config", "rotate"])
async def test_replacement_waits_for_old_client_close(operation):
    old_config = VulnClawConfig()
    new_config = old_config.model_copy(deep=True)
    closing, release = asyncio.Event(), asyncio.Event()

    async def close():
        closing.set()
        await release.wait()

    agent = AgentCore.__new__(AgentCore)
    agent.config = old_config
    agent._client = SimpleNamespace(close=close)
    agent._key_pool, agent._key_index = ["fixture-1", "fixture-2"], 0
    pending = asyncio.create_task(
        agent.apply_config(new_config) if operation == "config" else agent.rotate_api_key()
    )
    await closing.wait()
    assert not pending.done()
    assert agent.config is old_config
    assert agent._key_index == 0
    release.set()
    await pending
    assert agent._client is None
    if operation == "config":
        assert agent.config is new_config
    else:
        assert agent._key_index == 1


@pytest.mark.parametrize("failure", [False, True])
async def test_runtime_closes_client_and_mcp_once_even_on_close_failure(failure):
    close = AsyncMock(side_effect=OSError("fixture close failure") if failure else None)
    manager = SimpleNamespace(astop_all=AsyncMock())
    runtime = ApplicationRuntime(VulnClawConfig(), manager, SimpleNamespace(aclose=close))
    if failure:
        with pytest.raises(OSError):
            await runtime.stop()
    else:
        await runtime.stop()
    await runtime.stop()
    close.assert_awaited_once()
    manager.astop_all.assert_awaited_once()


async def test_child_cleanup_only_closes_owned_async_client():
    parent_client = SimpleNamespace(close=AsyncMock())
    child_client = SimpleNamespace(close=AsyncMock())
    parent = SimpleNamespace(_client=parent_client)
    child = SimpleNamespace(_client=child_client)
    assert await _close_child_resources(parent, child) == ""
    child_client.close.assert_awaited_once()
    parent_client.close.assert_not_awaited()
    assert await _close_child_resources(parent, SimpleNamespace(_client=parent_client)) == ""
    parent_client.close.assert_not_awaited()
