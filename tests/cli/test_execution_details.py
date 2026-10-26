"""Large display data stays complete without being sent with collapsed cards."""

import asyncio
import io
import json

import pytest

from vulnclaw.execution_details import INLINE_BYTES, PAGE_BYTES, ExecutionDetails
from vulnclaw.session_backend import BackendSession
from vulnclaw.session_protocol import JsonlWriter, decode_client_message


@pytest.mark.parametrize("field", ["code", "body", "response_body", "stdout", "stderr"])
def test_large_operation_details_round_trip_as_utf8_pages(field):
    details = ExecutionDetails()
    original = "中文 😀\r\n\x00" * 10_000
    fields = {field: original, "operation_id": "operation"}
    try:
        compact = details.compact("tool_execution", fields)
        assert fields[field] == original
        assert field not in compact
        ref = compact["detail_refs"][field]
        assert ref["bytes"] == len(original.encode("utf-8"))
        assert ref["preview"] == original[:256]
        # Identical observations use one stored value, including tool results.
        assert details.compact("tool_result", {"result": original})["result_ref"] == ref
        offset, pages = 0, []
        while offset < ref["bytes"]:
            page = details.read({"id": ref["id"], "offset": offset})
            assert 0 < page["next_offset"] - offset <= PAGE_BYTES
            assert len(page["text"].encode("utf-8")) == page["next_offset"] - offset
            pages.append(page["text"])
            offset = page["next_offset"]
        assert "".join(pages) == original
        assert details.read({"id": ref["id"], "offset": offset})["text"] == ""
    finally:
        details.clear()
    with pytest.raises(ValueError, match="unavailable"):
        details.read({"id": ref["id"]})


def test_inline_threshold_counts_bytes_and_small_updates_stay_inline():
    details = ExecutionDetails()
    try:
        for value in ("", "x" * INLINE_BYTES, "中" * (INLINE_BYTES // 3)):
            fields = {"stdout": value}
            assert details.compact("tool_execution", fields) == fields
        assert "detail_refs" in details.compact("tool_execution", {"stdout": "中" * INLINE_BYTES})
        assert details.compact("message", {"text": "x" * INLINE_BYTES * 2}) == {
            "text": "x" * INLINE_BYTES * 2,
        }
    finally:
        details.clear()


@pytest.mark.parametrize("offset", [-1, True, "0", 1.5, 1, INLINE_BYTES * 100])
def test_invalid_page_offsets_fail_clearly(offset):
    details = ExecutionDetails()
    try:
        ref = details.compact("tool_result", {"result": "😀" * INLINE_BYTES})["result_ref"]
        with pytest.raises(ValueError):
            details.read({"id": ref["id"], "offset": offset})
        with pytest.raises(ValueError, match="unavailable"):
            details.read({"id": "../../another-session"})
    finally:
        details.clear()


async def test_backend_detail_reads_work_during_tasks_and_survive_conversation_reset(headless_runtime):
    stream = io.StringIO()
    backend = BackendSession(JsonlWriter(stream))

    async def send(kind, identifier, payload):
        await backend.handle(decode_client_message(json.dumps({
            "protocol_version": 2, "type": kind, "request_id": identifier, "payload": payload,
        })))

    try:
        await send("initialize", "init", {})
        original = "body 😀\n" * 10_000
        backend.application.emit("tool_execution", task_id="task", call_id="call",
                                 operation_id="op", kind="http", status="completed",
                                 response_body=original)
        event = json.loads(stream.getvalue().splitlines()[-1])
        assert "response_body" not in event
        ref = event["detail_refs"]["response_body"]
        assert len(json.dumps(event)) < 2048
        # Use the current task as an active owner; reads must remain available.
        backend.application.active_task = asyncio.current_task()
        await send("control", "read", {"operation": "execution.details.read",
                                      "arguments": {"id": ref["id"]}})
        page = json.loads(stream.getvalue().splitlines()[-1])
        assert page["operation"] == "execution.details.read"
        assert "state" not in page
        assert original.startswith(page["result"]["text"])
        backend.application.active_task = None
        await send("control", "new", {"operation": "session.new", "arguments": {}})
        # REPL output queued before /new may still need to finish printing.
        await send("control", "read-again", {"operation": "execution.details.read",
                                            "arguments": {"id": ref["id"]}})
        assert json.loads(stream.getvalue().splitlines()[-1])["result"] == page["result"]
    finally:
        backend.application.active_task = None
        await backend.close()
    with pytest.raises(ValueError, match="unavailable"):
        backend.application.execution_details.read({"id": ref["id"]})


async def test_headless_events_keep_full_output(headless_runtime):
    from vulnclaw.application import ApplicationSession

    events = []
    application = ApplicationSession(lambda kind, **fields: events.append((kind, fields)),
                                     source="headless")
    try:
        original = "x" * INLINE_BYTES * 2
        application.emit("tool_result", result=original)
        assert events == [("tool_result", {"result": original})]
    finally:
        await application.close()
