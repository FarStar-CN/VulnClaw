"""Chat Completions protocol collection contracts."""

import pytest

from vulnclaw.llm.adapters.chat_completions import Collector as ChatCollector
from vulnclaw.llm.types import ProtocolError


@pytest.mark.parametrize("first_fn", [None, {}])
def test_chat_id_only_fragment_and_parallel_arguments(first_fn):
    collector = ChatCollector()
    collector.feed(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "id": "c0", "function": first_fn},
                            {
                                "index": 1,
                                "id": "c1",
                                "function": {"name": "second", "arguments": "{}"},
                            },
                        ]
                    }
                }
            ]
        }
    )
    collector.feed(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"name": "first", "arguments": '{"x":'}},
                        ]
                    }
                }
            ]
        }
    )
    collector.feed(
        {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": "1}"}},
                        ]
                    },
                }
            ]
        }
    )
    calls = collector.finish().tool_calls
    assert calls[0].id == "c0" and calls[0].function.arguments == '{"x":1}'
    assert calls[1].id == "c1"


def test_chat_reasoning_not_mixed_with_history_and_no_finish_is_error():
    collector = ChatCollector()
    collector.feed({"choices": [{"delta": {"reasoning_content": "think"}}]})
    collector.feed({"choices": [{"delta": {"content": "answer"}}]})
    with pytest.raises(ProtocolError):
        collector.finish()
    collector.feed({"choices": [{"finish_reason": "stop", "delta": {}}]})
    response = collector.finish()
    assert response.content == "answer"
    assert response.output_items[0]["content"] == "answer"
    assert response.output_items[0]["_chat_message"]["reasoning_content"] == "think"
