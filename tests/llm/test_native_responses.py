"""Provider-specific wire contracts for the union of Responses capabilities."""

import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI

from vulnclaw.config.schema import PROVIDER_PRESETS, LLMConfig, LLMProvider
from vulnclaw.llm import lifecycle
from vulnclaw.llm.adapters.responses import Collector, input_items, parse, tools_for_wire
from vulnclaw.llm.client import build_request, stream_result
from vulnclaw.llm.types import ProtocolError


def config(provider="openai", **overrides):
    preset = PROVIDER_PRESETS[LLMProvider(provider)]
    return LLMConfig(
        **{
            "provider": provider,
            "model": preset["default_model"],
            "base_url": preset["base_url"],
            **overrides,
        }
    )


def response(output=(), status="completed", **extra):
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 1,
        "model": "test",
        "status": status,
        "output": list(output),
        **extra,
    }


def sdk(handler, base="https://api.openai.com/v1"):
    return OpenAI(
        api_key="fake",
        base_url=base,
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.parametrize("provider", ["openai", "zhipu", "deepseek", "qwen", "moonshot"])
async def test_provider_specific_sse_and_history(provider):
    cfg = config(provider)
    reasoning = {"id": "rs1", "type": "reasoning", "summary": []}
    if provider == "deepseek":
        reasoning["content"] = [{"type": "reasoning_text", "text": "Use ping"}]
        event_name = "response.reasoning_text.delta"
    elif provider in {"qwen", "zhipu"}:
        reasoning["summary"] = [{"type": "summary_text", "text": "Use ping"}]
        event_name = "response.reasoning_text.delta"
    else:
        reasoning.update(
            encrypted_content="opaque", summary=[{"type": "summary_text", "text": "Use ping"}]
        )
        event_name = "response.reasoning_summary_text.delta"
    call = {
        "type": "function_call",
        "id": "fc1",
        "call_id": "call1",
        "name": "ping",
        "arguments": "{}",
        "status": "completed",
    }
    usage = {
        "input_tokens": 12,
        "output_tokens": 4,
        "total_tokens": 16,
        "input_tokens_details": {"cached_tokens": 2, "cache_write_tokens": 3},
        "output_tokens_details": {"reasoning_tokens": 3},
    }
    events = [
        {"type": event_name, "delta": "Use ping", "sequence_number": 0},
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": reasoning,
            "sequence_number": 1,
        },
        {
            "type": "response.output_item.done",
            "output_index": 1,
            "item": call,
            "sequence_number": 2,
        },
        {
            "type": "response.completed",
            "response": response([reasoning, call], usage=usage),
            "sequence_number": 3,
        },
    ]
    wire = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    observed, thinking = [], []
    sink = SimpleNamespace(
        on_content_token=lambda _: None,
        on_thinking_token=thinking.append,
        on_stream_end=lambda: None,
        on_response_event=observed.append,
    )
    with sdk(
        lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=wire),
        cfg.base_url,
    ) as client:
        result = await stream_result(client, cfg, build_request(cfg, "ping"), sink)
    assert thinking == ["Use ping"] and len(observed) == 4
    assert result.usage.raw == usage
    _, replay = input_items([*result.output_items, result.tool_calls[0].output_item("ok")])
    assert replay[0] == reasoning and replay[-1]["call_id"] == "call1"


@pytest.mark.parametrize("provider", ["openai", "deepseek", "qwen"])
def test_native_effort_none_and_sampling(provider):
    request = build_request(
        config(provider), "hello", reasoning={"effort": "none"}, temperature=0.4
    )
    assert request["reasoning"]["effort"] == "none"
    assert request["temperature"] == 0.4


@pytest.mark.parametrize("model", ["qwen3.8-max", "qwen3.7-max", "qwen3.5-plus"])
@pytest.mark.parametrize("parameter,value", [("temperature", 2), ("top_p", 0)])
def test_qwen_sampling_constraints_apply_to_all_models(model, parameter, value):
    with pytest.raises(ValueError, match=parameter):
        build_request(config("qwen", model=model), "hello", **{parameter: value})


def test_native_options_precedence_extensions_and_no_mutation():
    cfg = config(
        "qwen",
        responses={"reasoning": {"effort": "medium"}, "ocr_options": {"task": "document_parsing"}},
    )
    original = cfg.model_dump()
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=response())

    with sdk(handler, cfg.base_url) as client:
        lifecycle.submit(cfg, "hello", client=client, reasoning={"effort": "low"}, temperature=0.5)
    assert captured[0]["reasoning"] == {"effort": "low"}
    assert captured[0]["ocr_options"] == {"task": "document_parsing"}
    assert "extra_body" not in captured[0]
    assert cfg.model_dump() == original


def test_unsupported_optional_parameters_are_reported_before_sending():
    diagnostics = []
    request = build_request(
        config("deepseek"),
        "hello",
        diagnostics=diagnostics,
        metadata={"tag": "test"},
        parallel_tool_calls=False,
        reasoning={"summary": "auto"},
        text={"verbosity": "high"},
    )
    assert not {"metadata", "parallel_tool_calls"} & request.keys()
    assert "summary" not in request["reasoning"] and "verbosity" not in request["text"]
    assert {"metadata", "parallel_tool_calls", "reasoning.summary", "text.verbosity"} <= {
        d.parameter for d in diagnostics
    }


@pytest.mark.parametrize(
    "provider,options",
    [
        ("deepseek", {"previous_response_id": "r1"}),
        ("qwen", {"background": True}),
        ("moonshot", {"tool_choice": "required"}),
        ("moonshot", {"tools": [{"type": "shell"}]}),
    ],
)
def test_unsupported_semantics_are_not_silently_dropped(provider, options):
    with pytest.raises(ValueError):
        build_request(config(provider), "hello", **options)


@pytest.mark.parametrize(
    "options",
    [
        {"extra_body": {"model": "wrong"}},
        {"extra_headers": {"Authorization": "wrong"}},
        {"metadata": {"bad": float("nan")}},
        {"background": True, "store": False},
        {"previous_response_id": "r1", "conversation": "c1"},
    ],
)
def test_invalid_native_options_fail_before_network(options):
    with pytest.raises(ValueError):
        build_request(config(), "hello", **options)


def test_multimodal_and_native_items_roundtrip_without_mutation():
    history = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,AA==", "detail": "low"},
                },
                {"type": "input_file", "file_id": "file_test"},
            ],
        },
        {"type": "compaction", "id": "cmp1", "encrypted_content": "opaque"},
        {
            "type": "web_search_call",
            "id": "ws1",
            "status": "completed",
            "action": {"type": "search"},
        },
    ]
    original = copy.deepcopy(history)
    _, items = input_items(history)
    assert items[0]["content"][0] == {
        "type": "input_image",
        "image_url": "data:image/png;base64,AA==",
        "detail": "low",
    }
    assert items[1:] == history[1:] and history == original
    parsed = parse(response(history[1:], service_tier="priority"))
    assert parsed.raw["service_tier"] == "priority"


def test_strict_and_namespaced_custom_tools_and_results():
    tools = [
        {
            "type": "namespace",
            "name": "workspace",
            "description": "local",
            "tools": [
                {
                    "type": "function",
                    "name": "read",
                    "description": "",
                    "strict": True,
                    "parameters": {"type": "object", "properties": {}},
                },
                {"type": "custom", "name": "patch", "format": {"type": "text"}},
            ],
        }
    ]
    assert tools_for_wire(tools) == tools
    item = {
        "type": "custom_tool_call",
        "call_id": "c1",
        "name": "patch",
        "namespace": "workspace",
        "input": "raw diff",
    }
    result = parse(response([item]))
    call = result.tool_calls[0]
    assert call.namespace == "workspace" and json.loads(call.function.arguments) == {
        "input": "raw diff"
    }
    output = call.output_item("applied")
    assert output["type"] == "custom_tool_call_output"
    assert input_items([item, output])[1] == [item, output]
    with pytest.raises(ProtocolError, match="type"):
        input_items([item, {**output, "type": "function_call_output"}])


def test_hosted_shell_is_not_executed_locally_and_replays():
    item = {
        "type": "shell_call",
        "call_id": "c1",
        "action": {"commands": ["echo remote"]},
        "environment": {"type": "container_reference", "container_id": "cntr1"},
    }
    output = {"type": "shell_call_output", "call_id": "c1", "output": []}
    parsed = parse(response([item, output]))
    assert parsed.tool_calls == []
    assert input_items(parsed.output_items)[1] == [item, output]


@pytest.mark.parametrize("item", [{"type": "computer_call"}, {"type": "computer_call_output"}])
def test_computer_use_is_excluded(item):
    with pytest.raises(ProtocolError, match="Computer"):
        input_items([item])
    with pytest.raises(ProtocolError, match="Computer"):
        parse(response([item]))
    with pytest.raises(ValueError, match="Computer"):
        tools_for_wire([{"type": "computer"}])


def test_refusal_deltas_deduplicate_and_cancelled_terminal_is_preserved():
    collector = Collector()
    delta = {"type": "response.refusal.delta", "delta": "Cannot", "sequence_number": 1}
    assert collector.feed(delta) == ("content", "Cannot")
    assert collector.feed(delta) is None and not collector.accepted_event
    collector.feed({"type": "response.cancelled", "response": response(status="cancelled")})
    assert collector.finish().status == "cancelled"


def test_background_submit_retrieve_cancel_and_delete_use_same_route():
    requests = []

    def handler(request):
        requests.append((request.method, request.url.path))
        if request.method == "DELETE":
            return httpx.Response(
                200, json={"id": "resp_1", "object": "response.deleted", "deleted": True}
            )
        if request.url.path.endswith("/cancel"):
            return httpx.Response(200, json=response(status="cancelled"))
        if request.method == "POST":
            assert json.loads(request.content)["store"] is True
            return httpx.Response(200, json=response(status="queued"))
        return httpx.Response(200, json=response(status="in_progress"))

    cfg = config()
    with sdk(handler) as client:
        assert lifecycle.submit(cfg, "hello", client=client, background=True).pending
        assert lifecycle.retrieve(cfg, "resp_1", client=client).pending
        assert lifecycle.cancel(cfg, "resp_1", client=client).status == "cancelled"
        assert lifecycle.delete(cfg, "resp_1", client=client) is None
    assert requests == [
        ("POST", "/v1/responses"),
        ("GET", "/v1/responses/resp_1"),
        ("POST", "/v1/responses/resp_1/cancel"),
        ("DELETE", "/v1/responses/resp_1"),
    ]


def test_server_history_accepts_external_call_result_only_when_explicit():
    output = {"type": "function_call_output", "call_id": "c1", "output": "ok"}
    with pytest.raises(ProtocolError, match="Orphan"):
        build_request(config(), [output])
    assert build_request(config(), [output], previous_response_id="resp_1")["input"] == [output]


def test_native_options_config_roundtrip_and_env(tmp_path, monkeypatch):
    from vulnclaw.config import settings
    from vulnclaw.config.schema import VulnClawConfig

    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "config.yaml")
    monkeypatch.setattr(settings, "ensure_dirs", lambda: None)
    cfg = VulnClawConfig(
        llm=config(
            responses={"reasoning": {"context": "all_turns"}, "tools": [{"type": "web_search"}]}
        )
    )
    settings.save_config(cfg)
    assert settings.load_config().llm.responses == cfg.llm.responses
    settings.set_config_value("llm.responses", '{"reasoning":{"effort":"none"}}')
    assert settings.load_config().llm.responses["reasoning"]["effort"] == "none"
    monkeypatch.setenv("VULNCLAW_LLM_RESPONSES", '{"text":{"verbosity":"low"}}')
    assert settings.load_config().llm.responses == {"text": {"verbosity": "low"}}


def test_lifecycle_compaction_count_and_pagination_close_owned_client(monkeypatch):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/compact"):
            data = json.loads(request.content)
            assert data["instructions"] == "system"
            return httpx.Response(
                200,
                json={
                    "id": "cmp1",
                    "object": "response.compaction",
                    "created_at": 1,
                    "output": [{"type": "compaction", "encrypted_content": "opaque"}],
                },
            )
        if request.url.path.endswith("/input_tokens"):
            assert "max_output_tokens" not in json.loads(request.content)
            return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 12})
        after = request.url.params.get("after")
        item = {"type": "message", "id": "msg2" if after else "msg1", "role": "user", "content": []}
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [item],
                "has_more": not bool(after),
                "first_id": item["id"],
                "last_id": item["id"],
            },
        )

    clients = []

    def make_client(_):
        client = sdk(handler)
        clients.append(client)
        return client

    monkeypatch.setattr(lifecycle, "make_client", make_client)
    cfg = config()
    assert (
        lifecycle.compact(
            cfg, [{"role": "system", "content": "system"}, {"role": "user", "content": "hello"}]
        )["output"][0]["type"]
        == "compaction"
    )
    assert lifecycle.count_input_tokens(cfg, "hello")["input_tokens"] == 12
    assert [item["id"] for item in lifecycle.list_input_items(cfg, "resp_1")] == ["msg1", "msg2"]
    assert len(paths) == 4 and all(client.is_closed() for client in clients)


async def test_resume_stream_uses_cursor_and_closes_owned_client(monkeypatch):
    event = {"type": "response.completed", "sequence_number": 5, "response": response()}

    def handler(request):
        assert request.method == "GET"
        assert request.url.params["starting_after"] == "4"
        assert request.url.params["stream"] == "true"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=f"event: response.completed\ndata: {json.dumps(event)}\n\n",
        )

    client = sdk(handler)
    monkeypatch.setattr(lifecycle, "make_client", lambda _: client)
    observed = []
    sink = SimpleNamespace(
        on_content_token=lambda _: None,
        on_thinking_token=lambda _: None,
        on_stream_end=lambda: None,
        on_response_event=observed.append,
    )
    assert (
        await lifecycle.resume_stream(config(), "resp_1", sink, starting_after=4)
    ).status == "completed"
    assert len(observed) == 1 and client.is_closed()


def test_native_text_options_keep_explicit_response_format():
    request = build_request(
        config(), "hello", text={"verbosity": "low"}, response_format={"type": "json_object"}
    )
    assert request["text"] == {"verbosity": "low", "format": {"type": "json_object"}}


@pytest.mark.parametrize(
    "provider,options,expected",
    [
        (
            "openai",
            {
                "tools": [{"type": "image_generation", "size": "1024x1024"}],
                "context_management": [{"type": "compaction", "compact_threshold": 20000}],
            },
            {"context_management", "tools"},
        ),
        (
            "deepseek",
            {
                "tools": [{"type": "custom", "name": "apply_patch", "format": {"type": "text"}}],
                "text": {"format": {"type": "json_object"}},
            },
            {"tools", "text"},
        ),
        (
            "moonshot",
            {
                "tools": [
                    {
                        "type": "namespace",
                        "name": "edits",
                        "tools": [{"type": "custom", "name": "apply_patch"}],
                    }
                ],
                "prompt_cache_key": "test",
                "prompt_cache_options": {"ttl": "5m"},
            },
            {"tools", "prompt_cache_key", "prompt_cache_options"},
        ),
        ("qwen", {"tools": [{"type": "web_search_image"}, {"type": "image_search"}]}, {"tools"}),
        (
            "zhipu",
            {
                "tools": [
                    {
                        "type": "function",
                        "name": "ping",
                        "parameters": {"type": "object", "properties": {}},
                    }
                ]
            },
            {"tools", "reasoning", "max_output_tokens"},
        ),
    ],
)
def test_provider_native_options_survive_actual_sdk_serialization(provider, options, expected):
    cfg = config(provider)
    captured = []
    artifact = {
        "type": "image_generation_call",
        "id": "image1",
        "status": "completed",
        "result": "base64",
    }

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=response([artifact], ocr_result={"pages": ["text"]}))

    with sdk(handler, cfg.base_url) as client:
        result = lifecycle.submit(cfg, "hello", client=client, **options)
    assert expected <= captured[0].keys()
    assert captured[0]["tools"] == build_request(cfg, "hello", **options)["tools"]
    assert result.output_items == [artifact] and result.raw["ocr_result"] == {"pages": ["text"]}
