"""Long-lived Python backend for the native terminal client.

The backend owns VulnClaw business state and serves protocol-v1 JSONL requests
over stdin/stdout.  Human diagnostics are intentionally kept on stderr.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import shlex
import sys
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TextIO
from urllib.parse import urlparse

from vulnclaw.tui_protocol import (
    PROTOCOL_VERSION,
    ClientMessage,
    JsonlWriter,
    ProtocolError,
    decode_client_message,
)

SUPPORTED_COMMANDS = frozenset({"run", "scan", "recon", "exploit", "persistent"})
# Concrete management operations are feature extensions.  The transport and
# capability negotiation live in the architecture layer, while this base
# backend intentionally advertises no optional business controls.
SUPPORTED_CONTROL_OPERATIONS: frozenset[str] = frozenset()
VALUE_OPTIONS = frozenset(
    {
        "--prompt",
        "--engine",
        "--scope",
        "--ports",
        "--cve",
        "--cmd",
        "--only-port",
        "--only-host",
        "--only-path",
        "--blocked-host",
        "--blocked-path",
        "--allow-actions",
        "--block-actions",
        "--snapshot",
        "--run-name",
        "--resume-run",
        "--runs-dir",
        "--target-type",
        "--max-steps",
        "--max-tool-rounds",
        "--max-rounds",
    }
)
REPEATABLE_OPTIONS = frozenset({"--target"})
BOOLEAN_OPTIONS = frozenset(
    {"--resume", "--no-resume", "--mount", "--repair", "--force-fresh", "--no-import"}
)


@dataclass(frozen=True)
class ParsedTask:
    """Python-owned normalized task request."""

    command: str
    target: str
    prompt: str
    resume: bool
    constraints: Any
    options: dict[str, Any] = field(default_factory=dict)
    normalized_command: str = ""


@dataclass
class BackendRuntime:
    """The reusable config/MCP/AgentCore aggregate for one TUI session."""

    config: Any
    mcp_manager: Any
    agent: Any
    mcp_started: int = 0
    stopped: bool = False

    async def stop(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        await self.mcp_manager.astop_all()


RuntimeFactory = Callable[[], Any | Awaitable[Any]]
TaskRunner = Callable[[Any, ParsedTask, "BackendStreamSink"], Awaitable[dict[str, Any]]]


class BackendStreamSink:
    """Adapt AgentCore streaming callbacks to protocol-v1 task events."""

    def __init__(self, writer: JsonlWriter, task_id: str, *, show_thinking: bool) -> None:
        self._writer = writer
        self._task_id = task_id
        self._show_thinking = show_thinking
        self._thinking_buffer = ""
        self._content_buffer = ""

    def _event(self, event_type: str, **fields: Any) -> None:
        self._writer.event(event_type, task_id=self._task_id, **fields)

    def _flush_thinking(self) -> None:
        if self._thinking_buffer and self._show_thinking:
            self._event("reasoning", text=self._thinking_buffer)
        self._thinking_buffer = ""

    def _flush_content(self) -> None:
        if self._content_buffer:
            self._event("log", message=self._content_buffer)
        self._content_buffer = ""

    def _flush_all(self) -> None:
        self._flush_thinking()
        self._flush_content()

    def on_status(self, message: str) -> None:
        self._flush_all()
        self._event("status", status=str(message or ""))

    def on_thinking_token(self, token: str) -> None:
        if not token:
            return
        self._flush_content()
        if self._show_thinking:
            self._thinking_buffer += str(token)

    def on_content_token(self, token: str) -> None:
        if not token:
            return
        self._flush_thinking()
        self._content_buffer += str(token)

    def on_tool_call(self, tool_name: str, args: str) -> None:
        self._flush_all()
        self._event("tool_call", tool=str(tool_name), arguments=str(args or ""))

    def on_tool_result(self, result_summary: str) -> None:
        self._flush_all()
        self._event("tool_result", result=str(result_summary or ""))

    def on_stream_end(self) -> None:
        self._flush_all()


class BackendSession:
    """Stateful request dispatcher for one connected TUI client."""

    def __init__(
        self,
        writer: JsonlWriter,
        *,
        runtime_factory: RuntimeFactory = None,
        task_runner: TaskRunner = None,
    ) -> None:
        self.writer = writer
        self._runtime_factory = runtime_factory or _create_runtime
        self._task_runner = task_runner or _run_task
        self.runtime: Any | None = None
        self.bootstrap: dict[str, Any] = {}
        self.initialized = False
        self.shutdown_requested = False
        self.active_task: asyncio.Task[None] | None = None
        self.active_task_id: str | None = None
        self.cancel_request_id: str | None = None
        self.current_target = ""
        self.current_constraints: dict[str, Any] = {}
        self.last_run: dict[str, Any] | None = None

    async def handle(self, message: ClientMessage) -> None:
        handlers = {
            "initialize": self._initialize,
            "start_task": self._start_task,
            "cancel_task": self._cancel_task,
            "get_state": self._get_state,
            "control": self._control,
            "shutdown": self._shutdown,
        }
        await handlers[message.type](message)

    async def _initialize(self, message: ClientMessage) -> None:
        if self.initialized:
            raise ProtocolError(
                "already_initialized",
                "backend session is already initialized",
                request_id=message.request_id,
            )
        bootstrap = message.payload.get("bootstrap", {})
        if not isinstance(bootstrap, dict):
            raise ProtocolError(
                "invalid_message",
                "initialize payload.bootstrap must be an object",
                request_id=message.request_id,
            )
        self.bootstrap = bootstrap
        self.current_target = str(bootstrap.get("target") or "")
        bootstrap_command = str(bootstrap.get("command") or "run").lstrip("/")
        if bootstrap_command not in SUPPORTED_COMMANDS:
            bootstrap_command = "run"
        try:
            initial_constraints = _build_task_constraints(
                self.current_target, {}, bootstrap
            )
            _validate_task_action(bootstrap_command, initial_constraints)
        except ValueError as exc:
            raise ProtocolError(
                "invalid_bootstrap",
                str(exc),
                request_id=message.request_id,
            ) from exc
        self.current_constraints = _model_dump(initial_constraints)
        created = self._runtime_factory()
        self.runtime = await created if inspect.isawaitable(created) else created
        agent = getattr(self.runtime, "agent", None)
        if agent is not None:
            agent.session_state.target = self.current_target or None
            apply_constraints = getattr(agent, "_apply_task_constraints", None)
            if callable(apply_constraints):
                apply_constraints(initial_constraints)
        self.initialized = True
        self.writer.event(
            "ready",
            request_id=message.request_id,
            backend={
                "pid": os.getpid(),
                "version": _package_version(),
                "protocol_version": PROTOCOL_VERSION,
            },
            capabilities={
                "commands": sorted(SUPPORTED_COMMANDS),
                "control_operations": sorted(SUPPORTED_CONTROL_OPERATIONS),
                "cancellation": True,
                "authoritative_state": True,
            },
            runtime=_runtime_metadata(self.runtime),
            state=self.state_snapshot(),
        )

    async def _start_task(self, message: ClientMessage) -> None:
        self._require_initialized(message)
        if self.active_task is not None and not self.active_task.done():
            raise ProtocolError(
                "task_busy",
                f"task {self.active_task_id} is still active",
                request_id=message.request_id,
                task_id=message.task_id,
            )
        command_line = message.payload.get("command_line")
        if not isinstance(command_line, str) or not command_line.strip():
            raise ProtocolError(
                "invalid_task",
                "start_task payload.command_line must be a non-empty string",
                request_id=message.request_id,
                task_id=message.task_id,
            )
        try:
            task = parse_task_command(command_line, defaults=self.bootstrap)
        except ValueError as exc:
            raise ProtocolError(
                "invalid_task",
                str(exc),
                request_id=message.request_id,
                task_id=message.task_id,
            ) from exc

        self.current_target = task.target
        self.current_constraints = _model_dump(task.constraints)
        agent = getattr(self.runtime, "agent", None)
        if agent is not None:
            agent.session_state.target = task.target
            apply_constraints = getattr(agent, "_apply_task_constraints", None)
            if callable(apply_constraints):
                apply_constraints(task.constraints)
        self.active_task_id = message.task_id
        self.cancel_request_id = None
        self.active_task = asyncio.create_task(
            self._execute_task(message.request_id, message.task_id or "", task)
        )

    async def _execute_task(self, request_id: str, task_id: str, task: ParsedTask) -> None:
        self.writer.event(
            "task_started",
            request_id=request_id,
            task_id=task_id,
            command=task.command,
            normalized_command=task.normalized_command,
            target=task.target,
            resume=task.resume,
            constraints=_model_dump(task.constraints),
            state=self.state_snapshot(),
        )
        sink = BackendStreamSink(
            self.writer,
            task_id,
            show_thinking=bool(
                getattr(getattr(self.runtime, "config", None), "session", None)
                and getattr(self.runtime.config.session, "show_thinking", False)
            ),
        )
        try:
            result = await self._task_runner(self.runtime, task, sink)
            sink.on_stream_end()
            findings = result.get("findings") if isinstance(result, dict) else None
            if not isinstance(findings, list):
                findings = _runtime_findings(self.runtime)
            for finding in findings:
                if isinstance(finding, dict):
                    self.writer.event("finding", task_id=task_id, finding=finding)
            self.last_run = _json_safe(result if isinstance(result, dict) else {})
            self.writer.event(
                "task_completed",
                request_id=request_id,
                task_id=task_id,
                result=self.last_run,
                findings=findings,
                state=self.state_snapshot(active=False),
            )
            self.writer.event("state", task_id=task_id, state=self.state_snapshot(active=False))
        except asyncio.CancelledError:
            sink.on_stream_end()
            self.writer.event(
                "task_cancelled",
                request_id=self.cancel_request_id or request_id,
                task_id=task_id,
                state=self.state_snapshot(active=False),
            )
        except Exception as exc:  # noqa: BLE001 - task failures are protocol data
            sink.on_stream_end()
            self.writer.event(
                "task_failed",
                request_id=request_id,
                task_id=task_id,
                error={"code": "task_failed", "message": str(exc)},
                state=self.state_snapshot(active=False),
            )
        finally:
            self.active_task_id = None
            self.cancel_request_id = None
            self.active_task = None

    async def _cancel_task(self, message: ClientMessage) -> None:
        self._require_initialized(message)
        if (
            self.active_task is None
            or self.active_task.done()
            or message.task_id != self.active_task_id
        ):
            raise ProtocolError(
                "task_not_active",
                f"task {message.task_id} is not active",
                request_id=message.request_id,
                task_id=message.task_id,
            )
        self.cancel_request_id = message.request_id
        self.active_task.cancel()

    async def _get_state(self, message: ClientMessage) -> None:
        self._require_initialized(message)
        self.writer.event(
            "state",
            request_id=message.request_id,
            state=self.state_snapshot(),
        )

    async def _control(self, message: ClientMessage) -> None:
        """Validate the generic management envelope and gate extensions.

        Feature layers add concrete operations and their Python-owned business
        state.  The architecture layer rejects every unadvertised operation so
        replaceable clients can safely rely on capability negotiation.
        """

        self._require_initialized(message)
        operation = message.payload.get("operation")
        arguments = message.payload.get("arguments")
        if not isinstance(operation, str) or not operation.strip():
            raise ProtocolError(
                "invalid_control",
                "control payload.operation must be a non-empty string",
                request_id=message.request_id,
            )
        operation = operation.strip().lower()
        if not isinstance(arguments, dict):
            raise ProtocolError(
                "invalid_control",
                "control payload.arguments must be an object",
                request_id=message.request_id,
            )
        if operation not in SUPPORTED_CONTROL_OPERATIONS:
            raise ProtocolError(
                "unsupported_operation",
                f"unsupported control operation: {operation}",
                request_id=message.request_id,
            )
        result, state = await self._execute_control_operation(operation, arguments)
        self._write_control_result(message, operation, result, state=state)

    async def _execute_control_operation(
        self, operation: str, arguments: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Extension point for feature-owned management operations."""

        del arguments
        raise ProtocolError(
            "unsupported_operation",
            f"unsupported control operation: {operation}",
        )

    def _write_control_result(
        self,
        message: ClientMessage,
        operation: str,
        result: dict[str, Any],
        *,
        state: dict[str, Any] | None = None,
    ) -> None:
        fields: dict[str, Any] = {"operation": operation, "result": _json_safe(result)}
        if state is not None:
            fields["state"] = state
        self.writer.event("control_result", request_id=message.request_id, **fields)

    async def _shutdown(self, message: ClientMessage) -> None:
        self.shutdown_requested = True
        if self.active_task is not None and not self.active_task.done():
            active = self.active_task
            self.cancel_request_id = message.request_id
            active.cancel()
            try:
                await active
            except asyncio.CancelledError:
                pass
        await self.close()
        self.writer.event("shutdown_complete", request_id=message.request_id)

    async def close(self) -> None:
        if self.runtime is None:
            return
        stop = getattr(self.runtime, "stop", None)
        if stop is None:
            return
        result = stop()
        if inspect.isawaitable(result):
            await result

    async def wait_for_idle(self) -> None:
        task = self.active_task
        if task is not None:
            await task

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
                state.update(_json_safe(extra))
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


def parse_task_command(command_line: str, *, defaults: dict[str, Any] | None = None) -> ParsedTask:
    """Parse a raw terminal command into one validated Python task."""

    try:
        tokens = shlex.split(command_line)
    except ValueError as exc:
        raise ValueError(f"could not parse command: {exc}") from exc
    if tokens and tokens[0] == "vulnclaw":
        tokens.pop(0)
    if not tokens:
        raise ValueError("task command is empty")
    command = tokens.pop(0).lstrip("/").lower()
    if command not in SUPPORTED_COMMANDS:
        raise ValueError(f"unsupported task command: {command}")
    if not tokens or tokens[0].startswith("--"):
        raise ValueError(f"/{command} requires a target")
    target = tokens.pop(0).strip()
    if not target:
        raise ValueError("target must not be empty")

    options: dict[str, Any] = {}
    additional_targets: list[str] = []
    index = 0
    while index < len(tokens):
        option = tokens[index]
        if option in BOOLEAN_OPTIONS:
            options[option[2:].replace("-", "_")] = True
            index += 1
            continue
        if option in VALUE_OPTIONS or option in REPEATABLE_OPTIONS:
            if index + 1 >= len(tokens):
                raise ValueError(f"{option} requires a value")
            value = tokens[index + 1]
            if option in REPEATABLE_OPTIONS:
                additional_targets.append(value)
            else:
                options[option[2:].replace("-", "_")] = value
            index += 2
            continue
        raise ValueError(f"unsupported option: {option}")
    if additional_targets:
        options["additional_targets"] = additional_targets

    if "only_port" in options:
        try:
            port = int(options["only_port"])
        except (TypeError, ValueError) as exc:
            raise ValueError("--only-port must be an integer") from exc
        if not 1 <= port <= 65535:
            raise ValueError("--only-port must be between 1 and 65535")
        options["only_port"] = port
    for name in ("max_steps", "max_tool_rounds", "max_rounds"):
        if name in options:
            try:
                options[name] = int(options[name])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"--{name.replace('_', '-')} must be an integer") from exc
            if options[name] < 1:
                raise ValueError(f"--{name.replace('_', '-')} must be positive")
    if options.get("engine") not in (None, "solve", "team", "rounds"):
        raise ValueError("--engine must be one of: solve, team, rounds")

    defaults = defaults or {}
    resume = not bool(options.get("no_resume"))
    if "resume" in options:
        resume = True
    elif "no_resume" not in options and "resume" in defaults:
        resume = bool(defaults["resume"])

    constraints = _build_task_constraints(target, options, defaults)
    _validate_task_action(command, constraints)

    prompt = _build_task_prompt(command, target, options)
    if block := constraints.to_prompt_block():
        prompt = f"{prompt}\n\n{block}"
    normalized = shlex.join([f"/{command}", target, *tokens])
    return ParsedTask(command, target, prompt, resume, constraints, options, normalized)


async def _create_runtime() -> BackendRuntime:
    from vulnclaw.agent.core import AgentCore
    from vulnclaw.config.settings import load_config
    from vulnclaw.mcp.lifecycle import MCPLifecycleManager

    config = load_config()
    mcp_manager = MCPLifecycleManager(config)
    started = mcp_manager.start_enabled_servers()
    return BackendRuntime(config, mcp_manager, AgentCore(config, mcp_manager), started)


async def _run_task(
    runtime: BackendRuntime, task: ParsedTask, sink: BackendStreamSink
) -> dict[str, Any]:
    from vulnclaw.config.schema import resolve_engine
    from vulnclaw.orchestrator import run_agent_task

    agent = runtime.agent

    def on_event(kind: str, payload: dict[str, Any]) -> None:
        if kind == "agent_step":
            sink._event("log", message=f"turn {payload.get('step', '?')}")
        elif kind == "error":
            sink._event("log", message=f"error: {payload.get('error', '')}")
        elif kind == "ask_user":
            sink._event("approval_required", question=str(payload.get("question", "")))
        elif kind == "completed":
            sink._event("status", status="goal reached")
        elif kind == "no_path":
            sink._event("log", message=f"no path: {payload.get('reason', '')}")

    async def runner(shared_agent: Any) -> Any:
        if task.command == "run":
            selected_engine = resolve_engine(runtime.config, task.options.get("engine"))
            if selected_engine == "solve":
                return await shared_agent.solve(
                    task.prompt,
                    target=task.target,
                    max_steps=task.options.get(
                        "max_steps", getattr(runtime.config.session, "solve_max_steps", 80)
                    ),
                    max_tool_rounds=task.options.get(
                        "max_tool_rounds",
                        getattr(runtime.config.session, "solve_max_tool_rounds", 6),
                    ),
                    stream_sink=sink,
                    on_event=on_event,
                    task_constraints=task.constraints,
                )
            shared_agent._apply_task_constraints(task.constraints)
            return await shared_agent.auto_pentest(
                task.prompt,
                target=task.target,
                max_rounds=task.options.get(
                    "max_rounds", getattr(runtime.config.session, "max_rounds", 15)
                ),
                stream_sink=sink,
                engine=selected_engine,
                task_constraints=task.constraints,
            )
        if task.command == "persistent":
            shared_agent._apply_task_constraints(task.constraints)
            return await shared_agent.persistent_pentest(
                task.prompt,
                target=task.target,
                stream_sink=sink,
                task_constraints=task.constraints,
            )
        return await shared_agent.chat(
            task.prompt,
            target=task.target,
            stream_sink=sink,
            task_constraints=task.constraints,
        )

    result = await run_agent_task(
        agent=agent,
        command=task.command,
        target=task.target,
        resume=task.resume,
        snapshot_id=task.options.get("snapshot"),
        run_name=task.options.get("run_name"),
        resume_run_name=task.options.get("resume_run"),
        runs_dir=task.options.get("runs_dir"),
        additional_targets=task.options.get("additional_targets"),
        target_type=task.options.get("target_type"),
        mount=bool(task.options.get("mount")),
        repair=bool(task.options.get("repair")),
        force_fresh=bool(task.options.get("force_fresh")),
        no_import=bool(task.options.get("no_import")),
        runner=runner,
    )
    run_context = result.run_context
    return {
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
        "findings": _runtime_findings(runtime),
    }


async def serve(reader: TextIO, writer: JsonlWriter) -> None:
    """Serve one client until EOF or a graceful shutdown request."""

    session = BackendSession(writer)
    try:
        while not session.shutdown_requested:
            line = await asyncio.to_thread(reader.readline)
            if line == "":
                break
            if not line.strip():
                continue
            try:
                message = decode_client_message(line)
                await session.handle(message)
            except ProtocolError as exc:
                writer.write(exc.as_event())
            except Exception as exc:  # noqa: BLE001 - preserve the backend session
                writer.event("error", code="internal_error", message=str(exc))
    finally:
        if not session.shutdown_requested:
            active = session.active_task
            if active is not None and not active.done():
                active.cancel()
                try:
                    await active
                except asyncio.CancelledError:
                    pass
            await session.close()


def main() -> None:
    # Capture protocol stdout first, then redirect all incidental Rich/print
    # output from config, MCP, and AgentCore to stderr for JSONL integrity.
    protocol_output = sys.stdout
    sys.stdout = sys.stderr
    asyncio.run(serve(sys.stdin, JsonlWriter(protocol_output)))


def _build_task_prompt(command: str, target: str, options: dict[str, Any]) -> str:
    if custom := options.get("prompt"):
        return str(custom)
    if command == "recon":
        return f"Perform authorized reconnaissance against {target} without exploitation."
    if command == "scan":
        port_hint = f", focusing on ports {options['ports']}" if options.get("ports") else ""
        return f"Perform authorized vulnerability scanning against {target}{port_hint} without exploitation."
    if command == "exploit":
        cve_hint = f" using {options['cve']}" if options.get("cve") else ""
        command_hint = options.get("cmd", "id")
        return f"Attempt authorized exploitation against {target}{cve_hint} and verify with command: {command_hint}"
    if command == "persistent":
        return f"Continuously perform an authorized pentest against {target} until stopped."
    scope = options.get("scope", "full")
    return f"Perform an authorized {scope} pentest against {target}. This target is explicitly in scope."


def _runtime_metadata(runtime: Any) -> dict[str, Any]:
    custom = getattr(runtime, "metadata", None)
    if callable(custom):
        result = custom()
        if isinstance(result, dict):
            return _json_safe(result)
    config = getattr(runtime, "config", None)
    llm = getattr(config, "llm", None)
    try:
        from vulnclaw.config.token_provider import has_llm_credentials
        from vulnclaw.skills.loader import (
            list_core_skills,
            list_custom_skills,
            list_specialized_skills,
        )

        configured = bool(llm is not None and has_llm_credentials(llm))
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


def _target_hosts(target: str) -> list[str]:
    parsed = urlparse(target if "://" in target else f"//{target}")
    host = parsed.hostname
    if host:
        return [host.lower()]
    return []


def _build_task_constraints(
    target: str, options: dict[str, Any], defaults: dict[str, Any]
) -> Any:
    from vulnclaw.agent.context import TaskConstraints

    only_host = _option_or_default(options, defaults, "only_host")
    only_path = _option_or_default(options, defaults, "only_path")
    blocked_host = _option_or_default(options, defaults, "blocked_host")
    blocked_path = _option_or_default(options, defaults, "blocked_path")
    only_port = _option_or_default(options, defaults, "only_port")
    if only_port not in (None, ""):
        try:
            only_port = int(only_port)
        except (TypeError, ValueError) as exc:
            raise ValueError("only_port must be an integer") from exc
        if not 1 <= only_port <= 65535:
            raise ValueError("only_port must be between 1 and 65535")

    constraints = TaskConstraints(
        allowed_ports=[only_port] if only_port else [],
        allowed_hosts=[str(only_host)] if only_host else _target_hosts(target),
        blocked_hosts=_split_values(blocked_host),
        allowed_paths=_split_values(only_path),
        blocked_paths=_split_values(blocked_path),
        allowed_actions=_split_values(
            _option_or_default(options, defaults, "allow_actions")
        ),
        blocked_actions=_split_values(
            _option_or_default(options, defaults, "block_actions")
        ),
    )
    constraints.strict_mode = not constraints.is_empty()
    return constraints


def _validate_task_action(command: str, constraints: Any) -> None:
    from vulnclaw.agent.context import validate_action_constraints

    violation = validate_action_constraints(command, constraints)
    if violation is not None:
        raise ValueError(violation)


def _option_or_default(
    options: dict[str, Any], defaults: dict[str, Any], name: str
) -> Any:
    if name in options:
        return options[name]
    return defaults.get(name)


def _split_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        raw = value
    else:
        raw = str(value).split(",")
    return [str(item).strip() for item in raw if str(item).strip()]


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
