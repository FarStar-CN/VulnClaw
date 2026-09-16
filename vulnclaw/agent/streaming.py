"""Project live model callbacks into a shared transcript event vocabulary."""

from collections.abc import Callable


class TranscriptStreamSink:
    def __init__(self, emit: Callable[..., None], *, show_thinking: bool = True) -> None:
        self._emit = emit
        self._show_thinking = show_thinking
        self._segment = ""

    def accepts(self, event_type: str) -> bool:
        """Whether this sink's policy lets an externally built event through.

        Tokens reach the sink through the ``on_*`` callbacks below; events built
        by another producer (a sub-agent stream) arrive already typed, and this
        keeps the filtering decision in one place.
        """
        return event_type != "reasoning" or self._show_thinking

    def on_status(self, message: str) -> None:
        self._segment = ""
        self._emit("status", status=str(message or ""))

    def on_thinking_token(self, token: str) -> None:
        if token and self.accepts("reasoning"):
            self._emit("reasoning", text=str(token), append=self._segment == "reasoning")
            self._segment = "reasoning"

    def on_content_token(self, token: str) -> None:
        if token:
            self._emit("log", message=str(token), append=self._segment == "log")
            self._segment = "log"

    def on_tool_call(self, tool_name: str, args: str) -> None:
        self._segment = ""
        self._emit("tool_call", tool=str(tool_name), arguments=str(args or ""))

    def on_tool_result(self, result_summary: str) -> None:
        self._segment = ""
        self._emit("tool_result", result=str(result_summary or ""))

    def on_stream_end(self) -> None:
        self._segment = ""
