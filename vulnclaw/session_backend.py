"""JSONL transport adapter for the native terminal client."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, TextIO

from vulnclaw.application import ApplicationError, ApplicationSession, RuntimeFactory, TaskRunner
from vulnclaw.session_protocol import (
    PROTOCOL_VERSION,
    ClientMessage,
    JsonlWriter,
    ProtocolError,
    decode_client_message,
)


class BackendSession:
    """Map protocol requests and application events without owning business state."""

    def __init__(
        self, writer: JsonlWriter, *, runtime_factory: RuntimeFactory = None,
        task_runner: TaskRunner = None,
    ) -> None:
        self.writer = writer
        self.application = ApplicationSession(
            self._event, runtime_factory=runtime_factory, task_runner=task_runner,
        )

    def _event(self, event_type: str, **fields: Any) -> None:
        if event_type == "ready":
            from vulnclaw import __version__

            fields["backend"] = {
                "pid": os.getpid(), "version": __version__,
                "protocol_version": PROTOCOL_VERSION,
            }
        self.writer.event(event_type, **fields)

    async def handle(self, message: ClientMessage) -> None:
        try:
            await self.application.handle(
                message.type, request_id=message.request_id,
                task_id=message.task_id, payload=message.payload,
            )
        except ApplicationError as exc:
            raise ProtocolError(
                exc.code, str(exc), request_id=exc.request_id, task_id=exc.task_id,
            ) from exc

    async def wait_for_idle(self) -> None:
        await self.application.wait_for_idle()

    def state_snapshot(self, *, active: bool | None = None) -> dict[str, Any]:
        if active is None:
            active = self.active_task is not None and not self.active_task.done()
        state = {
            "target": self.current_target,
            "phase": "idle",
            "task_constraints": self.current_constraints,
            "task": {"active": active, "task_id": self.active_task_id if active else None},
            "last_run": self.last_run,
            "findings": _runtime_findings(self.runtime),
            "evidence": [],
            "constraint_violations": [],
        }
        runtime_state = getattr(self.runtime, "state_snapshot", None)
        if callable(runtime_state):
            extra = runtime_state()
            if isinstance(extra, dict):
                state.update(
                    {
                        key: _json_safe(value)
                        for key, value in extra.items()
                        if key in RUNTIME_STATE_FIELDS
                    }
                )
        else:
            agent = getattr(self.runtime, "agent", None)
            session = getattr(agent, "session_state", None)
            if session is not None:
                state.update(
                    {
                        "target": str(getattr(session, "target", "") or self.current_target),
                        "phase": _enum_value(getattr(session, "phase", "idle")),
                        "task_constraints": _model_dump(
                            getattr(session, "task_constraints", self.current_constraints)
                        ),
                        "findings": _runtime_findings(self.runtime),
                        "evidence": _collect_evidence(session),
                        "constraint_violations": list(
                            getattr(session, "constraint_violations", []) or []
                        ),
                    }
                )
        state["task"] = {"active": active, "task_id": self.active_task_id if active else None}
        state["last_run"] = self.last_run
        return _json_safe(state)

    def _require_initialized(self, message: ClientMessage) -> None:
        if not self.initialized:
            raise ProtocolError(
                "not_initialized",
                "initialize must be the first request",
                request_id=message.request_id,
                task_id=message.task_id,
            )


def _session_scope_defaults(bootstrap: dict[str, Any]) -> dict[str, Any]:
    return {
        field: _json_safe(bootstrap[field])
        for field in sorted(SCOPE_FIELDS)
        if field in bootstrap
    }


async def _create_runtime() -> BackendRuntime:
    from vulnclaw.agent.core import AgentCore
    from vulnclaw.config.settings import load_config
    from vulnclaw.mcp.lifecycle import MCPLifecycleManager

    config = load_config()
    mcp_manager = MCPLifecycleManager(config)
    started = mcp_manager.start_enabled_servers()
    return BackendRuntime(config, mcp_manager, AgentCore(config, mcp_manager), started)


class TuiApprovalChannel:
    """Trusted approval channel over the TUI JSONL control path.

    Emits the structured ``approval_required`` event, then awaits the
    operator decision arriving as the ``execution.approval.resolve``
    control operation. The decision never travels through model text.
    """

    def __init__(self, sink: BackendStreamSink) -> None:
        self._sink = sink

    async def request_approval(self, view: Any) -> str:
        from vulnclaw.agent.exec_gate import get_execution_gate

        self._sink._event("approval_required", **view.to_event_payload())
        gate = get_execution_gate()
        return await gate.wait_decision(view.request_hash)

    async def notify_closed(self, request_hash: str, status: str) -> None:
        """Dismiss the client modal when the pending leaves the queue."""
        self._sink._event(
            "approval_closed", request_hash=request_hash, status=status
        )


async def _run_task(
    runtime: BackendRuntime, task: PreparedTask, sink: BackendStreamSink
) -> dict[str, Any]:
    def on_event(kind: str, payload: dict[str, Any]) -> None:
        if kind == "subagent":
            sink._event("subagent", **payload)
        elif kind == "subagent_stream":
            event = dict(payload)
            event_type = event.pop("type")
            if sink.accepts(event_type):
                sink._event(event_type, **event)
        elif kind == "agent_step":
            sink._event("log", message=f"turn {payload.get('step', '?')}")
        elif kind == "error":
            sink._event("log", message=f"error: {payload.get('error', '')}")
        elif kind == "ask_user":
            sink._event("approval_required", question=str(payload.get("question", "")))
        elif kind == "completed":
            sink._event("status", status="goal reached")
        elif kind == "no_path":
            sink._event("log", message=f"no path: {payload.get('reason', '')}")
    execution = None
    approval_channel = TuiApprovalChannel(sink)
    from vulnclaw.agent.exec_gate import get_execution_gate

    _approval_gate = get_execution_gate()
    _previous_channel = _approval_gate.channel
    _approval_gate.install_channel(approval_channel)
    try:
        execution = await execute_task(
            runtime.agent,
            task,
            stream_sink=sink,
            on_event=on_event,
        )
    finally:
        if _previous_channel is not None:
            _approval_gate.install_channel(_previous_channel)
        else:
            _approval_gate.uninstall_channel()
    result = execution.run
    run_context = result.run_context
    report = None
    report_error = None
    if task.request.command == "run" and result.status == "completed":
        sink.on_status("Generating report...")
        try:
            report = await generate_report_for_run(result, config=runtime.config)
        except Exception as exc:  # Report failures preserve the completed task and saved audit.
            report_error = str(exc) or type(exc).__name__
    action_result = execution.action_result
    if isinstance(action_result, dict) and isinstance(action_result.get("findings"), list):
        findings = action_result["findings"]
    else:
        findings = _runtime_findings(runtime)
    return {
        "command": task.request.command,
        "target": task.request.target,
        "status": result.status,
        "exit_code": result.exit_code,
        "summary": _json_safe(result.summary),
        "run": (
            {
                "name": run_context.run_name,
                "directory": str(run_context.run_dir),
                "manifest": _json_safe(run_context.manifest),
            }
            if run_context is not None
            else None
        ),
        "findings": findings,
        "report": report,
        "report_error": report_error,
    }


async def serve(reader: TextIO, writer: JsonlWriter) -> None:
    """Serve one client until EOF or a graceful shutdown request."""
    session = BackendSession(writer)
    pending: set[asyncio.Task[None]] = set()

    async def dispatch(message: ClientMessage) -> None:
        try:
            await session.handle(message)
        except ProtocolError as exc:
            writer.write(exc.as_event())
        except Exception as exc:
            writer.event("error", request_id=message.request_id, task_id=message.task_id,
                         code="internal_error", message=str(exc))

    try:
        while not session.application.shutdown_requested:
            line = await asyncio.to_thread(reader.readline)
            if line == "":
                break
            if not line.strip():
                continue
            try:
                message = decode_client_message(line)
            except ProtocolError as exc:
                writer.write(exc.as_event())
                continue
            if message.type in {"initialize", "shutdown"}:
                await dispatch(message)
            else:
                task = asyncio.create_task(dispatch(message))
                pending.add(task)
                task.add_done_callback(pending.discard)
    finally:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await session.close()


def main() -> None:
    protocol_output = sys.stdout
    sys.stdout = sys.stderr
    asyncio.run(serve(sys.stdin, JsonlWriter(protocol_output)))


def _llm_configured(llm: Any) -> bool:
    """Whether *llm* carries usable credentials (static key or stored OAuth)."""
    if llm is None:
        return False
    try:
        from vulnclaw.config.token_provider import has_llm_credentials

        return bool(has_llm_credentials(llm))
    except Exception:
        return False


def _runtime_metadata(runtime: Any) -> dict[str, Any]:
    custom = getattr(runtime, "metadata", None)
    if callable(custom):
        result = custom()
        if isinstance(result, dict):
            return _json_safe(result)
    config = getattr(runtime, "config", None)
    llm = getattr(config, "llm", None)
    try:
        from vulnclaw.skills.loader import (
            list_core_skills,
            list_custom_skills,
            list_specialized_skills,
        )

        configured = _llm_configured(llm)
        skills = sorted(
            set(list_core_skills() + list_specialized_skills() + list_custom_skills())
        )
    except Exception:
        configured = False
        skills = []
    return {
        "config_ready": configured,
        "provider": str(getattr(llm, "provider", "unknown")),
        "model": str(getattr(llm, "model", "unknown")),
        "mcp_started": int(getattr(runtime, "mcp_started", 0)),
        "skills": skills,
    }


def _runtime_findings(runtime: Any) -> list[dict[str, Any]]:
    agent = getattr(runtime, "agent", None)
    session = getattr(agent, "session_state", None)
    findings = getattr(session, "findings", []) if session is not None else []
    if not findings:
        custom = getattr(runtime, "findings", None)
        if callable(custom):
            findings = custom()
    result: list[dict[str, Any]] = []
    for finding in findings or []:
        item = _model_dump(finding)
        if not isinstance(item, dict):
            continue
        for field in ("submission_id", "evidence_snapshots", "verification_history", "assessment", "previous_verification"):
            item.pop(field, None)
        item["id"] = item.pop("finding_id", item.get("id", ""))
        result.append(_json_safe(item))
    return result


def _collect_evidence(session: Any) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for finding in getattr(session, "findings", []) or []:
        for ref in getattr(finding, "evidence_refs", []) or []:
            dumped = _model_dump(ref)
            if isinstance(dumped, dict):
                evidence.append(_json_safe(dumped))
    return evidence


def _validate_task_action(command: str, constraints: Any) -> None:
    violation = validate_action_constraints(command, constraints)
    if violation is not None:
        raise ValueError(violation)


def _model_dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    if hasattr(value, "value"):
        return _json_safe(value.value)
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _package_version() -> str:
    from vulnclaw import __version__

    return __version__


if __name__ == "__main__":
    main()
