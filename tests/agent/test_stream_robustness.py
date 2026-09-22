"""Protocol-aware streaming and execution boundaries for the shared Agent loop."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from vulnclaw.agent.context import ContextManager
from vulnclaw.agent.llm_client import _NullSink, call_llm_auto, call_llm_stream
from vulnclaw.config.schema import VulnClawConfig
from vulnclaw.llm.types import ProtocolError


def output(text="done"):
    return {
        "type": "message",
        "id": "m1",
        "role": "assistant",
        "status": "completed",
        "phase": "final_answer",
        "content": [{"type": "output_text", "text": text}],
    }


def result(items, status="completed"):
    return {
        "id": "r1",
        "status": status,
        "output": items,
        "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
    }


def agent():
    cfg = VulnClawConfig()
    context = ContextManager()
    client = SimpleNamespace(responses=SimpleNamespace(create=MagicMock()))
    obj = SimpleNamespace(
        config=cfg,
        context=context,
        _get_client=lambda: client,
        _build_openai_tools=lambda: [],
        _execute_mcp_tool=AsyncMock(return_value="ok"),
    )
    return obj, client


@pytest.mark.parametrize("arguments", ['{"a":', "[]", "null", ""])
async def test_invalid_arguments_have_results_and_never_execute(arguments):
    obj, client = agent()
    call = {"type": "function_call", "call_id": "c1", "name": "ping", "arguments": arguments}
    client.responses.create.side_effect = [result([call]), result([output()])]
    assert await call_llm_auto(obj, "system", "run") == "done"
    obj._execute_mcp_tool.assert_not_called()
    sent = client.responses.create.call_args.kwargs["input"]
    assert sent[-1]["call_id"] == "c1"
    assert "rejected" in sent[-1]["output"]


async def test_reasoning_and_function_history_survive_two_requests():
    obj, client = agent()
    reasoning = {"type": "reasoning", "id": "rs1", "summary": [], "encrypted_content": "opaque"}
    call = {"type": "function_call", "call_id": "c1", "name": "ping", "arguments": "{}"}
    client.responses.create.side_effect = [result([reasoning, call]), result([output()])]
    assert await call_llm_auto(obj, "system", "run") == "done"
    obj._execute_mcp_tool.assert_awaited_once_with("ping", {})
    sent = client.responses.create.call_args.kwargs["input"]
    assert sent[1] == reasoning
    assert sent[2]["call_id"] == sent[3]["call_id"] == "c1"
    assert obj.context.messages[-1]["phase"] == "final_answer"
    assert sum(m.get("id") == "m1" for m in obj.context.messages) == 1


@pytest.mark.parametrize("terminal", [False, True])
async def test_incomplete_stream_never_executes_tools(terminal):
    obj, client = agent()
    call = {"type": "function_call", "call_id": "c1", "name": "ping", "arguments": "{}"}
    events = [{"type": "response.output_item.done", "output_index": 0, "item": call}]
    if terminal:
        events.append({"type": "response.incomplete", "response": result([call], "incomplete")})
    client.responses.create.return_value = iter(events)
    with pytest.raises(ProtocolError):
        await call_llm_stream(obj, "system")
    obj._execute_mcp_tool.assert_not_called()
    assert obj.context.messages == []
    assert client.responses.create.call_count == 1


async def test_partial_stream_disconnect_does_not_retry_or_commit():
    obj, client = agent()

    def broken():
        yield {"type": "response.output_text.delta", "delta": "partial"}
        raise ConnectionError("disconnected")

    client.responses.create.return_value = broken()
    sink = _NullSink()
    sink.on_content_token = MagicMock()
    with pytest.raises(ConnectionError):
        await call_llm_stream(obj, "system", sink)
    sink.on_content_token.assert_called_once_with("partial")
    assert client.responses.create.call_count == 1
    assert obj.context.messages == []


async def test_blocking_provider_handshake_and_iteration_yield_event_loop():
    for delay_create in (True, False):
        obj, client = agent()

        def delayed():
            if not delay_create:
                time.sleep(0.15)
            yield {"type": "response.completed", "response": result([output()])}

        def create(**kwargs):
            if delay_create:
                time.sleep(0.15)
            return delayed()

        client.responses.create.side_effect = create
        task = asyncio.create_task(call_llm_stream(obj, "system"))
        await asyncio.sleep(0.02)
        assert not task.done()
        assert await task == "done"


async def test_cancellation_closes_stream_and_does_not_commit():
    obj, client = agent()

    class Stream:
        closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise asyncio.CancelledError()

        def close(self):
            self.closed = True

    stream = Stream()
    client.responses.create.return_value = stream
    with pytest.raises(asyncio.CancelledError):
        await call_llm_stream(obj, "system")
    assert stream.closed
    assert obj.context.messages == []


async def test_stream_handshake_retry_then_single_tool_execution(monkeypatch):
    obj, client = agent()
    call = {"type": "function_call", "call_id": "c1", "name": "ping", "arguments": "{}"}
    client.responses.create.side_effect = [
        ConnectionError("connect failed"),
        iter([{"type": "response.completed", "response": result([call])}]),
        iter([{"type": "response.completed", "response": result([output()])}]),
    ]
    monkeypatch.setattr("vulnclaw.agent.llm_client.asyncio.sleep", AsyncMock())
    assert "done" in await call_llm_stream(obj, "system")
    obj._execute_mcp_tool.assert_awaited_once()
    assert client.responses.create.call_count == 3


async def test_dedup_and_batch_limit_still_return_every_call_id():
    obj, client = agent()
    calls = [
        {"type": "function_call", "call_id": f"c{i}", "name": "ping", "arguments": '{"n":%d}' % i}
        for i in range(12)
    ]
    calls.append({**calls[0], "call_id": "duplicate"})
    client.responses.create.side_effect = [result(calls), result([output()])]
    assert await call_llm_auto(obj, "system", "run") == "done"
    assert obj._execute_mcp_tool.await_count == 10
    outputs = [
        item
        for item in client.responses.create.call_args.kwargs["input"]
        if item.get("type") == "function_call_output"
    ]
    assert {item["call_id"] for item in outputs} == {call["call_id"] for call in calls}
    assert outputs[-1]["output"] == outputs[0]["output"]


async def test_cancellation_during_handshake_closes_late_stream():
    import threading

    obj, client = agent()
    started, release, closed = threading.Event(), threading.Event(), threading.Event()

    class Stream:
        def close(self):
            closed.set()

    def opening(**kwargs):
        started.set()
        release.wait(2)
        return Stream()

    client.responses.create.side_effect = opening
    task = asyncio.create_task(call_llm_stream(obj, "system"))
    assert await asyncio.to_thread(started.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    assert await asyncio.to_thread(closed.wait, 2)
    assert obj.context.messages == []


def test_explicit_null_subagent_budget_does_not_restore_server_limit():
    from vulnclaw.agent.llm_client import build_llm_request
    from vulnclaw.agent.subagent.models import SubagentContext

    obj, _ = agent()
    obj._subagent_ctx = SubagentContext(depth=1)
    request = build_llm_request(obj, [{"role": "user", "content": "hi"}], max_tokens=None)
    assert "max_output_tokens" not in request
    assert obj._subagent_ctx.next_model_token_reservation >= 4096


@pytest.mark.parametrize("kind", ["shell_call", "local_shell_call"])
async def test_native_shell_reuses_executor_and_preserves_result(kind):
    from vulnclaw.agent.tool_result_overrides import ToolOutput

    obj, client = agent()
    native = {"stdout": "hello", "stderr": "warning", "outcome": {"type": "exit", "exit_code": 7}}
    obj._execute_mcp_tool.return_value = ToolOutput("display", native)
    action = {"commands": ["echo hello"]} if kind == "shell_call" else {"command": ["echo", "hello"]}
    call = {"type": kind, "call_id": "s1", "action": action}
    client.responses.create.side_effect = [result([call]), result([output()])]
    assert await call_llm_auto(obj, "system", "run") == "done"
    obj._execute_mcp_tool.assert_awaited_once_with("shell_command", {
        "command": "echo hello", "workdir": None, "timeout_ms": None, "max_output_chars": None,
    })
    sent = client.responses.create.call_args.kwargs["input"][-1]
    assert sent["type"] == kind + "_output"
    assert sent["output"] == ([native] if kind == "shell_call" else "display")


@pytest.mark.parametrize("approved", [True, False])
async def test_hosted_mcp_approval_is_recorded_before_followup(monkeypatch, approved):
    obj, client = agent()
    authorize = AsyncMock(return_value=SimpleNamespace(approved=approved))
    monkeypatch.setattr("vulnclaw.agent.exec_gate.get_execution_gate", lambda _: SimpleNamespace(authorize=authorize))
    request = {"type": "mcp_approval_request", "id": "approval_1", "name": "lookup", "server_label": "remote", "arguments": "{}"}
    client.responses.create.side_effect = [result([request]), result([output()])]
    assert await call_llm_auto(obj, "system", "run") == "done"
    authorize.assert_awaited_once()
    obj._execute_mcp_tool.assert_not_called()
    assert client.responses.create.call_args.kwargs["input"][-1] == {
        "type": "mcp_approval_response", "approval_request_id": "approval_1", "approve": approved,
    }


@pytest.mark.parametrize("custom", [True, False])
@pytest.mark.parametrize("approved", [True, False])
async def test_native_patch_uses_gate_and_returns_typed_result(monkeypatch, tmp_path, custom, approved):
    obj, client = agent()
    monkeypatch.chdir(tmp_path)
    authorize = AsyncMock(return_value=SimpleNamespace(approved=approved, refusal_text=lambda _: "denied"))
    monkeypatch.setattr("vulnclaw.agent.exec_gate.get_execution_gate", lambda _: SimpleNamespace(authorize=authorize))
    call = ({"type": "custom_tool_call", "name": "apply_patch", "call_id": "p1",
             "input": "*** Begin Patch\n*** Add File: file.txt\n+hello\n*** End Patch"}
            if custom else {"type": "apply_patch_call", "call_id": "p1",
                            "operation": {"type": "create_file", "path": "file.txt", "diff": "+hello"}})
    client.responses.create.side_effect = [result([call]), result([output()])]
    assert await call_llm_auto(obj, "system", "run") == "done"
    assert (tmp_path / "file.txt").exists() is approved
    authorize.assert_awaited_once()
    obj._execute_mcp_tool.assert_not_called()
    sent = client.responses.create.call_args.kwargs["input"][-1]
    assert sent["type"] == ("custom_tool_call_output" if custom else "apply_patch_call_output")
    if not custom:
        assert sent["status"] == ("completed" if approved else "failed")


@pytest.mark.parametrize("path", ["../outside", "link/file.txt"])
async def test_native_patch_rejects_escape_without_requesting_approval(monkeypatch, tmp_path, path):
    obj, client = agent()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "link").symlink_to(tmp_path.parent, target_is_directory=True)
    gate = MagicMock()
    monkeypatch.setattr("vulnclaw.agent.exec_gate.get_execution_gate", gate)
    call = {"type": "apply_patch_call", "call_id": "p1", "operation": {
        "type": "create_file", "path": path, "diff": "+hello",
    }}
    client.responses.create.side_effect = [result([call]), result([output()])]
    await call_llm_auto(obj, "system", "run")
    gate.assert_not_called()
    sent = client.responses.create.call_args.kwargs["input"][-1]
    assert sent["status"] == "failed"
    assert "inside the working directory" in sent["output"]


async def test_native_patch_rechecks_file_after_approval(monkeypatch, tmp_path):
    obj, client = agent()
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "file.txt"
    path.write_text("before\n")
    async def authorize(*args, **kwargs):
        path.write_text("changed by user\n")
        return SimpleNamespace(approved=True)
    monkeypatch.setattr("vulnclaw.agent.exec_gate.get_execution_gate", lambda _: SimpleNamespace(authorize=authorize))
    call = {"type": "apply_patch_call", "call_id": "p1", "operation": {
        "type": "update_file", "path": "file.txt", "diff": "@@\n-before\n+after",
    }}
    client.responses.create.side_effect = [result([call]), result([output()])]
    await call_llm_auto(obj, "system", "run")
    assert path.read_text() == "changed by user\n"
    assert client.responses.create.call_args.kwargs["input"][-1]["status"] == "failed"


async def test_native_patch_cancellation_does_not_apply_or_retry(monkeypatch, tmp_path):
    obj, client = agent()
    monkeypatch.chdir(tmp_path)
    authorize = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr("vulnclaw.agent.exec_gate.get_execution_gate", lambda _: SimpleNamespace(authorize=authorize))
    call = {"type": "apply_patch_call", "call_id": "p1", "operation": {
        "type": "create_file", "path": "file.txt", "diff": "+hello",
    }}
    client.responses.create.return_value = result([call])
    with pytest.raises(asyncio.CancelledError):
        await call_llm_auto(obj, "system", "run")
    assert not (tmp_path / "file.txt").exists()
    assert client.responses.create.call_count == 1
    assert obj.context.messages[-1]["status"] == "failed"


async def test_native_hosted_shell_is_not_executed_locally():
    obj, client = agent()
    call = {"type": "shell_call", "call_id": "s1", "environment": {"type": "container_reference", "container_id": "c1"},
            "action": {"commands": ["echo hello"]}}
    client.responses.create.return_value = result([call, output()])
    assert await call_llm_auto(obj, "system", "run") == "done"
    obj._execute_mcp_tool.assert_not_called()


async def test_patch_write_failure_rolls_back_prior_files(monkeypatch, tmp_path):
    from pathlib import Path

    obj, client = agent()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("before\n")
    original_write = Path.write_bytes
    def write(path, data):
        if path.name == "b.txt":
            raise OSError("disk error")
        return original_write(path, data)
    monkeypatch.setattr(Path, "write_bytes", write)
    gate = SimpleNamespace(authorize=AsyncMock(return_value=SimpleNamespace(approved=True)))
    monkeypatch.setattr("vulnclaw.agent.exec_gate.get_execution_gate", lambda _: gate)
    call = {"type": "custom_tool_call", "name": "apply_patch", "call_id": "p1", "input":
            "*** Begin Patch\n*** Update File: a.txt\n@@\n-before\n+after\n*** Add File: b.txt\n+new\n*** End Patch"}
    client.responses.create.side_effect = [result([call]), result([output()])]
    await call_llm_auto(obj, "system", "run")
    assert (tmp_path / "a.txt").read_text() == "before\n"
    assert not (tmp_path / "b.txt").exists()
    assert "disk error" in client.responses.create.call_args.kwargs["input"][-1]["output"]


async def test_native_patch_is_unavailable_to_subagents(monkeypatch, tmp_path):
    from vulnclaw.agent.native_tools import execute_native_tool
    from vulnclaw.llm.adapters.responses import parse

    obj, _ = agent()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("vulnclaw.agent.builtin_tools.is_subagent", lambda _: True)
    gate = MagicMock()
    monkeypatch.setattr("vulnclaw.agent.exec_gate.get_execution_gate", gate)
    call = parse(result([{"type": "apply_patch_call", "call_id": "p1", "operation": {
        "type": "create_file", "path": "file.txt", "diff": "+hello",
    }}])).tool_calls[0]
    value = await execute_native_tool(obj, call)
    assert "not available to subagents" in value
    assert not (tmp_path / "file.txt").exists()
    gate.assert_not_called()


async def test_native_shell_duplicates_execute_once_but_return_both_call_ids():
    from vulnclaw.agent.tool_result_overrides import ToolOutput

    obj, client = agent()
    native = {"stdout": "ok", "stderr": "", "outcome": {"type": "exit", "exit_code": 0}}
    obj._execute_mcp_tool.return_value = ToolOutput("ok", native)
    calls = [{"type": "shell_call", "id": f"item_{i}", "call_id": f"s{i}",
              "action": {"commands": ["echo hello"]}} for i in range(2)]
    client.responses.create.side_effect = [result(calls), result([output()])]
    await call_llm_auto(obj, "system", "run")
    obj._execute_mcp_tool.assert_awaited_once()
    sent = client.responses.create.call_args.kwargs["input"][-2:]
    assert [item["call_id"] for item in sent] == ["s0", "s1"]
    assert all(item["output"] == [native] for item in sent)


async def test_client_tool_search_returns_available_tools_and_namespace_dispatch():
    obj, client = agent()
    obj._build_openai_tools = lambda: [{"type": "function", "function": {
        "name": "ping", "description": "Ping a host", "parameters": {"type": "object", "properties": {}},
    }}]
    search = {"type": "tool_search_call", "execution": "client", "call_id": "search1", "arguments": {"goal": "ping"}}
    call = {"type": "function_call", "call_id": "f1", "namespace": "network", "name": "ping", "arguments": "{}"}
    client.responses.create.side_effect = [result([search]), result([call]), result([output()])]
    assert await call_llm_auto(obj, "system", "run") == "done"
    sent = client.responses.create.call_args_list[1].kwargs["input"]
    found = next(item for item in sent if item.get("type") == "tool_search_output")
    assert [tool["name"] for tool in found["tools"]] == ["ping"]
    obj._execute_mcp_tool.assert_awaited_once_with("network.ping", {})
