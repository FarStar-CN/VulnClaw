"""URL utility functions — shared across infrastructure and domain layers.

修改者: Nyaecho
修改时间: 2026-07-08
修改原因: 消除 V1 违规 — mcp/lifecycle.py 基础设施层不应反向依赖
         agent/builtin_tools.py 领域层，将纯 URL 工具函数抽取到
         基础设施层 config/ 包中。
"""

from __future__ import annotations

from urllib.parse import urlparse, urlsplit


def infer_port_from_url(url: str) -> int | None:
    """Infer request port from URL.

    Returns the explicit port if present in the URL, otherwise infers
    from the scheme (443 for https, 80 for http), or None if unknown.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    if parsed.port:
        return parsed.port
    if parsed.scheme == "https":
        return 443
    if parsed.scheme == "http":
        return 80
    return None


def normalize_base_url(value: str | None) -> str:
    """Validate an OpenAI-compatible HTTP(S) base URL, stripping a trailing slash.

    Mirrors the rule the Web UI applies in ``vulnclaw.web.schemas`` so both
    surfaces accept exactly the same strings; it lives here so the config layer
    keeps no dependency on the optional ``web`` extra.
    """
    if value is None:
        return ""
    normalized = value.strip().rstrip("/")
    if not normalized:
        return ""
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("base_url must use http or https")
    if not parsed.netloc or not parsed.hostname:
        raise ValueError("base_url must include a host")
    if parsed.username or parsed.password:
        raise ValueError("base_url must not include credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("base_url must not include query strings or fragments")
    return normalized
