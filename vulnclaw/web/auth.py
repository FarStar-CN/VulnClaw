"""Token-based authentication for the VulnClaw Web UI.

The token is generated once and persisted to ``~/.vulnclaw/web_token``.
All ``/api/`` routes (except ``/api/health``) require a valid
``Authorization: Bearer <token>`` header.
"""

from __future__ import annotations

import hmac
import secrets
from pathlib import Path

try:
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    _HAS_STARLETTE = True
except ImportError:  # pragma: no cover
    _HAS_STARLETTE = False

TOKEN_DIR = Path.home() / ".vulnclaw"
TOKEN_FILE = TOKEN_DIR / "web_token"


def _token_path() -> Path:
    """Return the token file path, ensuring the parent directory exists."""
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    return TOKEN_FILE


def generate_token() -> str:
    """Return a persisted bearer token.

    If a token file already exists its content is reused.  Otherwise a new
    32-byte URL-safe token is generated, written to disk and returned.
    """
    path = _token_path()
    if path.exists():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing

    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="utf-8")
    # Restrict file permissions on POSIX (best-effort on Windows).
    try:
        import os

        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


def verify_token(token: str) -> bool:
    """Return *True* if *token* matches the stored bearer token.

    Uses :func:`hmac.compare_digest` for timing-safe comparison.
    """
    path = _token_path()
    if not path.exists():
        return False
    stored = path.read_text(encoding="utf-8").strip()
    return hmac.compare_digest(stored, token)


if _HAS_STARLETTE:

    class AuthMiddleware(BaseHTTPMiddleware):  # type: ignore[no-redef]
        """ASGI middleware that enforces bearer-token auth on ``/api/`` routes.

        ``/api/health`` is always exempt so that uptime probes work without
        credentials.
        """

        _EXEMPT_PREFIXES: tuple[str, ...] = ("/api/health",)

        async def dispatch(self, request: Request, call_next):  # type: ignore[override]
            path = request.url.path
            if path.startswith("/api/") and not any(
                path.startswith(p) for p in self._EXEMPT_PREFIXES
            ):
                auth_header = request.headers.get("Authorization", "")
                if not auth_header.startswith("Bearer "):
                    return JSONResponse(
                        {"detail": "Missing or malformed Authorization header"},
                        status_code=401,
                    )
                bearer = auth_header[len("Bearer ") :]
                if not verify_token(bearer):
                    return JSONResponse(
                        {"detail": "Invalid token"},
                        status_code=403,
                    )
            return await call_next(request)

else:  # pragma: no cover

    class AuthMiddleware:  # type: ignore[no-redef]
        """Stub raised when Starlette is not installed."""

        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError(
                "Starlette is not installed. Install the web extra: "
                "pip install vulnclaw[web]"
            )
