from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from vulnclaw.tui_backend import BackendSession, parse_task_command
from vulnclaw.tui_protocol import JsonlWriter, ProtocolError, decode_client_message


class FakeRuntime:
    def __init__(self) -> None:
        self.stop_calls = 0
        self.run_count = 0

    def metadata(self) -> dict[str, Any]:
        return {
            "config_ready": True,
            "provider": "fake",
            "model": "fake-1",
            "mcp_started": 0,
            "skills": [],
        }

    def state_snapshot(self) -> dict[str, Any]:
        return {"phase": "idle", "runtime_run_count": self.run_count}

    async def stop(self) -> None:
        self.stop_calls += 1


def request(
    kind: str,
    request_id: str,
    *,
    task_id: str | None = None,
    payload: dict[str, Any] | None = None,
):
    raw: dict[str, Any] = {
        "protocol_version": 1,
        "type": kind,
        "request_id": request_id,
        "payload": payload or {},
    }
    if task_id is not None:
        raw["task_id"] = task_id
    return decode_client_message(json.dumps(raw))


def events(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def protocol_validator() -> Draft202012Validator:
    schema_path = Path(__file__).resolve().parents[2] / "protocol" / "tui-v1.schema.json"
    return Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8")))


@pytest.mark.asyncio
async def test_initialize_and_two_tasks_share_one_session_backend_pid() -> None:
    stream = io.StringIO()
    runtime = FakeRuntime()

    async def runner(fake: FakeRuntime, task, sink) -> dict[str, Any]:
        fake.run_count += 1
        sink.on_status(f"running {task.target}")
        return {
            "status": "completed",
            "run": {"name": f"run-{fake.run_count}"},
            "findings": [
                {
                    "id": f"f-{fake.run_count}",
                    "severity": "high",
                    "title": f"Finding {fake.run_count}",
                    "target": task.target,
                }
            ],
        }

    session = BackendSession(
        JsonlWriter(stream), runtime_factory=lambda: runtime, task_runner=runner
    )
    await session.handle(
        request(
            "initialize",
            "r-init",
            payload={
                "bootstrap": {
                    "target": "bootstrap.test",
                    "allow_actions": ["recon", "scan"],
                }
            },
        )
    )
    for index in (1, 2):
        await session.handle(
            request(
                "start_task",
                f"r-{index}",
                task_id=f"t-{index}",
                payload={"command_line": f"/run https://target-{index}.test"},
            )
        )
        await session.wait_for_idle()

    emitted = events(stream)
    validator = protocol_validator()
    for event in emitted:
        validator.validate(event)
    ready = next(event for event in emitted if event["type"] == "ready")
    completed = [event for event in emitted if event["type"] == "task_completed"]
    assert ready["backend"]["pid"] == os.getpid()
    assert ready["capabilities"]["control_operations"] == []
    assert ready["state"]["target"] == "bootstrap.test"
    assert ready["state"]["task_constraints"]["allowed_actions"] == ["recon", "scan"]
    assert runtime.run_count == 2
    assert [event["task_id"] for event in completed] == ["t-1", "t-2"]
    assert [event["findings"][0]["id"] for event in completed] == ["f-1", "f-2"]


@pytest.mark.asyncio
async def test_unadvertised_control_operation_is_rejected() -> None:
    stream = io.StringIO()
    session = BackendSession(JsonlWriter(stream), runtime_factory=FakeRuntime)
    await session.handle(request("initialize", "r-init"))

    with pytest.raises(ProtocolError) as caught:
        await session.handle(
            request(
                "control",
                "r-control",
                payload={"operation": "example.inspect", "arguments": {}},
            )
        )

    assert caught.value.code == "unsupported_operation"


@pytest.mark.asyncio
async def test_concurrent_task_is_rejected_as_busy() -> None:
    stream = io.StringIO()
    started = asyncio.Event()
    release = asyncio.Event()

    async def runner(runtime, task, sink):
        started.set()
        await release.wait()
        return {"findings": []}

    session = BackendSession(
        JsonlWriter(stream), runtime_factory=FakeRuntime, task_runner=runner
    )
    await session.handle(request("initialize", "r-init"))
    await session.handle(
        request(
            "start_task",
            "r-1",
            task_id="t-1",
            payload={"command_line": "/run example.test"},
        )
    )
    await started.wait()

    with pytest.raises(ProtocolError) as caught:
        await session.handle(
            request(
                "start_task",
                "r-2",
                task_id="t-2",
                payload={"command_line": "/run other.test"},
            )
        )
    assert caught.value.code == "task_busy"
    release.set()
    await session.wait_for_idle()


@pytest.mark.asyncio
async def test_initialize_without_target_still_owns_bootstrap_scope() -> None:
    stream = io.StringIO()
    session = BackendSession(JsonlWriter(stream), runtime_factory=FakeRuntime)

    await session.handle(
        request(
            "initialize",
            "r-init",
            payload={
                "bootstrap": {
                    "only_port": 443,
                    "allow_actions": ["recon", "scan"],
                    "block_actions": ["exploit"],
                }
            },
        )
    )

    ready = next(event for event in events(stream) if event["type"] == "ready")
    constraints = ready["state"]["task_constraints"]
    assert constraints["allowed_ports"] == [443]
    assert constraints["allowed_actions"] == ["recon", "scan"]
    assert constraints["blocked_actions"] == ["exploit"]


@pytest.mark.asyncio
async def test_cancel_keeps_backend_available_and_shutdown_stops_runtime_once() -> None:
    stream = io.StringIO()
    runtime = FakeRuntime()
    started = asyncio.Event()

    async def runner(fake, task, sink):
        fake.run_count += 1
        if fake.run_count == 1:
            started.set()
            await asyncio.Event().wait()
        return {"status": "completed", "findings": []}

    session = BackendSession(
        JsonlWriter(stream), runtime_factory=lambda: runtime, task_runner=runner
    )
    await session.handle(request("initialize", "r-init"))
    await session.handle(
        request(
            "start_task",
            "r-1",
            task_id="t-1",
            payload={"command_line": "/run first.test"},
        )
    )
    await started.wait()
    await session.handle(request("cancel_task", "r-cancel", task_id="t-1"))
    await session.wait_for_idle()

    await session.handle(
        request(
            "start_task",
            "r-2",
            task_id="t-2",
            payload={"command_line": "/recon second.test --allow-actions recon"},
        )
    )
    await session.wait_for_idle()
    await session.handle(request("shutdown", "r-shutdown"))

    emitted = events(stream)
    validator = protocol_validator()
    for event in emitted:
        validator.validate(event)
    emitted_types = [event["type"] for event in emitted]
    cancelled = next(event for event in emitted if event["type"] == "task_cancelled")
    assert "task_cancelled" in emitted_types
    assert cancelled["request_id"] == "r-cancel"
    assert "task_completed" in emitted_types
    assert emitted_types[-1] == "shutdown_complete"
    assert runtime.run_count == 2
    assert runtime.stop_calls == 1


def test_python_parses_scope_and_action_constraints() -> None:
    task = parse_task_command(
        "/scan https://app.example/admin --only-port 443 --only-host app.example "
        "--only-path /admin --blocked-host internal.example --blocked-path /debug "
        "--allow-actions recon,scan --block-actions exploit --no-resume"
    )

    assert task.command == "scan"
    assert task.target == "https://app.example/admin"
    assert task.resume is False
    assert task.constraints.allowed_ports == [443]
    assert task.constraints.allowed_hosts == ["app.example"]
    assert task.constraints.allowed_paths == ["/admin"]
    assert task.constraints.blocked_hosts == ["internal.example"]
    assert task.constraints.blocked_paths == ["/debug"]
    assert task.constraints.allowed_actions == ["recon", "scan"]
    assert task.constraints.blocked_actions == ["exploit"]
    assert task.constraints.strict_mode is True


def test_python_rejects_command_outside_allowed_actions() -> None:
    with pytest.raises(ValueError, match="outside allowed actions"):
        parse_task_command("/exploit target.test --allow-actions recon,scan")
