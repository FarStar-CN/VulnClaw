"""Session-owned display details; business evidence remains owned by the agent."""

from __future__ import annotations

import codecs
import hashlib
import tempfile
import threading
from typing import Any, BinaryIO

INLINE_BYTES = 16 * 1024
PAGE_BYTES = 16 * 1024
DETAIL_FIELDS = ("code", "body", "response_body", "stdout", "stderr")


class ExecutionDetails:
    """Keep large display strings off the wire until a client requests a page."""

    def __init__(self) -> None:
        self._file: BinaryIO | None = None
        self._locations: dict[str, tuple[int, int]] = {}
        self._lock = threading.Lock()

    def _store(self, text: str) -> dict[str, Any] | None:
        if len(text) <= INLINE_BYTES // 4:
            return None
        raw = text.encode("utf-8")
        if len(raw) <= INLINE_BYTES:
            return None
        identifier = hashlib.sha256(raw).hexdigest()
        if identifier not in self._locations:
            if self._file is None:
                self._file = tempfile.TemporaryFile(prefix="vulnclaw-details-")
            self._file.seek(0, 2)
            start = self._file.tell()
            self._file.write(raw)
            self._locations[identifier] = (start, len(raw))
        return {"id": identifier, "bytes": len(raw), "preview": text[:256]}

    def compact(self, event_type: str, fields: dict[str, Any]) -> dict[str, Any]:
        if event_type not in {"tool_execution", "tool_result"}:
            return fields
        result = dict(fields)
        with self._lock:
            if event_type == "tool_execution":
                refs = {}
                for name in DETAIL_FIELDS:
                    value = fields.get(name)
                    ref = self._store(value) if isinstance(value, str) else None
                    if ref is not None:
                        refs[name] = ref
                        del result[name]
                if refs:
                    result["detail_refs"] = refs
            else:
                value = fields.get("result")
                ref = self._store(value) if isinstance(value, str) else None
                if ref is not None:
                    result["result"] = ref["preview"]
                    result["result_ref"] = ref
        return result

    def read(self, arguments: dict[str, Any]) -> dict[str, Any]:
        identifier = arguments.get("id")
        offset = arguments.get("offset", 0)
        if not isinstance(identifier, str):
            raise ValueError("detail id must be a string")
        if type(offset) is not int or offset < 0:
            raise ValueError("detail offset must be a non-negative integer")
        with self._lock:
            location = self._locations.get(identifier)
            if location is None or self._file is None:
                raise ValueError("execution detail is unavailable in this session")
            start, size = location
            if offset > size:
                raise ValueError("detail offset exceeds its size")
            self._file.seek(start + offset)
            raw = self._file.read(min(PAGE_BYTES, size - offset))
        try:
            text = codecs.getincrementaldecoder("utf-8")().decode(raw, final=offset + len(raw) == size)
        except UnicodeDecodeError as exc:
            raise ValueError("detail offset must be a UTF-8 boundary") from exc
        return {"id": identifier, "offset": offset, "next_offset": offset + len(text.encode("utf-8")),
                "bytes": size, "text": text}

    def clear(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None
            self._locations.clear()
