"""JSONL transport adapter for the native terminal client."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, TextIO

from vulnclaw.application import ApplicationError, ApplicationSession, RuntimeFactory, TaskRunner
from vulnclaw.tui_protocol import (
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

    async def close(self) -> None:
        await self.application.close()


async def serve(reader: TextIO, writer: JsonlWriter) -> None:
    """Serve one client until EOF or a graceful shutdown request."""
    session = BackendSession(writer)
    try:
        while not session.application.shutdown_requested:
            line = await asyncio.to_thread(reader.readline)
            if line == "":
                break
            if not line.strip():
                continue
            try:
                await session.handle(decode_client_message(line))
            except ProtocolError as exc:
                writer.write(exc.as_event())
            except Exception as exc:
                writer.event("error", code="internal_error", message=str(exc))
    finally:
        await session.close()


def main() -> None:
    protocol_output = sys.stdout
    sys.stdout = sys.stderr
    asyncio.run(serve(sys.stdin, JsonlWriter(protocol_output)))


if __name__ == "__main__":
    main()
