"""Wire contracts: these tests use the real SDK over a fake HTTP transport."""

import json
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI

from vulnclaw.config.schema import LLMConfig
from vulnclaw.llm.adapters.responses import Collector, input_items
from vulnclaw.llm.capabilities import api_format, endpoint
from vulnclaw.llm.client import build_request, complete, stream_result
from vulnclaw.llm.types import ProtocolError

TOOL = {
    "type": "function",
    "function": {
        "name": "ping",
        "parameters": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
    },
}


def response(output, status="completed", **extra):
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 1,
        "model": "gpt-5.6-sol",
        "status": status,
        "output": output,
        **extra,
    }


def message(text="done", **extra):
    return {
        "type": "message",
        "id": "msg_test",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
        **extra,
    }


def sdk(handler, base="https://api.openai.com/v1"):
    return OpenAI(
        api_key="fake-test-key",
        base_url=base,
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.parametrize(
    "provider,model,base,effort",
    [
        ("openai", "gpt-5.6-sol", "https://api.openai.com/v1", "xhigh"),
        ("deepseek", "deepseek-v4-pro", "https://api.deepseek.com", "high"),
        ("qwen", "qwen3.8-max", "https://dashscope.aliyuncs.com/compatible-mode/v1", "xhigh"),
        ("moonshot", "kimi-k3", "https://api.moonshot.cn/v1", "max"),
    ],
)
def test_real_sdk_request_wire_and_tool_replay(provider, model, base, effort):
    requests = []
    reasoning = {"type": "reasoning", "id": "rs_test", "summary": [], "encrypted_content": "opaque"}
    call = {
        "type": "function_call",
        "id": "fc_item",
        "call_id": "call_1",
        "name": "ping",
        "arguments": '{"ok":true}',
        "status": "completed",
    }

    def handler(request):
        requests.append(json.loads(request.content))
        assert str(request.url) == base + "/responses"
        return httpx.Response(
            200, json=response([reasoning, call] if len(requests) == 1 else [message()])
        )

    config = LLMConfig(provider=provider, model=model, base_url=base)
    with sdk(handler, base) as client:
        first = complete(config, [{"role": "user", "content": "ping"}], [TOOL], client=client)
        complete(
            config,
            [
                {"role": "user", "content": "ping"},
                *first.output_items,
                {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
            ],
            client=client,
        )
    assert requests[0]["reasoning"] == {"effort": effort}
    assert requests[0]["max_output_tokens"] == 4096
    assert requests[0]["tools"][0]["name"] == "ping"
    assert requests[0]["tools"][0]["strict"] is False
    assert (
        not {"messages", "max_tokens", "max_completion_tokens", "reasoning_effort"}
        & requests[0].keys()
    )
    assert requests[1]["input"][1]["encrypted_content"] == "opaque"
    assert requests[1]["input"][2]["call_id"] == "call_1"
    if provider in {"deepseek", "moonshot"}:
        assert "store" not in requests[0]
        assert "temperature" not in requests[0]


def test_glm_route_and_unverified_budget():
    cfg = LLMConfig(
        provider="zhipu", model="glm-5.3", base_url="https://open.bigmodel.cn/api/paas/v4"
    )
    assert endpoint(cfg) == "https://open.bigmodel.cn/api/v1"
    with pytest.raises(ValueError, match="budget"):
        build_request(cfg, [])
    cfg.max_tokens = None
    diagnostics = []
    payload = build_request(cfg, [{"role": "user", "content": "hi"}], diagnostics=diagnostics)
    assert "reasoning" not in payload
    assert any("unverified" in d.reason for d in diagnostics)


def test_explicit_chat_and_no_responses_fallback():
    cfg = LLMConfig(api_format="chat_completions")
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(404, json={"error": {"message": "not found"}})

    with sdk(handler) as client, pytest.raises(Exception):
        complete(cfg, [{"role": "user", "content": "hi"}], client=client)
    assert seen == ["https://api.openai.com/v1/chat/completions"]


def test_null_and_zero_overrides_differ_from_inherit():
    cfg = LLMConfig(provider="custom", base_url="https://proxy.test/v1")
    payload = build_request(cfg, [], temperature=0.0, top_p=0.0, max_tokens=None)
    assert payload["temperature"] == 0.0 and payload["top_p"] == 0.0
    assert "max_output_tokens" not in payload
    # An unverified endpoint still receives the configured rung verbatim.
    assert payload["reasoning"] == {"effort": "xhigh"}
    assert build_request(cfg, [])["max_output_tokens"] == 4096


@pytest.mark.parametrize(
    "field,value",
    [
        ("temperature", float("nan")),
        ("top_p", -1),
        ("max_tokens", 0),
        ("reasoning_effort", "typo"),
        # Dropped rungs stay rejected rather than being silently coerced.
        ("reasoning_effort", "auto"),
        ("reasoning_effort", "none"),
        ("reasoning_effort", "minimal"),
    ],
)
def test_invalid_config_is_rejected(field, value):
    with pytest.raises(ValueError):
        LLMConfig(**{field: value})


def test_output_phase_preserved_in_next_request():
    _, items = input_items(
        [message("working", phase="commentary"), message("answer", phase="final_answer")]
    )
    assert [item["phase"] for item in items] == ["commentary", "final_answer"]


def test_tool_schema_and_history_reject_ambiguity():
    with pytest.raises(ValueError, match="Duplicate"):
        build_request(LLMConfig(), [], [TOOL, TOOL])
    with pytest.raises(ProtocolError, match="Orphan"):
        input_items([{"type": "function_call_output", "call_id": "missing", "output": "ok"}])


@pytest.mark.parametrize("status", ["completed", "incomplete", "failed"])
async def test_real_sdk_sse_terminal_and_usage(status):
    terminal = response(
        [message("你好")],
        status=status,
        usage={"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
        incomplete_details={"reason": "max_output_tokens"} if status == "incomplete" else None,
    )
    events = [
        {"type": "response.output_text.delta", "sequence_number": 0, "delta": "你好"},
        {"type": "response.output_text.done", "sequence_number": 1, "text": "你好"},
        {"type": f"response.{status}", "sequence_number": 2, "response": terminal},
    ]
    data = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    sink = SimpleNamespace(
        on_content_token=lambda s: text.append(s),
        on_thinking_token=lambda s: None,
        on_stream_end=lambda: None,
    )
    text = []
    with sdk(
        lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data)
    ) as client:
        result = await stream_result(client, LLMConfig(), build_request(LLMConfig(), []), sink)
    assert result.status == status and result.content == "你好"
    assert result.usage.total_tokens == 13
    assert text == ["你好"]
    if status != "completed":
        with pytest.raises(ProtocolError):
            result.require_complete()


def test_eof_is_not_success_and_duplicate_delta_is_not_replayed():
    collector = Collector()
    event = {"type": "response.output_text.delta", "sequence_number": 0, "delta": "partial"}
    assert collector.feed(event) == ("content", "partial")
    assert collector.feed(event) is None
    with pytest.raises(ProtocolError, match="terminal"):
        collector.finish()


def test_provider_presets_and_custom_endpoint_are_distinct():
    assert api_format(LLMConfig(provider="moonshot")) == "responses"
    assert api_format(LLMConfig(provider="ollama")) == "chat_completions"
    cfg = LLMConfig(provider="zhipu", base_url="https://proxy.test/api/paas/v4")
    assert endpoint(cfg) == cfg.base_url


@pytest.mark.parametrize(
    "options",
    [
        {"temperature": float("inf")},
        {"max_tokens": True},
        {"top_p": -0.1},
        {"reasoning_effort": "typo"},
    ],
)
def test_invalid_single_call_override_rejected_before_io(options):
    with pytest.raises(ValueError):
        build_request(LLMConfig(), [], **options)


@pytest.mark.parametrize(
    "base,protocol,expected",
    [
        ("https://api.z.ai/api/paas/v4", "responses", "https://api.z.ai/api/v1"),
        ("https://gateway.test/api/paas/v4", "responses", "https://gateway.test/api/paas/v4"),
        (
            "https://open.bigmodel.cn/api/coding/paas/v4",
            "chat_completions",
            "https://open.bigmodel.cn/api/coding/paas/v4",
        ),
    ],
)
def test_endpoint_routing_is_exact(base, protocol, expected):
    assert endpoint(LLMConfig(provider="zhipu", base_url=base, api_format=protocol)) == expected


def test_glm_offline_request_and_no_claim_of_parameter_support():
    base = "https://open.bigmodel.cn/api/v1"
    cfg = LLMConfig(provider="zhipu", model="glm-5.3", base_url=base, max_tokens=None)

    def handler(request):
        body = json.loads(request.content)
        assert str(request.url) == base + "/responses"
        assert not {"reasoning", "temperature", "top_p", "max_output_tokens"} & body.keys()
        return httpx.Response(200, json=response([message()]))

    with sdk(handler, base) as client:
        assert (
            complete(cfg, [{"role": "user", "content": "hi"}], client=client).status == "completed"
        )


def test_config_null_roundtrip_env_and_invalid_yaml(tmp_path, monkeypatch):
    from vulnclaw.config import settings
    from vulnclaw.config.schema import VulnClawConfig

    path = tmp_path / "config.yaml"
    monkeypatch.setattr(settings, "CONFIG_FILE", path)
    monkeypatch.setattr(settings, "ensure_dirs", lambda: None)
    cfg = VulnClawConfig()
    cfg.llm.max_tokens = cfg.llm.temperature = None
    settings.save_config(cfg)
    loaded = settings.load_config().llm
    assert loaded.max_tokens is None and loaded.temperature is None
    monkeypatch.setenv("VULNCLAW_LLM_TOP_P", "0")
    assert settings.load_config().llm.top_p == 0
    monkeypatch.setenv("VULNCLAW_LLM_TEMPERATURE", "nan")
    with pytest.raises(ValueError):
        settings.load_config()
    monkeypatch.delenv("VULNCLAW_LLM_TEMPERATURE")
    path.write_text("llm:\n  reasoning_effort: typo\n")
    with pytest.raises(ValueError):
        settings.load_config()


def test_reasoning_and_tools_survive_checkpoint_and_reject_split_archive(tmp_path):
    from vulnclaw.agent.context import ContextManager, SessionState
    from vulnclaw.agent.context_vault import VaultManager, assign_refs

    ctx = ContextManager()
    ctx.state.llm_identity = ("openai", "gpt-5.6-sol", "responses", "https://api.openai.com/v1")
    items = [
        {"type": "reasoning", "encrypted_content": "opaque", "summary": []},
        {"type": "function_call", "name": "ping", "call_id": "c", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c", "output": "ok"},
    ]
    ctx.add_messages(items)
    saved = ctx.state.save(tmp_path / "state.json")
    ctx.state = SessionState.load(saved)
    assert ctx.get_messages() == items
    assert ctx.state.llm_identity[2] == "responses"
    vault = VaultManager()
    vault.config.min_archive_messages = 1
    vault.config.min_archive_chars = 0
    assign_refs(ctx.messages, vault.registry)
    ok, reason, _ = vault.archive_range(
        ctx.messages,
        start=ctx.messages[1]["_vault_ref"],
        end=ctx.messages[2]["_vault_ref"],
        tier=1,
        force=True,
    )
    assert not ok and "splits" in reason


def test_completed_stream_item_must_match_terminal():
    collector = Collector()
    collector.feed(
        {"type": "response.output_item.done", "output_index": 0, "item": message("first")}
    )
    collector.feed({"type": "response.completed", "response": response([message("different")])})
    with pytest.raises(ProtocolError, match="conflicts"):
        collector.finish()


@pytest.mark.parametrize("streaming", [False, True])
def test_oauth_401_refreshes_once_on_same_route(monkeypatch, streaming):
    from vulnclaw.llm.client import start_stream

    cfg = LLMConfig(auth_mode="oauth", max_tokens=None)
    calls = []
    refreshes = []
    monkeypatch.setattr(
        "vulnclaw.llm.token_provider.refresh_llm_token",
        lambda config: refreshes.append(config) or "renewed-test-token",
    )

    def handler(request):
        calls.append((str(request.url), request.headers["authorization"]))
        if len(calls) == 1:
            return httpx.Response(401, json={"error": {"message": "expired"}})
        return httpx.Response(200, json=response([message()]))

    with sdk(handler) as client:
        if streaming:
            opened = start_stream(client, cfg, build_request(cfg, []))
            opened.close()
        else:
            assert complete(cfg, [], client=client).status == "completed"
    assert len(refreshes) == 1 and len(calls) == 2
    assert calls[0][0] == calls[1][0] == "https://api.openai.com/v1/responses"
    assert calls[1][1] == "Bearer renewed-test-token"
