"""Clipboard + paste wiring for the classic-REPL config panel."""

from __future__ import annotations

import base64
from types import SimpleNamespace

from vulnclaw.cli import tui as tui_mod


def test_read_system_clipboard_windows_decodes_base64_stdout(monkeypatch, tmp_path):
    """Windows path pipes base64 through stdout — no temp file is ever created."""
    monkeypatch.setattr(tui_mod.sys, "platform", "win32")

    def fake_run(cmd, **kwargs):
        assert cmd[0] == "powershell.exe"
        payload = base64.b64encode("sk-pasted-key".encode("utf-8"))
        return SimpleNamespace(returncode=0, stdout=payload, stderr=b"")

    monkeypatch.setattr(tui_mod.subprocess, "run", fake_run)

    assert tui_mod._read_system_clipboard() == "sk-pasted-key"
    # The base64 route never writes a paste artifact anywhere.
    assert not list(tmp_path.glob("vulnclaw-paste-*"))


def test_read_system_clipboard_windows_handles_non_ascii(monkeypatch):
    monkeypatch.setattr(tui_mod.sys, "platform", "win32")

    def fake_run(cmd, **kwargs):
        payload = base64.b64encode("密钥-ключ-🔑".encode("utf-8"))
        return SimpleNamespace(returncode=0, stdout=payload, stderr=b"")

    monkeypatch.setattr(tui_mod.subprocess, "run", fake_run)

    assert tui_mod._read_system_clipboard() == "密钥-ключ-🔑"


def test_read_system_clipboard_windows_returns_none_on_failure(monkeypatch):
    monkeypatch.setattr(tui_mod.sys, "platform", "win32")
    monkeypatch.setattr(
        tui_mod.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout=b"", stderr=b"boom"),
    )

    assert tui_mod._read_system_clipboard() is None


def test_read_system_clipboard_windows_returns_none_on_bad_base64(monkeypatch):
    monkeypatch.setattr(tui_mod.sys, "platform", "win32")
    monkeypatch.setattr(
        tui_mod.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"not-base64!!!", stderr=b""),
    )

    assert tui_mod._read_system_clipboard() is None


def test_read_system_clipboard_unix_uses_first_working_helper(monkeypatch):
    monkeypatch.setattr(tui_mod.sys, "platform", "linux")
    calls: list[tuple[str, ...]] = []

    def fake_run(cmd, **kwargs):
        calls.append(tuple(cmd))
        if cmd[0] == "pbpaste":
            raise FileNotFoundError("no pbpaste")
        if cmd[0] == "wl-paste":
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"")
        if cmd[0] == "xclip":
            return SimpleNamespace(returncode=0, stdout=b"from-xclip", stderr=b"")
        return SimpleNamespace(returncode=1, stdout=b"", stderr=b"")

    monkeypatch.setattr(tui_mod.subprocess, "run", fake_run)

    assert tui_mod._read_system_clipboard() == "from-xclip"
    assert calls[0][0] == "pbpaste"
    assert any(c[0] == "xclip" for c in calls)
