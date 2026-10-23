import asyncio
import io
import json
from types import SimpleNamespace

import pytest

from tests.cli.test_session_backend import FakeRuntime
from vulnclaw.agent.context import SessionState
from vulnclaw.agent.exec_gate import GateRequest, get_execution_gate, reset_execution_gate
from vulnclaw.cli.execution import HeadlessExecutor
from vulnclaw.cli.output import CommandOutput
from vulnclaw.input_service import InputRequest
from vulnclaw.task_service import TaskCreateRequest


@pytest.fixture(autouse=True)
def gate():
    reset_execution_gate()
    yield
    reset_execution_gate()


@pytest.mark.parametrize("format", ["json", "jsonl"])
async def test_headless_task_closes_runtime_before_final_result(tmp_path, monkeypatch, format):
    monkeypatch.setattr("vulnclaw.config.settings.RUNS_DIR", tmp_path)
    runtime = FakeRuntime()
    stream = io.StringIO()
    output = CommandOutput(format, stream)

    async def runner(_runtime, _task, sink):
        sink.on_content_token("# 中文 answer")
        return {"status": "completed", "findings": []}

    executor = HeadlessExecutor(output, runtime_factory=lambda: runtime, task_runner=runner)
    result = await executor.execute(InputRequest(task=TaskCreateRequest(command="run", target="example.test")))
    assert runtime.stop_calls == 1
    assert result["status"] == "completed"
    assert result["exit_code"] == 0
    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert lines[-1]["type"] == "command_result"
    assert sum(line["type"] == "command_result" for line in lines) == 1
    assert (len(lines) > 1) is (format == "jsonl")
    assert result["artifacts"][0]["kind"] == "summary"


@pytest.mark.parametrize("interaction", ["approval", "question"])
async def test_required_interaction_terminates_execution_and_preserves_policy(monkeypatch, interaction):
    runtime = FakeRuntime()
    runtime.config.safety.permission_mode = "ask"
    runtime.agent = SimpleNamespace(session_state=SessionState())
    continued = []

    async def execute(_agent, _task, *, stream_sink, on_event, **_kwargs):
        if interaction == "approval":
            await get_execution_gate().authorize(GateRequest("shell", "echo synthetic-command"))
        else:
            on_event("ask_user", {"question": "Which authorized target?"})
        continued.append(True)
        raise AssertionError("execution continued after a required interaction")

    monkeypatch.setattr("vulnclaw.application.execute_task", execute)
    stream = io.StringIO()
    executor = HeadlessExecutor(CommandOutput("jsonl", stream), runtime_factory=lambda: runtime)
    result = await asyncio.wait_for(executor.execute(InputRequest(task=TaskCreateRequest(
        command="run", target="example.test",
    ))), timeout=2)
    assert result["status"] == ("requires_approval" if interaction == "approval" else "requires_input")
    assert result["exit_code"] == 1
    assert continued == []
    assert runtime.stop_calls == 1
    assert get_execution_gate().mode == "ask"
    assert get_execution_gate().channel is None
    assert not get_execution_gate()._pending_by_hash


async def test_cancellation_cleans_up_before_returning_a_machine_result():
    runtime = FakeRuntime()
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def runner(_runtime, _task, sink):
        sink.on_content_token("```python\npartial")
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    stream = io.StringIO()
    executor = HeadlessExecutor(CommandOutput("jsonl", stream), runtime_factory=lambda: runtime, task_runner=runner)
    pending = asyncio.create_task(executor.execute(InputRequest(task=TaskCreateRequest(command="run", target="example.test"))))
    await started.wait()
    pending.cancel()
    result = await pending
    assert cleaned.is_set()
    assert runtime.stop_calls == 1
    assert result["status"] == "cancelled"
    messages = [json.loads(line) for line in stream.getvalue().splitlines()]
    partial = [message for message in messages if message.get("role") == "assistant"]
    assert partial[0]["text"] == "```python\npartial"
    assert partial[-1]["status"] == "cancelled"
    assert messages[-1]["type"] == "command_result"
