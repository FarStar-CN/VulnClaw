from __future__ import annotations

import io
import json

import pytest

from tests.cli.test_session_backend import FakeRuntime, request
from vulnclaw.agent.streaming import TranscriptStreamSink
from vulnclaw.session_backend import BackendSession
from vulnclaw.session_protocol import JsonlWriter


@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
def test_segments_end_before_tools_and_keep_partial_source(terminal):
    events = []
    sink = TranscriptStreamSink(lambda kind, **fields: events.append({"type": kind, **fields}))
    sink.on_thinking_token("inspect")
    sink.on_content_token("```py\n")
    sink.on_content_token("  print('中文')")
    sink.on_tool_call("probe", "{\"raw\": true}")
    sink.on_tool_result("HTTP/1.1 200\n\nbody")
    sink.on_content_token("[ref][late]")
    sink.on_stream_end(terminal)
    sink.on_stream_end(terminal)
    messages = [event for event in events if event["type"] == "message"]
    identifiers = {event["message_id"] for event in messages}
    assert len(identifiers) == 3
    assert sum(event["operation"] == "end" for event in messages) == 3
    assert messages[-1]["status"] == terminal
    assert "text" not in messages[-1]
    assert events[4]["operation"] == "end"
    assert events[5]["type"] == "tool_call"
    assert messages[2]["text"] + messages[3]["text"] == "```py\n  print('中文')"


async def test_snapshot_has_cumulative_text_and_later_delta_keeps_identity():
    stream = io.StringIO()
    backend = BackendSession(JsonlWriter(stream), runtime_factory=FakeRuntime)
    await backend.handle(request("initialize", "init"))
    from vulnclaw.application import ApplicationStreamSink

    sink = ApplicationStreamSink(backend.application.emit, "t1", show_thinking=True)
    sink.on_content_token("[text][ref]")
    await backend.handle(request("get_state", "snapshot"))
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    snapshot = events[-1]["state"]["messages"][0]
    assert snapshot["text"] == "[text][ref]"
    assert snapshot["status"] == "streaming"
    sink.on_content_token("\n\n[ref]: https://example.test")
    sink.on_stream_end("cancelled")
    current = backend.application.state_snapshot()["messages"][0]
    assert current["message_id"] == snapshot["message_id"]
    assert current["text"] == "[text][ref]\n\n[ref]: https://example.test"
    assert current["status"] == "cancelled"
    await backend.close()
