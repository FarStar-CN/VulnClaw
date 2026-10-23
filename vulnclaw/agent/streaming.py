"""Project live model callbacks into a shared transcript event vocabulary."""

from collections.abc import Callable
from uuid import uuid4


class TranscriptStreamSink:
    def __init__(
        self, emit: Callable[..., None], *, show_thinking: bool = True, source: str = "main"
    ) -> None:
        self._emit = emit
        self._show_thinking = show_thinking
        self._segment = ""
        self._source = source
        self._message_id = ""

    def accepts(self, event_type: str, **fields) -> bool:
        """Whether this sink's policy lets an externally built event through.

        Tokens reach the sink through the ``on_*`` callbacks below; events built
        by another producer (a sub-agent stream) arrive already typed, and this
        keeps the filtering decision in one place.
        """
        return not (event_type == "message" and fields.get("role") == "reasoning") or self._show_thinking

    def _append(self, role: str, token: str) -> None:
        if not token:
            return
        if role != self._segment:
            self.on_stream_end()
            self._segment = role
            self._message_id = uuid4().hex
        self._emit(
            "message", message_id=self._message_id, source=self._source, role=role,
            format="markdown", operation="append", text=str(token),
        )

    def on_status(self, message: str) -> None:
        self.on_stream_end()
        self._emit("status", status=str(message or ""))

    def on_thinking_token(self, token: str) -> None:
        if self._show_thinking:
            self._append("reasoning", token)

    def on_content_token(self, token: str) -> None:
        self._append("assistant", token)

    def on_tool_event(self, event_type: str, **fields) -> None:
        self.on_stream_end()
        self._emit(event_type, agent_id=self._source, **fields)

    def on_tool_call(self, tool_name: str, args: str) -> None:
        self.on_stream_end()
        self._emit("tool_call", tool=str(tool_name), arguments=str(args or ""))

    def on_tool_result(self, result_summary: str, *, format: str = "text") -> None:
        self.on_stream_end()
        self._emit("tool_result", result=str(result_summary or ""), format=format)

    def on_stream_end(self, status: str = "completed") -> None:
        if self._message_id:
            self._emit(
                "message", message_id=self._message_id, source=self._source,
                role=self._segment, format="markdown", operation="end", status=status,
            )
        self._message_id = ""
        self._segment = ""
