"""Readable provider errors shared by interactive and machine clients."""

from collections.abc import Mapping


def format_llm_user_error(exc: BaseException) -> str:
    """Turn provider exceptions into a short message for application clients.

    OpenRouter wraps upstream rate limits as ``Provider returned error`` with the
    useful text buried in ``body['metadata']['raw']`` — surface that instead of
    dumping the whole Error code blob after ``Thinking...``.
    """
    body = getattr(exc, "body", None)
    if isinstance(body, Mapping):
        meta = body.get("metadata")
        if isinstance(meta, Mapping):
            raw = str(meta.get("raw") or "").strip()
            hint = str(meta.get("remedy_hint") or "").strip()
            if raw:
                return f"{raw}" + (f" ({hint})" if hint else "")
        message = str(body.get("message") or "").strip()
        code = body.get("code")
        if message:
            return f"{code}: {message}" if code is not None else message
    text = str(exc).strip()
    return text or type(exc).__name__
