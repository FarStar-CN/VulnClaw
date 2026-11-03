from __future__ import annotations

import errno
import json

import pytest

from vulnclaw.agent.agent_state import AgentState
from vulnclaw.agent.core import AgentCore
from vulnclaw.agent.solver import (
    _completion_gate,
    _implicit_flag_completion,
    _new_evidence_items,
    _solve_impl,
    _visible_evidence_counts,
)
from vulnclaw.config.schema import VulnClawConfig
from vulnclaw.evidence import PersistenceErrorLimitExceeded
from vulnclaw.evidence.models import (
    CapturedObservation,
    ObservationExecution,
    ObservationTool,
    new_evidence_id,
)
from vulnclaw.run_context import create_run_context
from vulnclaw.targets import parse_target


def _agent(tmp_path):
    config = VulnClawConfig()
    config.session.output_dir = tmp_path / "output"
    config.subagent.enabled = False
    agent = AgentCore(config)
    context = create_run_context(
        command="solve",
        targets=[parse_target("https://example.test")],
        runs_dir=tmp_path / "runs",
        run_name="solve-evidence",
    )
    store = agent.bind_run_context(context)
    return agent, store


def _commit(store, agent, text: str, target: str = "https://example.test"):
    evidence_id = new_evidence_id()
    store.commit_observation(
        CapturedObservation(
            evidence_id=evidence_id,
            run_id=store.run_id,
            target=target,
            tool=ObservationTool(name="fetch", arguments={"url": target}),
            execution=ObservationExecution(outcome="succeeded"),
            text=text,
            summary="fetch succeeded",
        ),
        scope=agent.evidence_scope,
    )
    return evidence_id


def test_completion_gate_resolves_persisted_stable_ids(tmp_path):
    agent, store = _agent(tmp_path)
    try:
        state = agent.session_state.agent_state
        state.goal = "capture the flag"
        evidence_id = _commit(store, agent, "response contains flag{real_value}")
        state.remember_observation(
            store.describe(evidence_id, scope=agent.evidence_scope)
        )

        ok, _, cited = _completion_gate(
            state, f"FINAL: captured flag{{real_value}} evidence={evidence_id}"
        )
        assert ok and cited == [evidence_id]

        ok, reason, _ = _completion_gate(
            state, "FINAL: captured flag{real_value} evidence=ev_" + "0" * 32
        )
        assert not ok and "unknown" in reason
    finally:
        agent.release_run_binding()


def test_completion_gate_rejects_claimed_flag_absent_from_persisted_bytes(tmp_path):
    agent, store = _agent(tmp_path)
    try:
        state = agent.session_state.agent_state
        state.goal = "capture the flag"
        evidence_id = _commit(store, agent, "ordinary response")
        state.remember_observation(
            store.describe(evidence_id, scope=agent.evidence_scope)
        )

        ok, reason, _ = _completion_gate(
            state, f"FINAL: flag{{invented}} evidence={evidence_id}"
        )
        assert not ok and "not present" in reason
    finally:
        agent.release_run_binding()


def test_completion_gate_rejects_pending_evidence(tmp_path, monkeypatch):
    agent, store = _agent(tmp_path)
    try:
        state = agent.session_state.agent_state
        state.goal = "capture the flag"

        def fail(path, data):
            raise OSError(errno.ENOSPC, "full")

        monkeypatch.setattr(store, "_write_payload", fail)
        evidence_id = _commit(store, agent, "flag{pending}")
        state.remember_observation(
            store.describe(evidence_id, scope=agent.evidence_scope)
        )

        ok, reason, _ = _completion_gate(
            state, f"FINAL: flag{{pending}} evidence={evidence_id}"
        )
        assert not ok and "not durably saved" in reason
    finally:
        agent.release_run_binding()


def test_completion_gate_without_citation_quotes_persisted_term(tmp_path):
    agent, store = _agent(tmp_path)
    try:
        state = agent.session_state.agent_state
        state.goal = "find injection"
        _commit(store, agent, "unique_marker observed on the endpoint")

        ok, _, cited = _completion_gate(state, "FINAL: unique_marker confirms injection")
        assert ok and cited == []
    finally:
        agent.release_run_binding()


def test_implicit_flag_completion_uses_persisted_bytes(tmp_path):
    agent, store = _agent(tmp_path)
    try:
        state = agent.session_state.agent_state
        state.goal = "capture the flag"
        evidence_id = _commit(store, agent, "leaked flag{implicit_ok}")

        ok, reason, ids = _implicit_flag_completion(state, "we got flag{implicit_ok}")
        assert ok and ids == [evidence_id]
        assert "committed evidence" in reason
    finally:
        agent.release_run_binding()


def test_visible_counts_separate_committed_and_pending(tmp_path, monkeypatch):
    agent, store = _agent(tmp_path)
    try:
        state = agent.session_state.agent_state
        _commit(store, agent, "committed")

        def fail(path, data):
            raise OSError(errno.ENOSPC, "full")

        monkeypatch.setattr(store, "_write_payload", fail)
        _commit(store, agent, "pending")

        committed, pending = _visible_evidence_counts(AgentState())
        assert committed == 0 and pending == 0
        committed, pending = _visible_evidence_counts(state)
        assert committed == 1 and pending == 1
    finally:
        agent.release_run_binding()


def test_format_view_reports_missing_payload_instead_of_falling_back(tmp_path):
    agent, store = _agent(tmp_path)
    try:
        state = agent.session_state.agent_state
        evidence_id = _commit(store, agent, "body")
        state.remember_observation(
            store.describe(evidence_id, scope=agent.evidence_scope)
        )
        record_path = store.run_dir / "evidence" / "records" / f"{evidence_id}.json"
        payload_path = json.loads(record_path.read_text())["payload"]["path"]
        (store.run_dir / payload_path).unlink()

        rendered = state.format_evidence_view(evidence_id)

        assert "unavailable" in rendered
        assert "[empty range]" not in rendered
    finally:
        agent.release_run_binding()


def test_round_results_drive_new_evidence_counts_across_tool_rounds():
    state = AgentState()
    state.begin_round()
    state.record_round_results(["fetch"], ["e001"])
    state.record_round_results(["shell"], ["e002", "e001"])

    assert state.round_tool_names == ["fetch", "shell"]
    assert state.round_evidence_ids == ["e001", "e002"]
    state.evidence = [
        state.remember_tool_result(
            tool="fetch", arguments={"url": f"https://example.test/{index}"},
            output=f"body-{index}", target="https://example.test",
        )
        for index in range(2)
    ]
    # The round result list is authoritative even if a cited id was already
    # present in the bounded cache when the turn started.
    assert [item.id for item in _new_evidence_items(state, {"e001"})] == [
        "e001", "e002",
    ]

    state.begin_round()
    assert state.round_tool_names == []
    assert state.round_evidence_ids == []


@pytest.mark.asyncio
async def test_direct_solve_creates_a_managed_run_binding(tmp_path, monkeypatch):
    config = VulnClawConfig()
    config.session.output_dir = tmp_path / "output"
    config.session.runs_dir = tmp_path / "runs"
    config.subagent.enabled = False
    agent = AgentCore(config)

    async def model(*_args, **_kwargs):
        return "ASK_USER: which host should I use?"

    monkeypatch.setattr("vulnclaw.agent.solver.call_llm_auto", model)
    try:
        result = await agent.solve("check https://example.test", max_steps=1)
        assert result.needs_user is True
        assert agent.run_context is not None
        assert agent.evidence_store is not None
        assert agent.session_state.run_id == agent.run_context.run_id
        assert (agent.run_context.run_dir / "evidence" / "records").is_dir()
        assert agent.run_context.run_dir.is_relative_to((tmp_path / "runs").resolve())
    finally:
        agent.release_run_binding()


@pytest.mark.asyncio
async def test_direct_solve_without_target_is_an_explicit_error(tmp_path, monkeypatch):
    config = VulnClawConfig()
    config.session.output_dir = tmp_path / "output"
    config.session.runs_dir = tmp_path / "runs"
    config.subagent.enabled = False
    agent = AgentCore(config)
    monkeypatch.setattr(
        "vulnclaw.agent.solver.call_llm_auto",
        lambda *_args, **_kwargs: pytest.fail("model must not run"),
    )
    with pytest.raises(ValueError, match="requires a target"):
        await agent.solve("summarize the previous findings", max_steps=1)


@pytest.mark.asyncio
async def test_solver_does_not_convert_persistence_limit_into_repeated_errors(tmp_path, monkeypatch):
    agent, store = _agent(tmp_path)
    try:
        async def boom(*_args, **_kwargs):
            raise PersistenceErrorLimitExceeded(
                failure_total=6, limit=5, stage="record", last_error="synthetic"
            )

        monkeypatch.setattr("vulnclaw.agent.solver.call_llm_auto", boom)
        state = agent.session_state
        state.target = "https://example.test"
        with pytest.raises(PersistenceErrorLimitExceeded):
            await _solve_impl(
                agent, origin="https://example.test", goal="find something", max_steps=1
            )
    finally:
        agent.release_run_binding()
