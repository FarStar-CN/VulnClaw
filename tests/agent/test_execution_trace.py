"""Execution display data comes from actual processes and HTTPX requests."""

import asyncio
import io
import json
import sys
from contextvars import copy_context

import httpx
import pytest

from vulnclaw.agent.builtin_tools import _spawn_captured
from vulnclaw.agent.execution_trace import (
    Operation,
    capture_process,
    observe_http_client,
    observe_tools,
    trace_call,
)
from vulnclaw.agent.streaming import TranscriptStreamSink
from vulnclaw.session_protocol import JsonlWriter, make_event


def sink_events(source="main"):
    events = []
    writer = JsonlWriter(io.StringIO())

    def emit(event_type, **fields):
        event = make_event(event_type, task_id="t", **fields)
        writer.write(event)
        events.append(event)

    return TranscriptStreamSink(emit, source=source), events


async def test_process_observes_exact_argv_exit_and_separate_streams(tmp_path):
    sink, events = sink_events()
    argv = [sys.executable, "-c", "import sys; print('中文 😀'); print('err', file=sys.stderr); sys.exit(2)"]
    with observe_tools(sink), trace_call("process"):
        result = await capture_process(_spawn_captured, argv, cwd=str(tmp_path), env={}, timeout_s=5)
    assert result[0] == 2
    operation = [event for event in events if event["kind"] == "command"][-1]
    assert operation["argv"] == argv
    assert operation["cwd"] == str(tmp_path)
    assert operation["stdout"].strip() == "中文 😀"
    assert operation["stderr"].strip() == "err"
    assert operation["status"] == "failed"
    assert events[-1]["status"] == "failed"


async def test_http_records_actual_encoding_serialized_body_and_masks_headers():
    sink, events = sink_events()
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(404, json={"missing": True}, headers={"Set-Cookie": "session=secret"})

    with observe_tools(sink), trace_call("http"):
        with observe_http_client(httpx.Client(transport=httpx.MockTransport(respond))) as client:
            response = client.post("https://example.test/a?q=%27", json={"中文": "😀"},
                                   headers={"Authorization": "Bearer secret", "Cookie": "id=secret"})
    assert response.status_code == 404
    assert len(requests) == 1
    operation = [event for event in events if event["kind"] == "http"][-1]
    assert operation["url"] == str(requests[0].url)
    assert operation["body"] == requests[0].content.decode()
    assert operation["headers"]["authorization"] == "[masked]"
    assert operation["headers"]["cookie"] == "[masked]"
    assert operation["response_headers"]["set-cookie"] == "[masked]"
    assert json.loads(operation["response_body"]) == {"missing": True}
    assert operation["status"] == "completed"
    assert operation["status_code"] == 404


async def test_async_parallel_calls_keep_source_and_call_identity():
    async def run(call_id, source, delay):
        sink, events = sink_events(source)

        async def respond(request):
            await asyncio.sleep(delay)
            return httpx.Response(200, text=call_id)

        with observe_tools(sink), trace_call(call_id):
            async with observe_http_client(httpx.AsyncClient(transport=httpx.MockTransport(respond))) as client:
                await client.get(f"https://example.test/{call_id}")
        return events

    a, b = await asyncio.gather(run("a", "child-a", .01), run("b", "child-b", 0))
    for call_id, source, events in [("a", "child-a", a), ("b", "child-b", b)]:
        assert {event["call_id"] for event in events} == {call_id}
        assert {event["agent_id"] for event in events} == {source}
        assert [event for event in events if "response_body" in event][-1]["response_body"] == call_id


def test_redirects_and_timeout_are_individual_operations_without_resending():
    sink, events = sink_events()
    seen = []

    def respond(request):
        seen.append(request.url.path)
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "/final"})
        if request.url.path == "/slow":
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, text="done")

    with observe_tools(sink), trace_call("batch"):
        with observe_http_client(httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=True)) as client:
            client.get("https://example.test/start")
            with pytest.raises(httpx.ReadTimeout):
                client.get("https://example.test/slow")
    assert seen == ["/start", "/final", "/slow"]
    final = {event["operation_id"]: event for event in events if event["kind"] == "http"}
    assert len(final) == 3
    assert sorted(op["status"] for op in final.values()) == ["completed", "completed", "timed_out"]
    assert events[-1]["status"] == "partial"


async def test_cancellation_finishes_running_operation():
    sink, events = sink_events()
    started = asyncio.Event()

    async def respond(request):
        started.set()
        await asyncio.Event().wait()

    async def run():
        with observe_tools(sink), trace_call("cancel"):
            async with observe_http_client(httpx.AsyncClient(transport=httpx.MockTransport(respond))) as client:
                await client.get("https://example.test/slow")

    task = asyncio.create_task(run())
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events[-1]["status"] == "cancelled"
    assert [event for event in events if event["kind"] == "http"][-1]["status"] == "cancelled"


def test_late_thread_observations_do_not_reopen_a_finished_call():
    sink, events = sink_events()
    with observe_tools(sink), trace_call("finished"):
        context = copy_context()
    count = len(events)
    context.run(lambda: Operation("http", method="GET", url="https://example.test/").finish("completed"))
    assert len(events) == count


@pytest.mark.parametrize("outcome,status", [("reference body", "completed"), (None, "failed"), (RuntimeError("loader unavailable"), "failed")])
async def test_skill_loading_emits_explicit_status_without_changing_model_content(monkeypatch, outcome, status):
    from tests.agent.test_builtin_tools import DummyAgent
    from vulnclaw.agent.builtin_tools import execute_mcp_tool
    from vulnclaw.skills import loader

    calls = []

    def load(skill, reference):
        calls.append((skill, reference))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(loader, "load_skill_reference", load)
    sink, events = sink_events()
    with observe_tools(sink), trace_call("skill"):
        result = await execute_mcp_tool(DummyAgent(), "load_skill_reference",
                                        {"skill_name": "web", "reference_name": "test.md"})
    assert calls == [("web", "test.md")]
    assert events[-1]["status"] == status
    if status == "completed":
        assert result == outcome
    else:
        assert result.startswith("[!]")
