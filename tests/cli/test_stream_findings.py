"""Versioned streams distinguish registered candidates, completion and required input."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from vulnclaw.cli.main import app
from vulnclaw.findings.intake import submit_finding
from vulnclaw.findings.types import FindingAssessment


@pytest.mark.parametrize("command", ["run", "solve"])
@pytest.mark.parametrize("submit_candidate", [False, True])
def test_stream_completion_uses_registered_candidates(
    headless_runtime, monkeypatch, command, submit_candidate
):
    case = headless_runtime
    case.config.session.solve_auto_report = False
    state = case.agent.session_state
    target = "https://synthetic.test"
    state.target = target
    record = state.agent_state.remember_tool_result(
        tool="fetch", arguments={"url": target}, output="synthetic normal page", target=target
    )
    state.agent_state.mark_complete("Normal page loaded", evidence_ids=[record.id])
    if submit_candidate:
        submit_finding(
            case.agent,
            {
                "title": "Headers require review",
                "description": "Recorded request needs analysis",
                "vuln_type": "security_headers",
                "severity": "Low",
                "endpoint": target,
                "evidence_ids": [record.id],
            },
        )
    monkeypatch.setattr(
        "vulnclaw.findings.verification.assess_finding",
        AsyncMock(
            return_value=FindingAssessment(
                outcome="suspected_low",
                method="evidence_review",
                reason="Independent reproduction required",
                evidence_ids=[record.id],
            )
        ),
    )
    result = CliRunner().invoke(app, [command, target, "--stream", "--no-resume"])
    assert result.exit_code == 0, result.stdout
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert events[-1]["type"] == "command_result"
    findings = events[-1]["result"]["findings"]
    assert len(findings) == int(submit_candidate)
    assert events[-1]["findings"]["verified"] == 0
    if submit_candidate:
        finding = findings[0]
        assert finding["id"] == state.findings[0].finding_id
        assert finding["title"] == "Headers require review"
        assert not finding["verified"]
        assert "submission_id" not in finding
        assert "evidence_snapshots" not in finding
        assert "assessment" not in finding


@pytest.mark.parametrize("command", ["run", "solve"])
def test_progress_and_question_have_explicit_message_lifecycles(headless_runtime, command):
    case = headless_runtime
    case.config.session.show_thinking = True
    continued = []

    async def solve(*_args, **kwargs):
        sink = kwargs["stream_sink"]
        sink.on_status("working")
        sink.on_thinking_token("synthetic reasoning")
        sink.on_content_token("synthetic answer")
        sink.on_tool_call("fetch", '{"url":"https://synthetic.test"}')
        sink.on_tool_result("HTTP/1.1 200\n\nsynthetic page")
        kwargs["on_event"]("ask_user", {"question": "Which authorized scope?"})
        continued.append(True)
        return SimpleNamespace(completed=True)

    case.agent.solve.side_effect = solve
    result = CliRunner().invoke(
        app, [command, "https://synthetic.test", "--no-resume", "--format", "jsonl"]
    )
    assert result.exit_code == 1
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert events[-1]["type"] == "command_result"
    assert events[-1]["status"] == "requires_input"
    assert not continued
    assert case.runtimes[0].stopped
    messages = [event for event in events if event["type"] == "message"]
    assert any(
        event.get("role") == "reasoning" and event.get("text") == "synthetic reasoning"
        for event in messages
    )
    assert any(
        event.get("role") == "assistant" and event.get("text") == "synthetic answer"
        for event in messages
    )
    tool = next(index for index, event in enumerate(events) if event["type"] == "tool_call")
    assert events[tool - 1]["type"] == "message"
    assert events[tool - 1]["operation"] == "end"
    assert any(event.get("status") == "cancelled" for event in messages)
    assert not [event for event in events if event["type"] == "finding"]
