"""The same Agent scenarios must remain usable through either explicit protocol."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import OpenAI

from vulnclaw.agent.builtin_tools import build_openai_tools
from vulnclaw.agent.context import ContextManager, SessionState
from vulnclaw.agent.context_budget import _message_history_digest, build_context_budget
from vulnclaw.agent.context_vault import _content_text
from vulnclaw.agent.llm_client import _fit_tool_loop_context, _NullSink, call_llm_auto
from vulnclaw.agent.roles import ROLE_REGISTRY, SUBAGENT_ROLE_REGISTRY
from vulnclaw.agent.token_counter import estimate_tokens, group_tool_exchanges
from vulnclaw.agent.tool_result_overrides import ToolOutput
from vulnclaw.config.schema import VulnClawConfig
from vulnclaw.llm.adapters.responses import input_items
from vulnclaw.llm.client import build_request


@pytest.mark.parametrize("protocol", ["responses", "chat_completions"])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("tool_fails", [False, True])
async def test_function_execution_results_and_history_match_chat(
    protocol, streaming, tool_fails, tmp_path
):
    cfg = VulnClawConfig()
    cfg.llm.model = "gpt-4.1"
    cfg.llm.api_format = protocol
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.path.endswith(
            "/responses" if protocol == "responses" else "/chat/completions"
        )
        first = len(requests) == 1
        if protocol == "responses":
            items = (
                [
                    {
                        "type": "function_call",
                        "name": "shell_command",
                        "call_id": "c1",
                        "arguments": '{"command":"echo hello"}',
                    }
                ]
                if first
                else [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "done"}],
                    }
                ]
            )
            result = {"id": "r1", "status": "completed", "output": items}
            events = [{"type": "response.completed", "response": result}]
        else:
            message = (
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {
                                "name": "shell_command",
                                "arguments": '{"command":"echo hello"}',
                            },
                        }
                    ],
                }
                if first
                else {"role": "assistant", "content": "done"}
            )
            result = {
                "choices": [
                    {"message": message, "finish_reason": "tool_calls" if first else "stop"}
                ]
            }
            delta = copy.deepcopy(message)
            for call in delta.get("tool_calls", []):
                call["index"] = 0
            events = [
                {
                    "choices": [
                        {"delta": delta, "finish_reason": result["choices"][0]["finish_reason"]}
                    ]
                }
            ]
        if body.get("stream"):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content="".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n",
            )
        return httpx.Response(200, json=result)

    with OpenAI(
        api_key="fake",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=0,
    ) as client:
        execute = (
            AsyncMock(side_effect=ValueError("tool failure"))
            if tool_fails
            else AsyncMock(
                return_value=ToolOutput(
                    "hello evidence",
                    {
                        "stdout": "hello",
                        "stderr": "",
                        "outcome": {"type": "exit", "exit_code": 0},
                    },
                )
            )
        )
        obj = SimpleNamespace(
            config=cfg,
            context=ContextManager(),
            _get_client=lambda: client,
            _build_openai_tools=lambda: [],
            _execute_mcp_tool=execute,
        )
        assert (
            await call_llm_auto(
                obj, "system", "task", stream_sink=_NullSink() if streaming else None
            )
            == "done"
        )
    execute.assert_awaited_once_with("shell_command", {"command": "echo hello"})
    sent = requests[1]["input" if protocol == "responses" else "messages"][-1]
    value = sent["output" if protocol == "responses" else "content"]
    assert isinstance(value, str) and ("tool failure" if tool_fails else "hello evidence") in value
    path = obj.context.state.save(tmp_path / "checkpoint.json")
    restored = SessionState.load(path)
    obj.context.state = restored
    replay = build_request(cfg.llm, obj.context.get_messages())
    assert replay["input" if protocol == "responses" else "messages"][-1]["role"] == "assistant"


@pytest.mark.parametrize("role", [None, *ROLE_REGISTRY, *SUBAGENT_ROLE_REGISTRY])
def test_all_role_tools_remain_available_with_identical_schemas(role):
    mcp = SimpleNamespace(
        get_tool_schemas=lambda: [
            {
                "name": "fixture_mcp",
                "description": "test",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ]
    )
    tools = build_openai_tools(mcp, active_role=role)
    cfg = VulnClawConfig().llm
    cfg.model = "gpt-4.1"
    responses = build_request(cfg, [{"role": "user", "content": "task"}], tools).get("tools", [])
    cfg.api_format = "chat_completions"
    chat = build_request(cfg, [{"role": "user", "content": "task"}], tools).get("tools", [])
    assert [{key: value for key, value in t.items() if key != "type"} for t in responses] == [
        t["function"] for t in chat
    ]
    assert {t["name"] for t in responses} == {t["function"]["name"] for t in tools}


def exchange(index, kind="function_call"):
    result_kind = {
        "function_call": "function_call_output",
        "shell_call": "shell_call_output",
        "custom_tool_call": "custom_tool_call_output",
    }[kind]
    call = {"type": kind, "call_id": f"c{index}", "name": "ping", "arguments": "{}"}
    return [
        {
            "type": "reasoning",
            "id": f"rs{index}",
            "summary": [],
            "encrypted_content": f"opaque{index}",
        },
        call,
        {"type": result_kind, "call_id": f"c{index}", "output": "evidence" + "x" * 1400},
    ]


@pytest.mark.parametrize("kind", ["function_call", "shell_call", "custom_tool_call"])
def test_long_responses_loop_keeps_latest_complete_exchange(kind):
    task = {"role": "user", "content": "active task"}
    history = [{"role": "system", "content": "system"}, task]
    rounds = [exchange(i, kind) for i in range(5)]
    for items in rounds:
        history.extend(items)
    original = copy.deepcopy(history)
    assert group_tool_exchanges(history[2:]) == rounds
    fitted = _fit_tool_loop_context(
        SimpleNamespace(context=SimpleNamespace(max_tokens=12000)), history, task
    )
    assert estimate_tokens(fitted) <= 9750
    assert all(item in fitted for item in rounds[-1])
    assert not any(item in fitted for item in rounds[0])
    input_items(fitted)  # Every retained tool still has a matching call/result.
    assert history == original


def test_parallel_calls_and_mcp_approvals_stay_in_one_exchange():
    items = exchange(1)
    items.insert(2, {"type": "function_call", "call_id": "c2", "name": "other", "arguments": "{}"})
    items.insert(3, {"type": "mcp_approval_request", "id": "approval1", "name": "remote"})
    items.extend(
        [
            {"type": "function_call_output", "call_id": "c2", "output": "ok"},
            {"type": "mcp_approval_response", "approval_request_id": "approval1", "approve": False},
        ]
    )
    assert group_tool_exchanges([*items, *exchange(3)]) == [items, exchange(3)]


def test_manual_compaction_and_digest_preserve_responses_observations():
    context = ContextManager()
    observations = [
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "[+] confirmed evidence"}],
        },
        {
            "type": "function_call_output",
            "call_id": "c1",
            "output": "[!] timeout on earlier attempt",
        },
        {
            "type": "shell_call_output",
            "call_id": "c2",
            "output": [
                {
                    "stdout": "[+] service detected",
                    "stderr": "warning",
                    "outcome": {"type": "exit", "exit_code": 0},
                }
            ],
        },
    ]
    for i, item in enumerate(observations):
        context.add_messages([{"role": "user", "content": f"task {i}"}, item])
    context.add_messages([{"role": "user", "content": "new task"}])
    summary = context.compact_messages(max_recent=1)
    assert all(text in summary for text in ("confirmed evidence", "timeout", "service detected"))
    assert "service detected" in "".join(_message_history_digest([[observations[-1]]]))
    assert _content_text({"type": "reasoning", "encrypted_content": "secret opaque"}) == ""


def test_native_instructions_augment_agent_prompt_and_reserve_budget():
    cfg = VulnClawConfig()
    messages = [
        {"role": "system", "content": "agent contract"},
        {"role": "user", "content": "task"},
    ]
    obj = SimpleNamespace(config=cfg)
    baseline = build_context_budget(obj, messages, [])
    cfg.llm.responses = {"instructions": "extra directions " * 100}
    request = build_request(cfg.llm, messages)
    assert request["instructions"].startswith("agent contract\n\nextra directions")
    bounded = build_context_budget(obj, messages, [])
    assert bounded.usable_input_tokens < baseline.usable_input_tokens


async def test_role_denied_patch_returns_failed_status_without_requesting_execution(monkeypatch):
    from unittest.mock import MagicMock

    from vulnclaw.agent.tool_call_manager import handle_tool_calls_with_results
    from vulnclaw.llm.adapters.responses import parse

    gate = MagicMock()
    monkeypatch.setattr("vulnclaw.agent.exec_gate.get_execution_gate", gate)
    cfg = VulnClawConfig()
    obj = SimpleNamespace(config=cfg, active_role="researcher", _execute_mcp_tool=AsyncMock())
    response = parse(
        {
            "status": "completed",
            "output": [
                {
                    "type": "apply_patch_call",
                    "call_id": "p1",
                    "operation": {
                        "type": "create_file",
                        "path": "never-created.txt",
                        "diff": "+hello",
                    },
                }
            ],
        }
    )
    results, _ = await handle_tool_calls_with_results(obj, response)
    assert results[0]["failed"] is True
    wire = response.tool_calls[0].output_item(
        results[0]["native_output"], failed=results[0]["failed"]
    )
    assert wire["status"] == "failed" and "role_tool_violation" in wire["output"]
    gate.assert_not_called()
    obj._execute_mcp_tool.assert_not_called()


def test_compaction_digest_prefers_tool_observations_over_reasoning_and_arguments():
    items = exchange(1)
    items[0]["summary"] = [{"type": "summary_text", "text": "plan to inspect service"}]
    items[-1]["output"] = "[+] confirmed service on port 443"
    digest = "\n".join(_message_history_digest([items]))
    assert "confirmed service on port 443" in digest
    assert "opaque1" not in digest and "plan to inspect" not in digest
