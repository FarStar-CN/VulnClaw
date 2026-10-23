"""Display-only execution observations, scoped to one model tool call."""

from __future__ import annotations

import asyncio
import inspect
import os
import shlex
import subprocess
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator
from uuid import uuid4

import httpx

SENSITIVE_HEADERS = frozenset({
    "authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key",
    "x-auth-token", "x-access-token",
})
_OBSERVER: ContextVar[Callable[..., None] | None] = ContextVar("tool_observer", default=None)
_CALL: ContextVar[CallTrace | None] = ContextVar("tool_call_trace", default=None)


def display_headers(headers: Any) -> dict[str, str]:
    return {str(k): "[masked]" if str(k).lower() in SENSITIVE_HEADERS else str(v)
            for k, v in headers.items()}


def notify_call(sink: Any, tool_call: Any) -> None:
    # Transcript events are emitted by the dispatcher, including non-streaming fallback.
    if not callable(getattr(sink, "on_tool_event", None)):
        sink.on_tool_call(tool_call.function.name, tool_call.function.arguments)


def notify_result(sink: Any, result: dict[str, Any]) -> None:
    if not callable(getattr(sink, "on_tool_event", None)):
        sink.on_tool_result(result["content"])


@contextmanager
def observe_tools(sink: Any) -> Iterator[None]:
    observer = getattr(sink, "on_tool_event", None)
    token = _OBSERVER.set(observer if callable(observer) else None)
    # A child agent starts a fresh tool scope, even when spawned inside agent_run.
    call_token = _CALL.set(None)
    try:
        yield
    finally:
        _CALL.reset(call_token)
        _OBSERVER.reset(token)


@dataclass
class CallTrace:
    call_id: str
    observer: Callable[..., None]
    status: str = "preparing"
    closed: bool = False
    operations: list[Operation] = field(default_factory=list)
    started: float = field(default_factory=time.perf_counter)

    def update(self, status: str, **fields: Any) -> None:
        if self.closed:
            return
        self.status = status
        self.observer("tool_execution", call_id=self.call_id, operation_id=self.call_id,
                      kind="tool", status=status, **fields)


@contextmanager
def trace_call(call_id: str) -> Iterator[None]:
    observer = _OBSERVER.get()
    trace = CallTrace(call_id, observer) if observer else None
    token = _CALL.set(trace)
    if trace:
        trace.update("preparing")
    try:
        yield
    except BaseException as exc:
        if trace:
            status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
            for operation in trace.operations:
                if operation.fields["status"] in {"preparing", "running", "awaiting_approval"}:
                    operation.finish(status, error=str(exc))
            trace.update(status, error=str(exc))
        raise
    finally:
        if trace and trace.status in {"preparing", "running", "awaiting_approval"}:
            statuses = [op.fields["status"] for op in trace.operations]
            failures = {"failed", "timed_out", "cancelled", "denied"}
            failed = [status for status in statuses if status in failures]
            retried = bool(trace.operations and trace.operations[-1].fields.get("attempt", 1) > 1)
            status = (statuses[-1] if retried else
                      "partial" if failed and len(failed) < len(statuses) else
                      failed[-1] if failed else "completed" if statuses else "returned")
            trace.update(status, duration_ms=int((time.perf_counter() - trace.started) * 1000))
        if trace:
            trace.closed = True
        _CALL.reset(token)


def call_status(status: str, **fields: Any) -> None:
    trace = _CALL.get()
    if trace:
        trace.update(status, **fields)


def execution_error(message: str) -> str:
    trace = _CALL.get()
    status = "failed"
    if trace and trace.operations:
        last = trace.operations[-1].fields["status"]
        if last in {"timed_out", "cancelled", "denied"}:
            status = last
    call_status(status, error=message)
    return message


def format_argv(argv: list[str]) -> str:
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def skipped_call(call_id: str, reason: str) -> None:
    observer = _OBSERVER.get()
    if observer:
        observer("tool_execution", call_id=call_id, operation_id=call_id, kind="tool",
                 status="skipped", error=reason)
        observer("tool_result", call_id=call_id, result=reason)


class Operation:
    def __init__(self, kind: str, **fields: Any) -> None:
        self.trace = _CALL.get()
        self.started = time.perf_counter()
        self.fields = {"operation_id": uuid4().hex, "kind": kind, "status": "running",
                       "attempt": 1, **fields}
        if self.trace:
            self.trace.operations.append(self)
            self.trace.update("running")
            self.update()

    def update(self, **fields: Any) -> None:
        self.fields.update(fields)
        if self.trace and not self.trace.closed:
            self.trace.observer("tool_execution", call_id=self.trace.call_id, **self.fields)

    def finish(self, status: str, **fields: Any) -> None:
        self.update(status=status, duration_ms=int((time.perf_counter() - self.started) * 1000),
                    **fields)


async def capture_process(spawn: Callable, argv: list[str], *, cwd: str, env: dict,
                          timeout_s: float, command: str = "", code: str = ""):
    operation = Operation("script" if code else "command", argv=argv,
                          command=command or format_argv(argv), cwd=cwd, code=code)
    try:
        result = await asyncio.get_running_loop().run_in_executor(
            None, lambda: spawn(argv, cwd=cwd, env=env, timeout_s=timeout_s),
        )
    except BaseException as exc:
        operation.finish("cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                         error=str(exc))
        raise
    returncode, stdout, stderr, timed_out = result
    operation.finish("timed_out" if timed_out else "failed" if returncode else "completed",
                     exit_code=returncode, stdout=stdout, stderr=stderr)
    return result


@contextmanager
def observe_process(argv: list[str], *, attempt: int = 1) -> Iterator[Operation]:
    operation = Operation("command", argv=argv, command=format_argv(argv), attempt=attempt)
    try:
        yield operation
    except BaseException as exc:
        operation.finish("timed_out" if isinstance(exc, subprocess.TimeoutExpired) else "failed",
                         error=str(exc))
        raise


def _body(content: bytes, headers: Any) -> str:
    kind = headers.get("content-type", "")
    if kind and not any(part in kind for part in ("text", "json", "xml", "form", "javascript")):
        return f"[binary: {len(content)} bytes]"
    return content.decode("utf-8", errors="replace")


def observe_http_client(client: Any, *, attempt: int = 1) -> Any:
    """Attach public HTTPX hooks without changing transports, redirects or reads."""
    if _CALL.get() is None or not hasattr(client, "event_hooks"):
        return client
    active: ContextVar[list[tuple[Operation, httpx.Response | None]] | None] = ContextVar(
        "http_observations", default=None,
    )

    def request_hook(request: httpx.Request) -> None:
        pending = active.get()
        if pending is None:
            return
        fields: dict[str, Any] = {"method": request.method, "url": str(request.url),
                                  "headers": display_headers(request.headers)}
        try:
            fields["body"] = _body(request.content, request.headers)
        except httpx.RequestNotRead:
            pass
        pending.append((Operation("http", attempt=attempt, **fields), None))

    def response_hook(response: httpx.Response) -> None:
        pending = active.get()
        if pending:
            operation, _ = pending[-1]
            pending[-1] = (operation, response)
            operation.finish("completed", status_code=response.status_code,
                             response_headers=display_headers(response.headers))

    def finish(pending: list, error: BaseException | None = None) -> None:
        for operation, response in pending:
            if response is not None and response.is_stream_consumed:
                operation.update(response_body=_body(response.content, response.headers))
        if error and pending:
            status = ("timed_out" if isinstance(error, httpx.TimeoutException) else
                      "cancelled" if isinstance(error, asyncio.CancelledError) else "failed")
            pending[-1][0].finish(status, error=str(error) or type(error).__name__)

    send = client.send
    if inspect.iscoroutinefunction(send):
        async def async_request(request):
            request_hook(request)

        async def async_response(response):
            response_hook(response)

        client.event_hooks["request"].append(async_request)
        client.event_hooks["response"].append(async_response)

        async def async_send(*args, **kwargs):
            pending: list = []
            token = active.set(pending)
            try:
                result = await send(*args, **kwargs)
                finish(pending)
                return result
            except BaseException as exc:
                finish(pending, exc)
                raise
            finally:
                active.reset(token)

        client.send = async_send
    else:
        client.event_hooks["request"].append(request_hook)
        client.event_hooks["response"].append(response_hook)

        def sync_send(*args, **kwargs):
            pending: list = []
            token = active.set(pending)
            try:
                result = send(*args, **kwargs)
                finish(pending)
                return result
            except BaseException as exc:
                finish(pending, exc)
                raise
            finally:
                active.reset(token)

        client.send = sync_send
    return client
