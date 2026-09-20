"""Tests for the built-in ChatGPT-backend bridge proxy."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from vulnclaw.llm import chatgpt_proxy as cp

# ── Pure translation functions ───────────────────────────────────────


@pytest.fixture
def mock_backend(monkeypatch):
    received: dict = {}

    def _sse(event: dict) -> bytes:
        return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()

    class Backend(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 — emulates the streaming Responses backend
            received["auth"] = self.headers.get("Authorization")
            received["account"] = self.headers.get("chatgpt-account-id")
            n = int(self.headers.get("Content-Length", 0))
            received["body"] = json.loads(self.rfile.read(n).decode())
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(_sse({"type": "response.created", "response": {"id": "resp_x"}}))
            self.wfile.write(
                _sse({"type": "response.output_text.delta", "delta": "po"})
            )
            self.wfile.write(
                _sse({"type": "response.output_text.delta", "delta": "ng"})
            )
            self.wfile.write(
                _sse(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_x", "status": "completed",
                            "output": [{"type": "message", "role": "assistant",
                                        "content": [{"type": "output_text", "text": "pong"}]}],
                            "usage": {
                                "input_tokens": 1,
                                "output_tokens": 1,
                                "total_tokens": 2,
                            },
                        },
                    }
                )
            )

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Backend)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("VULNCLAW_CHATGPT_BACKEND_URL", f"http://127.0.0.1:{port}/responses")
    monkeypatch.setattr(cp, "_resolve_credentials", lambda: ("tok-123", "acct-9"))
    monkeypatch.setattr(cp, "list_backend_models", lambda token, account_id: ["gpt-5.5"])
    yield received
    srv.shutdown()


def _post(url, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read().decode()
    except urllib.error.HTTPError as exc:
        raise AssertionError(exc.read().decode()) from exc


def test_proxy_non_stream(mock_backend):
    base = cp.ensure_proxy_running()
    body = _post(
        base + "/responses",
        {"model": "gpt-5", "input": [{"type": "message", "role": "user", "content": "ping"}]},
    )
    chat = json.loads(body)
    assert chat["output"][0]["content"][0]["text"] == "pong"
    assert mock_backend["auth"] == "Bearer tok-123"
    assert mock_backend["account"] == "acct-9"
    # request was translated into Responses shape (streaming + store=false)
    assert mock_backend["body"]["input"][0]["type"] == "message"
    assert mock_backend["body"]["stream"] is True
    assert mock_backend["body"]["store"] is False


def test_proxy_stream(mock_backend):
    base = cp.ensure_proxy_running()
    body = _post(
        base + "/responses",
        {"model": "gpt-5", "input": [{"type": "message", "role": "user", "content": "ping"}], "stream": True},
    )
    assert "data: " in body
    assert "response.completed" in body
    first = json.loads(body.split("data: ", 1)[1].split("\n", 1)[0])
    assert first["type"] == "response.created"
    assert '"delta": "po"' in body
    assert '"delta": "ng"' in body


def test_proxy_models_endpoint(mock_backend):
    base = cp.ensure_proxy_running()
    with urllib.request.urlopen(base + "/models", timeout=10) as r:
        data = json.loads(r.read().decode())
    assert data["object"] == "list"
    assert any(m["id"] for m in data["data"])
