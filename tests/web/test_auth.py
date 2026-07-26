"""Tests for the Web UI bearer-token file logic (vulnclaw.web.auth).

The loopback-vs-remote middleware gate is covered in test_web.py
(TestWebAuthLoopback); this file covers token generation/verification, kept off
the real home directory via monkeypatch.
"""

from __future__ import annotations

import vulnclaw.web.auth as auth


def _redirect_token_dir(monkeypatch, tmp_path):
    token_dir = tmp_path / ".vulnclaw"
    monkeypatch.setattr(auth, "TOKEN_DIR", token_dir)
    monkeypatch.setattr(auth, "TOKEN_FILE", token_dir / "web_token")
    return token_dir / "web_token"


class TestTokenFile:
    def test_generate_persists_and_is_reused(self, monkeypatch, tmp_path):
        token_file = _redirect_token_dir(monkeypatch, tmp_path)
        first = auth.generate_token()
        assert first and token_file.is_file()
        assert token_file.read_text(encoding="utf-8").strip() == first
        # A second call reuses the persisted token rather than minting a new one.
        assert auth.generate_token() == first

    def test_verify_token_matches_and_rejects(self, monkeypatch, tmp_path):
        _redirect_token_dir(monkeypatch, tmp_path)
        token = auth.generate_token()
        assert auth.verify_token(token) is True
        assert auth.verify_token("not-the-token") is False

    def test_verify_token_false_when_no_file(self, monkeypatch, tmp_path):
        token_file = _redirect_token_dir(monkeypatch, tmp_path)
        assert not token_file.exists()
        assert auth.verify_token("anything") is False

    def test_client_is_loopback_variants(self):
        assert auth._client_is_loopback("127.0.0.1")
        assert auth._client_is_loopback("::1")
        assert auth._client_is_loopback("localhost")
        assert not auth._client_is_loopback("8.8.8.8")
        assert not auth._client_is_loopback(None)
