import io
import json

import pytest

from tests.cli.test_session_backend import FakeRuntime, request
from vulnclaw.session_backend import BackendSession
from vulnclaw.session_protocol import JsonlWriter, ProtocolError


async def test_report_reader_returns_existing_markdown_without_generating_or_editing(tmp_path):
    path = tmp_path / "report.md"
    text = "# 中文报告\n\n```python\n    print('evidence')\n```\n"
    path.write_text(text, encoding="utf-8")
    stream = io.StringIO()
    backend = BackendSession(JsonlWriter(stream), runtime_factory=FakeRuntime)
    await backend.handle(request("initialize", "init"))
    backend.application.last_run = {"report": {"path": str(path)}}
    await backend.handle(request("control", "read", payload={"operation": "report.read", "arguments": {}}))
    result = json.loads(stream.getvalue().splitlines()[-1])["result"]["report"]
    assert result == {"path": str(path), "text": text, "format": "markdown"}
    assert path.read_text(encoding="utf-8") == text
    await backend.close()


async def test_report_reader_requires_an_available_text_report(tmp_path):
    backend = BackendSession(JsonlWriter(io.StringIO()), runtime_factory=FakeRuntime)
    await backend.handle(request("initialize", "init"))
    read = request("control", "read", payload={"operation": "report.read", "arguments": {}})
    with pytest.raises(ProtocolError, match="No report is available"):
        await backend.handle(read)
    backend.application.last_report = {"path": str(tmp_path / "report.pdf")}
    with pytest.raises(ProtocolError, match="binary artifact"):
        await backend.handle(read)
    await backend.close()
