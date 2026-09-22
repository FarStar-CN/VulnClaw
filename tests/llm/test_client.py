"""Shared LLM client connection defaults."""


def test_openai_default_headers_allow_user_agent_override(monkeypatch):
    from vulnclaw.llm.client import openai_default_headers

    monkeypatch.delenv("VULNCLAW_LLM_USER_AGENT", raising=False)
    assert openai_default_headers()["User-Agent"] == "Mozilla/5.0"

    monkeypatch.setenv("VULNCLAW_LLM_USER_AGENT", "test-agent")

    assert openai_default_headers()["User-Agent"] == "test-agent"
