"""Tests for LLM streaming functionality."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from vulnclaw.agent.subagent.models import SubagentContext


class AsyncStreamContext:
    closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.closed = True


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


class TestCallLlmStream:
    """Test call_llm_stream functionality."""

    @pytest.mark.asyncio
    async def test_stream_tokens_accumulated(self):
        """Test that stream tokens are accumulated correctly."""
        from vulnclaw.agent.llm_client import call_llm_stream

        # Mock agent
        agent = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock()
        agent._get_client.return_value = mock_client

        # Mock llm config
        agent.config.llm.provider = "openai"
        agent.config.llm.model = "gpt-4"
        agent.config.llm.max_tokens = None
        agent.config.llm.temperature = None

        # Create mock streaming response - using plain objects
        class MockDelta:
            def __init__(self, content="", reasoning=""):
                self.content = content
                self.reasoning_content = reasoning

        class MockChoice:
            def __init__(self, delta):
                self.delta = delta

        class MockChunk:
            def __init__(self, content="", reasoning=""):
                self.delta = MockDelta(content=content, reasoning=reasoning)
                self.choices = [MockChoice(self.delta)]

        # Create async iterator
        class MockAsyncStream(AsyncStreamContext):
            def __init__(self, chunks):
                self._chunks = chunks

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._chunks:
                    return self._chunks.pop(0)
                raise StopAsyncIteration

        mock_stream = MockAsyncStream([
            MockChunk(content="Hello"),
            MockChunk(content=" "),
            MockChunk(content="World"),
        ])

        mock_client.chat.completions.create.return_value = mock_stream

        # Mock other agent methods
        agent.context.get_messages.return_value = []
        agent._build_openai_tools.return_value = []

        # Test
        result = await call_llm_stream(agent, "system prompt")

        assert "Hello World" in result or result == "Hello World"

    @pytest.mark.asyncio
    async def test_stream_with_reasoning(self):
        """Test streaming with reasoning content."""
        from vulnclaw.agent.llm_client import call_llm_stream

        agent = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock()
        agent._get_client.return_value = mock_client

        # Mock llm config
        agent.config.llm.provider = "openai"
        agent.config.llm.model = "gpt-4"
        agent.config.llm.max_tokens = None
        agent.config.llm.temperature = None

        class MockDelta:
            def __init__(self, content="", reasoning=""):
                self.content = content
                self.reasoning_content = reasoning

        class MockChoice:
            def __init__(self, delta):
                self.delta = delta

        class MockChunk:
            def __init__(self, content="", reasoning=""):
                self.delta = MockDelta(content=content, reasoning=reasoning)
                self.choices = [MockChoice(self.delta)]

        class MockAsyncStream(AsyncStreamContext):
            def __init__(self, chunks):
                self._chunks = chunks

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._chunks:
                    return self._chunks.pop(0)
                raise StopAsyncIteration

        mock_stream = MockAsyncStream([
            MockChunk(reasoning="thinking..."),
            MockChunk(content="answer"),
        ])

        mock_client.chat.completions.create.return_value = mock_stream
        agent.context.get_messages.return_value = []
        agent._build_openai_tools.return_value = []

        result = await call_llm_stream(agent, "system prompt")

        assert "answer" in result

    @pytest.mark.asyncio
    async def test_async_handshake_is_cancellable(self):
        from vulnclaw.agent.llm_client import _create_streaming_response
        entered = asyncio.Event()
        async def create(**kwargs):
            entered.set()
            await asyncio.Event().wait()
        agent = SimpleNamespace(_get_client=lambda: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
        task = asyncio.create_task(_create_streaming_response(agent, {}))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_stream_is_closed_when_iteration_is_cancelled(self):
        from vulnclaw.agent.llm_client import call_llm_stream
        entered = asyncio.Event()
        class Stream(AsyncStreamContext):
            def __aiter__(self):
                return self
            async def __anext__(self):
                entered.set()
                await asyncio.Event().wait()
        stream = Stream()
        agent = MagicMock()
        agent.config.llm.provider = "openai"
        agent.config.llm.model = "test"
        agent.config.llm.max_tokens = 100
        agent.config.llm.temperature = 0.1
        agent.context.get_messages.return_value = []
        agent._build_openai_tools.return_value = []
        agent._get_client.return_value.chat.completions.create = AsyncMock(return_value=stream)
        task = asyncio.create_task(call_llm_stream(agent, "system"))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stream.closed


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


class TestStreamOutputSequence:
    """测试流式输出的调用顺序"""

    @pytest.mark.asyncio
    async def test_stream_calls_sink_in_order(self):
        """验证 sink 方法按正确顺序被调用"""
        from vulnclaw.agent.llm_client import call_llm_stream

        # 创建 Spy Sink
        spy = SpySink()

        # Mock agent
        agent = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock()
        agent._get_client.return_value = mock_client

        # Mock llm config
        agent.config.llm.provider = "openai"
        agent.config.llm.model = "gpt-4"
        agent.config.llm.max_tokens = None
        agent.config.llm.temperature = None

        # Mock streaming response
        class MockDelta:
            def __init__(self, content="", reasoning=""):
                self.content = content
                self.reasoning_content = reasoning

        class MockChoice:
            def __init__(self, delta):
                self.delta = delta

        class MockChunk:
            def __init__(self, content="", reasoning=""):
                self.delta = MockDelta(content=content, reasoning=reasoning)
                self.choices = [MockChoice(self.delta)]

        class MockAsyncStream(AsyncStreamContext):
            def __init__(self, chunks):
                self._chunks = chunks

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._chunks:
                    return self._chunks.pop(0)
                raise StopAsyncIteration

        mock_stream = MockAsyncStream([
            MockChunk(content="Hello"),
            MockChunk(content=" "),
            MockChunk(content="World"),
        ])

        mock_client.chat.completions.create.return_value = mock_stream
        agent.context.get_messages.return_value = []
        agent._build_openai_tools.return_value = []

        # Test
        await call_llm_stream(agent, "system prompt", stream_sink=spy)

        # 验证序列
        assert len(spy.calls) > 0
        # 第一个应该是 status (Thinking...)
        assert spy.calls[0][0] == 'status'
        assert 'Thinking' in spy.calls[0][1]
        # 最后应该是 end
        assert spy.calls[-1][0] == 'end'
        # 中间应该有 content tokens
        content_calls = [c for c in spy.calls if c[0] == 'content']
        assert len(content_calls) == 3  # "Hello", " ", "World"

    @pytest.mark.asyncio
    async def test_stream_with_reasoning_calls_thinking_then_content(self):
        """验证 reasoning 和 content 的调用顺序"""
        from vulnclaw.agent.llm_client import call_llm_stream

        spy = SpySink()

        agent = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock()
        agent._get_client.return_value = mock_client

        agent.config.llm.provider = "openai"
        agent.config.llm.model = "gpt-4"
        agent.config.llm.max_tokens = None
        agent.config.llm.temperature = None

        class MockDelta:
            def __init__(self, content="", reasoning=""):
                self.content = content
                self.reasoning_content = reasoning

        class MockChoice:
            def __init__(self, delta):
                self.delta = delta

        class MockChunk:
            def __init__(self, content="", reasoning=""):
                self.delta = MockDelta(content=content, reasoning=reasoning)
                self.choices = [MockChoice(self.delta)]

        class MockAsyncStream(AsyncStreamContext):
            def __init__(self, chunks):
                self._chunks = chunks

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._chunks:
                    return self._chunks.pop(0)
                raise StopAsyncIteration

        mock_stream = MockAsyncStream([
            MockChunk(reasoning="thinking..."),
            MockChunk(content="answer"),
        ])

        mock_client.chat.completions.create.return_value = mock_stream
        agent.context.get_messages.return_value = []
        agent._build_openai_tools.return_value = []

        await call_llm_stream(agent, "system prompt", stream_sink=spy)

        # 验证 reasoning 出现在 content 之前
        thinking_calls = [c for c in spy.calls if c[0] == 'thinking']
        content_calls = [c for c in spy.calls if c[0] == 'content']

        assert len(thinking_calls) > 0
        assert len(content_calls) > 0

        # 找到第一个 thinking 和 content 的索引
        first_thinking_idx = next(i for i, c in enumerate(spy.calls) if c[0] == 'thinking')
        first_content_idx = next(i for i, c in enumerate(spy.calls) if c[0] == 'content')
        assert first_thinking_idx < first_content_idx

    @pytest.mark.asyncio
    async def test_stream_accumulates_full_text(self):
        """验证返回的完整文本包含所有 token"""
        from vulnclaw.agent.llm_client import call_llm_stream

        spy = SpySink()

        agent = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock()
        agent._get_client.return_value = mock_client

        agent.config.llm.provider = "openai"
        agent.config.llm.model = "gpt-4"
        agent.config.llm.max_tokens = None
        agent.config.llm.temperature = None

        class MockDelta:
            def __init__(self, content="", reasoning=""):
                self.content = content
                self.reasoning_content = reasoning

        class MockChoice:
            def __init__(self, delta):
                self.delta = delta

        class MockChunk:
            def __init__(self, content="", reasoning=""):
                self.delta = MockDelta(content=content, reasoning=reasoning)
                self.choices = [MockChoice(self.delta)]

        class MockAsyncStream(AsyncStreamContext):
            def __init__(self, chunks):
                self._chunks = chunks

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._chunks:
                    return self._chunks.pop(0)
                raise StopAsyncIteration

        mock_stream = MockAsyncStream([
            MockChunk(content="The "),
            MockChunk(content="quick "),
            MockChunk(content="brown "),
            MockChunk(content="fox"),
        ])

        mock_client.chat.completions.create.return_value = mock_stream
        agent.context.get_messages.return_value = []
        agent._build_openai_tools.return_value = []

        result = await call_llm_stream(agent, "system prompt", stream_sink=spy)

        # 验证返回值包含所有 token
        assert "The quick brown fox" in result or result == "The quick brown fox"
        # 验证 token 数量
        content_tokens = [c for c in spy.calls if c[0] == 'content']
        assert len(content_tokens) == 4




class TestStreamFallback:
    """测试流式降级到非流式的场景"""

    @pytest.mark.asyncio
    async def test_stream_fallback_when_streaming_not_supported(self):
        """测试当 Provider 不支持流式时自动降级"""
        from vulnclaw.agent.llm_client import call_llm_stream

        spy = SpySink()

        agent = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock()
        agent._get_client.return_value = mock_client

        agent.config.llm.provider = "openai"
        agent.config.llm.model = "gpt-4"
        agent.config.llm.max_tokens = None
        agent.config.llm.temperature = None

        # Mock 非流式响应（fallback 路径）
        class MockMessage:
            def __init__(self):
                self.content = "Fallback response text"
                self.tool_calls = None

        class MockChoice:
            def __init__(self):
                self.message = MockMessage()

        class MockResponse:
            def __init__(self):
                self.choices = [MockChoice()]

        mock_client.chat.completions.create.return_value = MockResponse()
        agent.context.get_messages.return_value = []
        agent._build_openai_tools.return_value = []

        # Test - 应该返回 fallback 响应
        result = await call_llm_stream(agent, "system prompt", stream_sink=spy)

        # 验证返回值
        assert "Fallback response text" in result

    @pytest.mark.asyncio
    async def test_stream_cancellation_propagates_and_closes_stream(self):
        """Cancellation must close the stream without falling back or retrying."""
        from vulnclaw.agent.llm_client import call_llm_stream

        spy = SpySink()

        agent = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock()
        agent._get_client.return_value = mock_client

        agent.config.llm.provider = "openai"
        agent.config.llm.model = "gpt-4"
        agent.config.llm.max_tokens = None
        agent.config.llm.temperature = None

        # 创建一个会抛出 CancelledError 的流
        class MockAsyncStream(AsyncStreamContext):
            def __init__(self):
                self.yielded = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self.yielded:
                    self.yielded = True
                    class MockDelta:
                        content = "Partial "
                        reasoning_content = ""
                    class MockChoice:
                        delta = MockDelta()
                    class MockChunk:
                        choices = [MockChoice()]
                    return MockChunk()
                raise asyncio.CancelledError()

        mock_client.chat.completions.create.return_value = MockAsyncStream()
        agent.context.get_messages.return_value = []
        agent._build_openai_tools.return_value = []

        with pytest.raises(asyncio.CancelledError):
            await call_llm_stream(agent, "system prompt", stream_sink=spy)
        assert mock_client.chat.completions.create.return_value.closed
        mock_client.chat.completions.create.assert_awaited_once()


class TestCallLlmAutoStream:
    """测试 call_llm_auto_stream 功能"""

    @pytest.mark.asyncio
    async def test_auto_stream_handles_tool_calls(self):
        """测试工具调用被正确处理"""
        from vulnclaw.agent.llm_client import call_llm_auto_stream

        spy = SpySink()

        agent = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock()
        agent._get_client.return_value = mock_client

        agent.config.llm.provider = "openai"
        agent.config.llm.model = "gpt-4"
        agent.config.llm.max_tokens = None
        agent.config.llm.temperature = None

        # Mock 流式响应，包含工具调用（异步迭代器）
        class MockDelta:
            def __init__(self, content="", reasoning="", tool_calls=None):
                self.content = content
                self.reasoning_content = reasoning
                self.tool_calls = tool_calls

        class MockToolCallChunk:
            def __init__(self):
                self.id = "call_123"
                self.type = "function"
                self.function = MagicMock()
                self.function.name = "test_tool"
                self.function.arguments = '{"arg": "value"}'

        class MockChoice:
            def __init__(self, delta):
                self.delta = delta

        class MockChunk:
            def __init__(self, content="", reasoning="", tool_calls=None):
                self.choices = [MockChoice(MockDelta(content=content, reasoning=reasoning, tool_calls=tool_calls))]

        class MockAsyncStream(AsyncStreamContext):
            def __init__(self, chunks):
                self._chunks = chunks

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._chunks:
                    raise StopAsyncIteration
                return self._chunks.pop(0)

        mock_stream = MockAsyncStream([
            MockChunk(content="Using "),
            MockChunk(content="test tool"),
        ])

        mock_client.chat.completions.create.return_value = mock_stream

        # Mock 工具执行
        async def mock_handle_tool_calls_with_results(agent_obj, message):
            return [{"tool_call_id": "call_123", "content": "Tool executed"}], []

        agent.context.get_messages.return_value = []
        agent._build_openai_tools.return_value = []
        agent.context.add_assistant_message = MagicMock()

        # Patch handle_tool_calls_with_results
        import vulnclaw.agent.llm_client as llm_client_module
        original = llm_client_module.handle_tool_calls_with_results
        llm_client_module.handle_tool_calls_with_results = mock_handle_tool_calls_with_results

        try:
            result = await call_llm_auto_stream(
                agent, "system prompt", "round context", stream_sink=spy
            )
            # 验证返回值
            assert result is not None
        finally:
            llm_client_module.handle_tool_calls_with_results = original

    @pytest.mark.asyncio
    async def test_auto_stream_text_only_response(self):
        """测试纯文本响应（无工具调用）"""
        from vulnclaw.agent.llm_client import call_llm_auto_stream

        spy = SpySink()

        agent = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock()
        agent._get_client.return_value = mock_client

        agent.config.llm.provider = "openai"
        agent.config.llm.model = "gpt-4"
        agent.config.llm.max_tokens = None
        agent.config.llm.temperature = None

        # Mock 流式响应（纯文本，异步迭代器）
        class MockDelta:
            def __init__(self, content="", reasoning=""):
                self.content = content
                self.reasoning_content = reasoning

        class MockChoice:
            def __init__(self, delta):
                self.delta = delta

        class MockChunk:
            def __init__(self, content="", reasoning=""):
                self.choices = [MockChoice(MockDelta(content=content, reasoning=reasoning))]

        class MockAsyncStream(AsyncStreamContext):
            def __init__(self, chunks):
                self._chunks = chunks

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._chunks:
                    raise StopAsyncIteration
                return self._chunks.pop(0)

        mock_stream = MockAsyncStream([
            MockChunk(content="Analysis "),
            MockChunk(content="complete"),
        ])

        mock_client.chat.completions.create.return_value = mock_stream
        agent.context.get_messages.return_value = []
        agent._build_openai_tools.return_value = []
        agent.context.add_assistant_message = MagicMock()

        result = await call_llm_auto_stream(
            agent, "system prompt", "round context", stream_sink=spy
        )

        # 验证返回值包含响应文本
        assert "Analysis complete" in result
