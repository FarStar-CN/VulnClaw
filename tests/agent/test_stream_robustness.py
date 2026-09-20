"""Protocol-aware streaming and execution boundaries for the shared Agent loop."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from vulnclaw.agent.context import ContextManager
from vulnclaw.agent.llm_client import _NullSink, call_llm_auto, call_llm_stream
from vulnclaw.config.schema import VulnClawConfig
from vulnclaw.llm.chat import Collector as ChatCollector
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


@pytest.mark.parametrize("first_fn", [None, {}])
def test_chat_id_only_fragment_and_parallel_arguments(first_fn):
    collector = ChatCollector()
    collector.feed(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "id": "c0", "function": first_fn},
                            {
                                "index": 1,
                                "id": "c1",
                                "function": {"name": "second", "arguments": "{}"},
                            },
                        ]
                    }
                }
            ]
        }
    )
    collector.feed(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"name": "first", "arguments": '{"x":'}},
                        ]
                    }
                }
            ]
        }
    )
    collector.feed(
        {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": "1}"}},
                        ]
                    },
                }
            ]
        }
    )
    calls = collector.finish().tool_calls
    assert calls[0].id == "c0" and calls[0].function.arguments == '{"x":1}'
    assert calls[1].id == "c1"


def test_chat_reasoning_not_mixed_with_history_and_no_finish_is_error():
    collector = ChatCollector()
    collector.feed({"choices": [{"delta": {"reasoning_content": "think"}}]})
    collector.feed({"choices": [{"delta": {"content": "answer"}}]})
    with pytest.raises(ProtocolError):
        collector.finish()
    collector.feed({"choices": [{"finish_reason": "stop", "delta": {}}]})
    response = collector.finish()
    assert response.content == "answer"
    assert response.output_items[0]["content"] == "answer"
    assert response.output_items[0]["_chat_message"]["reasoning_content"] == "think"


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
