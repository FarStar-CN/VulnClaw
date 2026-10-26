"""Terminal startup leaves optional retrieval engines cold until first use."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from vulnclaw.agent.core import AgentCore
from vulnclaw.agent.kb_context import _retriever_for
from vulnclaw.config.schema import VulnClawConfig


@pytest.mark.parametrize("module", ["vulnclaw.cli.main", "vulnclaw.session_backend"])
def test_startup_imports_do_not_load_retrieval_engines(module):
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}, sys, json; "
         "print(json.dumps([name for name in sys.modules if "
         "name == 'vulnclaw.kb.retriever' or name.startswith('chromadb')]))"],
        capture_output=True, text=True, check=True,
    )
    assert json.loads(result.stdout) == []


def test_concurrent_first_use_shares_one_retriever(tmp_path, monkeypatch):
    import vulnclaw.kb.retriever as retrieval

    entered, release = Event(), Event()
    retriever = SimpleNamespace(get_status=lambda: retrieval.RetrieverStatus.KEYWORD_FALLBACK)

    def create():
        entered.set()
        assert release.wait(5)
        return retriever

    factory = Mock(side_effect=create)
    monkeypatch.setattr(retrieval, "KnowledgeRetriever", factory)
    config = VulnClawConfig()
    config.session.output_dir = tmp_path
    agent = AgentCore(config)
    report = Mock()
    monkeypatch.setattr(agent, "_report_kb_status", report)
    factory.assert_not_called()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_retriever_for, agent)
        try:
            assert entered.wait(5)
            second = pool.submit(_retriever_for, agent)
        finally:
            release.set()
        assert first.result() is retriever
        assert second.result() is retriever
    factory.assert_called_once()
    report.assert_called_once()


def test_first_use_keeps_empty_library_seeding_and_does_not_retry_failed_initialization(tmp_path, monkeypatch):
    import vulnclaw.kb.retriever as retrieval
    import vulnclaw.kb.updater as updater

    config = VulnClawConfig()
    config.session.output_dir = tmp_path
    agent = AgentCore(config)
    store = object()
    empty = SimpleNamespace(get_status=lambda: retrieval.RetrieverStatus.DISABLED, store=store)
    seeded = SimpleNamespace(get_status=lambda: retrieval.RetrieverStatus.KEYWORD_FALLBACK)
    factory = Mock(side_effect=[empty, seeded])
    seed = Mock()
    monkeypatch.setattr(retrieval, "KnowledgeRetriever", factory)
    monkeypatch.setattr(updater, "seed_knowledge_base", seed)
    monkeypatch.setattr(agent, "_report_kb_status", Mock())
    assert _retriever_for(agent) is seeded
    assert _retriever_for(agent) is seeded
    seed.assert_called_once_with(store)
    assert factory.call_count == 2

    broken = AgentCore(config)
    factory = Mock(side_effect=RuntimeError("unavailable"))
    monkeypatch.setattr(retrieval, "KnowledgeRetriever", factory)
    assert _retriever_for(broken) is None
    assert _retriever_for(broken) is None
    factory.assert_called_once()
