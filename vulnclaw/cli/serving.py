"""Explicit Web service launcher."""

from __future__ import annotations

import ipaddress
import os
import sys
from urllib.parse import urlsplit

import typer

from vulnclaw.cli.output import CommandError, perform


def _is_loopback_bind_host(host: str) -> bool:
    normalized = host.strip().strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _web_public_url() -> str | None:
    value = os.environ.get("VULNCLAW_WEB_PUBLIC_URL", "").strip().rstrip("/")
    if not value:
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        print(
            "Ignoring invalid VULNCLAW_WEB_PUBLIC_URL; expected an HTTPS origin.", file=sys.stderr
        )
        return None
    return value


def web(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(7788, "--port", min=1, max=65535),
    dry_run: bool = typer.Option(False, "--dry-run"),
    allow_remote: bool = typer.Option(False, "--allow-remote"),
):
    """Run the Web service explicitly; startup diagnostics use stderr."""

    def operation():
        if not _is_loopback_bind_host(host) and not allow_remote:
            raise CommandError(
                "remote_bind_requires_opt_in", "Non-local binding requires --allow-remote."
            )
        from vulnclaw.web.app import FASTAPI_AVAILABLE

        if dry_run:
            return {
                "host": host,
                "port": port,
                "fastapi_available": FASTAPI_AVAILABLE,
                "allow_remote": allow_remote,
            }
        if not FASTAPI_AVAILABLE:
            raise CommandError(
                "missing_dependency", "FastAPI is missing. Install with pip install vulnclaw[web]."
            )
        import uvicorn

        from vulnclaw.web.app import create_app
        from vulnclaw.web.auth import generate_token

        token = generate_token()
        if allow_remote:
            browser_host = "127.0.0.1" if host in {"0.0.0.0", "::", "[::]"} else host
            origin = _web_public_url() or f"http://{browser_host}:{port}"
            print(
                f"Open once to sign in: {origin}/?token={token}\nAPI Authorization: Bearer {token}",
                file=sys.stderr,
            )
        uvicorn.run(create_app(), host=host, port=port, log_level="info")
        return {"status": "stopped"}

    perform(operation)
