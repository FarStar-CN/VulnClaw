"""Tests for LLM streaming functionality."""

from __future__ import annotations

from types import SimpleNamespace

from vulnclaw.agent.subagent.models import SubagentContext


def test_subagent_usage_estimates_provider_omitted_fields():
    from vulnclaw.agent.subagent.budget import record_usage

    agent = SimpleNamespace(
        _subagent_ctx=SubagentContext(
            depth=1,
            next_input_token_estimate=123,
        ),
    )
    response = SimpleNamespace(
        usage=SimpleNamespace(completion_tokens=7),
        choices=[
            SimpleNamespace(message=SimpleNamespace(content="short answer"))
        ],
    )

    recorded = record_usage(agent, response)

    assert recorded == 130
    assert agent._subagent_ctx.input_tokens_used == 123
    assert agent._subagent_ctx.output_tokens_used == 7


def test_model_context_cap_compacts_one_oversized_recent_message():
    from vulnclaw.agent.llm_client import _fit_context_window
    from vulnclaw.agent.token_counter import estimate_tokens

    agent = SimpleNamespace(
        _subagent_ctx=SubagentContext(depth=1),
        config=SimpleNamespace(
            llm=SimpleNamespace(max_context_tokens=40_000),
        ),
    )
    messages = [
        {"role": "system", "content": "system contract"},
        {
            "role": "user",
            "content": "e001 beginning " + ("x" * 200_000) + " e999 ending",
        },
    ]

    fitted = _fit_context_window(agent, messages)

    assert estimate_tokens(fitted) <= 36_000
    assert "e001 beginning" in fitted[-1]["content"]
    assert "e999 ending" in fitted[-1]["content"]
    # Unified budget layer clamps the oversized single message (head/tail
    # preserved) instead of dropping it: content must be meaningfully smaller.
    assert len(fitted[-1]["content"]) < len(messages[-1]["content"])


def test_tool_loop_context_stays_within_hot_budget_and_keeps_tool_exchanges():
    from vulnclaw.agent.llm_client import (
        _fit_tool_loop_context,
        _tool_loop_target_budget,
    )
    from vulnclaw.agent.token_counter import estimate_tokens

    agent = SimpleNamespace(context=SimpleNamespace(max_tokens=1_200))
    round_message = {"role": "user", "content": "current task must remain visible"}
    messages = [
        {"role": "system", "content": "system contract"},
        {"role": "user", "content": "old context " + "x" * 4_000},
        round_message,
    ]
    for number in range(3):
        call_id = f"call-{number}"
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": "probe", "arguments": "{}"},
                    }
                ],
            }
        )
        messages.append(
            {"role": "tool", "tool_call_id": call_id, "content": "r" * 2_000}
        )

    fitted = _fit_tool_loop_context(agent, messages, round_message)

    assert _tool_loop_target_budget(agent) == 1_024
    assert estimate_tokens(fitted) <= _tool_loop_target_budget(agent)
    assert round_message in fitted
    for index, message in enumerate(fitted):
        if message.get("role") == "tool":
            predecessor = fitted[index - 1]
            assert predecessor.get("role") == "assistant"
            assert message["tool_call_id"] in {
                item["id"] for item in predecessor["tool_calls"]
            }


def test_tool_loop_uses_stable_prefix_until_high_water_then_falls_to_target():
    from vulnclaw.agent.llm_client import (
        _build_tool_loop_messages,
        _fit_tool_loop_context,
        _tool_loop_hot_budget,
        _tool_loop_target_budget,
    )
    from vulnclaw.agent.token_counter import estimate_tokens

    history = [
        {"role": "user", "content": "previous evidence " + "h" * 2_000},
        {"role": "assistant", "content": "previous assessment " + "a" * 2_000},
    ]
    agent = SimpleNamespace(
        context=SimpleNamespace(max_tokens=3_200, get_messages=lambda: list(history))
    )
    messages, round_message = _build_tool_loop_messages(
        agent,
        "system contract",
        "current task must remain visible",
        include_history=True,
    )
    stable_prefix = list(messages)
    assert _tool_loop_hot_budget(agent) == 3_200
    assert _tool_loop_target_budget(agent) == 2_600

    messages.extend(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "under-high-water",
                        "type": "function",
                        "function": {"name": "probe", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "under-high-water",
                "content": "r" * 2_000,
            },
        ]
    )
    assert estimate_tokens(messages) < _tool_loop_hot_budget(agent)
    assert _fit_tool_loop_context(agent, messages, round_message) is messages
    assert messages[:len(stable_prefix)] == stable_prefix

    for number in range(2):
        call_id = f"overflow-{number}"
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": "probe", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": call_id, "content": "r" * 4_000},
            ]
        )

    assert estimate_tokens(messages) > _tool_loop_hot_budget(agent)
    fitted = _fit_tool_loop_context(agent, messages, round_message)

    assert estimate_tokens(fitted) <= _tool_loop_target_budget(agent)
    assert fitted[0] == stable_prefix[0]
    assert round_message in fitted
    for index, message in enumerate(fitted):
        if message.get("role") == "tool":
            predecessor = fitted[index - 1]
            assert predecessor.get("role") == "assistant"
            assert message["tool_call_id"] in {
                item["id"] for item in predecessor["tool_calls"]
            }


class TestNullSink:
    """Test _NullSink has no side effects."""

    def test_null_sink_on_status(self):
        """on_status should do nothing."""
        from vulnclaw.agent.llm_client import _NullSink

        sink = _NullSink()
        # Should not raise
        sink.on_status("Thinking...")
        sink.on_thinking_token("test")
        sink.on_content_token("test")
        sink.on_tool_call("tool", "args")
        sink.on_tool_result("result")
        sink.on_stream_end()

    def test_null_sink_returns_none(self):
        """All methods should return None."""
        from vulnclaw.agent.llm_client import _NullSink

        sink = _NullSink()
        assert sink.on_status("msg") is None
        assert sink.on_thinking_token("token") is None
        assert sink.on_content_token("token") is None
        assert sink.on_tool_call("name", "args") is None
        assert sink.on_tool_result("result") is None
        assert sink.on_stream_end() is None




class TestTerminalStreamSink:
    """Test TerminalStreamSink."""

    def test_show_thinking_true(self):
        """Test thinking content is shown when show_thinking=True."""
        from vulnclaw.agent.llm_client import _NullSink

        # This is tested implicitly - NullSink should do nothing
        sink = _NullSink()
        sink.on_thinking_token("thinking content")
        # Should not raise

    def test_show_thinking_false(self):
        """Test thinking content is hidden when show_thinking=False."""
        from vulnclaw.agent.llm_client import _NullSink

        sink = _NullSink()
        # Should not raise even with content
        sink.on_thinking_token("thinking content")


class SpySink:
    """记录所有 sink 方法调用的测试用 Sink。"""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def on_status(self, message: str) -> None:
        self.calls.append(('status', message))

    def on_thinking_token(self, token: str) -> None:
        self.calls.append(('thinking', token))

    def on_content_token(self, token: str) -> None:
        self.calls.append(('content', token))

    def on_tool_call(self, tool_name: str, args: str) -> None:
        self.calls.append(('tool_call', f"{tool_name}:{args}"))

    def on_tool_result(self, result_summary: str) -> None:
        self.calls.append(('tool_result', result_summary))

    def on_stream_end(self) -> None:
        self.calls.append(('end', ''))




class TestTerminalStreamSinkRealOutput:
    """测试 TerminalStreamSink 的实际输出"""

    def test_sink_outputs_status(self):
        """测试 on_status 正确输出"""
        import io

        from rich.console import Console

        from vulnclaw.cli.main import TerminalStreamSink

        output = io.StringIO()
        console = Console(file=output, force_terminal=True)
        sink = TerminalStreamSink(console, show_thinking=False)

        sink.on_status("Thinking...")
        sink.on_stream_end()

        result = output.getvalue()
        assert "Thinking" in result

    def test_sink_outputs_content_tokens(self):
        """测试 on_content_token 正确输出"""
        import io

        from rich.console import Console

        from vulnclaw.cli.main import TerminalStreamSink

        output = io.StringIO()
        console = Console(file=output, force_terminal=True)
        sink = TerminalStreamSink(console, show_thinking=False)

        sink.on_content_token("Hello")
        sink.on_content_token(" ")
        sink.on_content_token("World")
        sink.on_stream_end()

        result = output.getvalue()
        assert "Hello" in result
        assert "World" in result

    def test_sink_show_thinking_true_outputs_thinking(self):
        """测试 show_thinking=True 时 thinking 内容被输出"""
        import io

        from rich.console import Console

        from vulnclaw.cli.main import TerminalStreamSink

        output = io.StringIO()
        console = Console(file=output, force_terminal=True)
        sink = TerminalStreamSink(console, show_thinking=True)

        sink.on_thinking_token("thinking...")
        sink.on_stream_end()

        result = output.getvalue()
        assert "thinking" in result

    def test_sink_show_thinking_false_hides_thinking(self, capsys):
        """测试 show_thinking=False 时 thinking 内容被隐藏"""
        import io

        from rich.console import Console

        from vulnclaw.cli.main import TerminalStreamSink

        output = io.StringIO()
        console = Console(file=output, force_terminal=True)
        sink = TerminalStreamSink(console, show_thinking=False)

        sink.on_thinking_token("thinking...")
        sink.on_content_token("answer")
        sink.on_stream_end()

        result = output.getvalue()
        # thinking 不应该在输出中
        # 但 content 应该输出
        assert "answer" in result

    def test_sink_outputs_tool_call(self):
        """测试 on_tool_call 正确输出"""
        import io

        from rich.console import Console

        from vulnclaw.cli.main import TerminalStreamSink

        output = io.StringIO()
        console = Console(file=output, force_terminal=True)
        sink = TerminalStreamSink(console, show_thinking=False)

        sink.on_tool_call("nmap_scan", '{"target": "example.com"}')
        sink.on_stream_end()

        result = output.getvalue()
        assert "nmap_scan" in result
        assert "example.com" in result

    def test_sink_outputs_tool_result(self):
        """测试 on_tool_result 正确输出"""
        import io

        from rich.console import Console

        from vulnclaw.cli.main import TerminalStreamSink

        output = io.StringIO()
        console = Console(file=output, force_terminal=True)
        sink = TerminalStreamSink(console, show_thinking=False)

        sink.on_tool_result("Port is open")
        sink.on_stream_end()

        result = output.getvalue()
        assert "Port" in result
        assert "open" in result

    def test_sink_collapses_long_tool_result_for_terminal_only(self):
        """Long tool results are collapsed for humans without breaking tool flow."""
        import io

        from rich.console import Console

        from vulnclaw.cli.main import TerminalStreamSink

        output = io.StringIO()
        console = Console(file=output, force_terminal=True)
        sink = TerminalStreamSink(console, show_thinking=False)

        long_result = "[tool:fetch] [evidence:e123]\n" + "A" * 5000
        sink.on_tool_result(long_result)
        sink.on_stream_end()

        result = output.getvalue()
        assert result.count("A") < 5000
        assert "terminal preview collapsed" in result
        assert "e123" in result

    def test_sink_prints_payload_like_rich_markup_as_plain_text(self):
        """SQL payloads such as [/**/] must not be parsed as Rich markup."""
        import io

        from rich.console import Console

        from vulnclaw.cli.main import TerminalStreamSink

        output = io.StringIO()
        console = Console(file=output, force_terminal=True)
        sink = TerminalStreamSink(console, show_thinking=False)

        sink.on_tool_result("payload=[/**/]union[/**/]select")
        sink.on_tool_call("fetch", '{"url":"https://example.com/?id=1[/**/]and"}')
        sink.on_stream_end()

        result = output.getvalue()
        assert "[/**/]" in result
        assert "payload=" in result

    def test_solve_event_printer_prints_payload_as_plain_text(self):
        """Observation summaries may contain SQL payload markers."""
        import io

        from rich.console import Console

        from vulnclaw.cli.main import _make_solve_event_printer

        output = io.StringIO()
        console = Console(file=output, force_terminal=True)
        printer = _make_solve_event_printer(console)

        printer(
            "agent_observation",
            {
                "reason": "try [/**/] union",
                "tools": ["fetch"],
                "evidence": "payload [/**/] observed",
            },
        )

        result = output.getvalue()
        assert "[/**/]" in result
        assert "payload" in result
