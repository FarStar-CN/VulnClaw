"""Local Responses forwarding for the existing ChatGPT subscription login.

The subscription backend is separate from the public OpenAI API. Its URL and
headers remain overridable; no Chat Completions translation is performed.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# ── Upstream (ChatGPT backend) configuration — all overridable via env ───
DEFAULT_BACKEND_URL = "https://chatgpt.com/backend-api/codex/responses"
DEFAULT_MODELS_URL = "https://chatgpt.com/backend-api/codex/models"
DEFAULT_CLIENT_VERSION = "1.0.0"


def _backend_url() -> str:
    return os.environ.get("VULNCLAW_CHATGPT_BACKEND_URL", DEFAULT_BACKEND_URL)


def _models_url() -> str:
    return os.environ.get("VULNCLAW_CHATGPT_MODELS_URL", DEFAULT_MODELS_URL)


def _client_version() -> str:
    return os.environ.get("VULNCLAW_CHATGPT_CLIENT_VERSION", DEFAULT_CLIENT_VERSION)


def _extra_headers() -> dict[str, str]:
    """Headers Codex sends to the backend. Overridable via env."""
    headers = {
        "OpenAI-Beta": os.environ.get("VULNCLAW_CHATGPT_OPENAI_BETA", "responses=experimental"),
        "originator": os.environ.get("VULNCLAW_CHATGPT_ORIGINATOR", "codex_cli_rs"),
        "User-Agent": os.environ.get("VULNCLAW_CHATGPT_USER_AGENT", "vulnclaw-codex-proxy"),
    }
    return headers


def _backend_headers(token: str, account_id: str) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "session_id": str(uuid.uuid4()),
        **_extra_headers(),
    }
    if account_id:
        headers["chatgpt-account-id"] = account_id
    return headers


def _open_backend(payload: dict[str, Any], token: str, account_id: str):
    request = urllib.request.Request(
        _backend_url(),
        data=json.dumps({**payload, "stream": True, "store": False}).encode(),
        headers=_backend_headers(token, account_id),
        method="POST",
    )
    return urllib.request.urlopen(request, timeout=600)


def list_backend_models(token: str, account_id: str) -> list[str]:
    """Return the model slugs the ChatGPT backend allows for this account."""
    url = f"{_models_url()}?client_version={urllib.parse.quote(_client_version())}"
    headers = _backend_headers(token, account_id)
    headers["Accept"] = "application/json"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError):
        return []
    return [m.get("slug", "") for m in data.get("models", []) if m.get("slug")]


# ═════════════════════════════════════════════════════════════════════
# Local OpenAI-compatible HTTP server
# ═════════════════════════════════════════════════════════════════════


def _resolve_credentials() -> tuple[str, str]:
    """Fetch a fresh ChatGPT token (auto-refreshing) + account id from the store."""
    from vulnclaw.config.settings import load_config
    from vulnclaw.llm.token_provider import load_oauth_tokens, resolve_llm_token

    token = resolve_llm_token(load_config().llm)
    account_id = str(load_oauth_tokens().get("account_id") or "")
    return token, account_id


class _ProxyHandler(BaseHTTPRequestHandler):
    def _send_json(self, obj: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json(
            {"error": {"message": message, "type": "vulnclaw_chatgpt_proxy"}}, status=status
        )

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            slugs: list[str] = []
            try:
                token, account_id = _resolve_credentials()
                slugs = list_backend_models(token, account_id)
            except Exception:  # noqa: BLE001 - models listing is best-effort
                slugs = []
            self._send_json(
                {
                    "object": "list",
                    "data": [{"id": s, "object": "model", "owned_by": "openai"} for s in slugs],
                }
            )
        else:
            self._send_error_json(404, f"not found: {self.path}")

    def do_POST(self):  # noqa: N802
        if self.path.rstrip("/") != "/v1/responses":
            self._send_error_json(404, f"not found: {self.path}")
            return
        headers_sent = False
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("request body must be an object")
            rejected = {"temperature", "top_p", "max_output_tokens"}.intersection(payload)
            if rejected:
                self._send_error_json(
                    400, f"Subscription backend unsupported parameters: {sorted(rejected)}"
                )
                return
            token, account_id = _resolve_credentials()
            with _open_backend(payload, token, account_id) as upstream:
                chunks = iter(lambda: upstream.read1(65536), b"")
                if payload.get("stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    headers_sent = True
                    for chunk in chunks:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    self.close_connection = True
                else:
                    from openai._streaming import SSEDecoder

                    terminal = None
                    for event in SSEDecoder().iter_bytes(chunks):
                        data = event.json()
                        if data.get("type") in {
                            "response.completed",
                            "response.incomplete",
                            "response.failed",
                        }:
                            terminal = data["response"]
                        elif data.get("type") in {"error", "response.error"}:
                            raise ValueError(str(data.get("error") or data.get("message")))
                    if terminal is None:
                        raise ValueError("Backend stream ended without terminal response")
                    self._send_json(terminal)
        except urllib.error.HTTPError as exc:
            self._send_error_json(exc.code, exc.read().decode("utf-8", "replace"))
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as exc:
            if headers_sent:
                self.close_connection = True
            else:
                self._send_error_json(502, f"proxy error: {exc}")

    def log_message(self, *args):  # silence default logging
        pass


_PROXY_LOCK = threading.Lock()
_PROXY_BASE_URL: str | None = None


def ensure_proxy_running() -> str:
    """Start the in-process proxy (once) and return its OpenAI base_url."""
    global _PROXY_BASE_URL
    with _PROXY_LOCK:
        if _PROXY_BASE_URL is not None:
            return _PROXY_BASE_URL
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ProxyHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        _PROXY_BASE_URL = f"http://127.0.0.1:{port}/v1"
        return _PROXY_BASE_URL
