"""Machine tasks retain scan profiles, persistence, audit classification and failure exits."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from vulnclaw.agent.context import PentestPhase, SessionState
from vulnclaw.cli.main import app
from vulnclaw.findings.intake import submit_finding
from vulnclaw.findings.types import FindingAssessment
from vulnclaw.target_state.store import save_target_state


def invoke(args):
    result = CliRunner().invoke(app, args)
    document = json.loads(result.stdout.splitlines()[-1])
    assert document["type"] == "command_result"
    return result, document


@pytest.mark.parametrize("mode", ["quick", "standard", "deep"])
def test_scan_mode_budgets_reach_the_real_shared_engine(headless_runtime, mode):
    from vulnclaw.headless import resolve_scan_profile

    case = headless_runtime
    result, document = invoke(
        ["run", "https://synthetic.test", "--no-resume", "--non-interactive", "--scan-mode", mode]
    )
    assert result.exit_code == 0, document
    profile = resolve_scan_profile(case.config, mode)
    kwargs = case.agent.solve.call_args.kwargs
    assert kwargs["max_steps"] == profile.max_steps
    assert kwargs["max_tool_rounds"] == profile.max_tool_rounds
    summary = json.loads(
        Path(
            next(item["path"] for item in document["artifacts"] if item["kind"] == "summary")
        ).read_text()
    )
    assert summary["scan_mode"] == mode
    assert summary["profile"]["max_parallel"] == profile.max_parallel
    assert case.runtimes[0].stopped
    assert Path(document["result"]["run"]["directory"], "run.json").is_file()
    assert Path(document["result"]["report"]["path"]).is_file()


def test_explicit_budget_and_report_destination_win_over_profile(headless_runtime):
    destination = headless_runtime.root / "custom" / "report.md"
    result, document = invoke(
        [
            "run",
            "https://synthetic.test",
            "--no-resume",
            "--scan-mode",
            "quick",
            "--max-steps",
            "999",
            "--output",
            str(destination),
        ]
    )
    assert result.exit_code == 0, document
    assert headless_runtime.agent.solve.call_args.kwargs["max_steps"] == 999
    assert Path(document["result"]["report"]["path"]) == destination
    assert destination.is_file()


def test_team_fan_out_cap_reaches_the_existing_supervisor(headless_runtime, monkeypatch):
    team = AsyncMock()
    monkeypatch.setattr("vulnclaw.agent.team.run_team_pentest", team)
    result, document = invoke(
        ["run", "synthetic.test", "--no-resume", "--engine", "team", "--scan-mode", "quick"]
    )
    assert result.exit_code == 0, document
    assert team.call_args.kwargs["max_parallel"] == 1
    assert not headless_runtime.agent.solve.called


@pytest.mark.parametrize(
    ("history_verified", "outcome", "fail_on", "expected"),
    [
        (True, "suspected_low", "verified", 0),
        (False, "verified", "verified", 2),
        (False, "suspected_low", "any", 3),
        (False, "suspected_high", "verified", 0),
        (True, "verified", "never", 0),
        (True, "rejected", "any", 0),
    ],
)
def test_exit_code_uses_current_audit_instead_of_historical_claims(
    headless_runtime, monkeypatch, history_verified, outcome, fail_on, expected
):
    case = headless_runtime
    state = case.agent.session_state
    state.target = "https://synthetic.test"
    evidence = state.agent_state.remember_tool_result(
        tool="fetch",
        arguments={"url": state.target},
        output="synthetic observation",
        target=state.target,
    )
    submit_finding(
        case.agent,
        {
            "title": "Synthetic candidate",
            "description": "Recorded observation",
            "vuln_type": "SQLi",
            "severity": "High",
            "endpoint": state.target,
            "evidence_ids": [evidence.id],
        },
    )
    if history_verified:
        state.findings[0].mark_verified("Historical conclusion")
    monkeypatch.setattr(
        "vulnclaw.findings.verification.assess_finding",
        AsyncMock(
            return_value=FindingAssessment(
                outcome=outcome,
                method="poc" if outcome == "verified" else "evidence_review",
                reason="Current assessment",
                evidence_ids=[evidence.id],
            )
        ),
    )
    result, document = invoke(["run", state.target, "--no-resume", "--fail-on", fail_on])
    assert result.exit_code == expected, document
    assert document["status"] == "completed"
    assert document["findings"] == {
        "verified": int(outcome == "verified"),
        "candidates": int(outcome.startswith("suspected")),
    }
    summary = json.loads(
        Path(
            next(item["path"] for item in document["artifacts"] if item["kind"] == "summary")
        ).read_text()
    )
    assert summary["findings"] == {
        **document["findings"],
        "total": sum(document["findings"].values()),
    }
    assert summary["exit_code"] == expected


@pytest.mark.parametrize("resume", [True, False])
def test_task_restores_existing_target_state_only_when_requested(headless_runtime, resume):
    target = "https://synthetic.test"
    saved = SessionState(target=target)
    saved.advance_phase(PentestPhase.RECON)
    saved.add_note("Existing business data")
    save_target_state(target, saved, command="recon")
    observed = []

    async def solve(*args, **kwargs):
        observed.extend(headless_runtime.agent.session_state.notes)
        return SimpleNamespace(completed=False)

    headless_runtime.agent.solve.side_effect = solve
    result, document = invoke(["recon", target, *([] if resume else ["--no-resume"])])
    assert result.exit_code == 0, document
    assert ("Existing business data" in observed) is resume


def test_persistent_alias_keeps_zero_cycles_and_step_callback(headless_runtime, monkeypatch):
    async def one_cycle(*_args, **kwargs):
        await kwargs["cycle_runner"]("synthetic cycle", lambda *_args: None)
        return []

    persistent = AsyncMock(side_effect=one_cycle)
    monkeypatch.setattr(headless_runtime.agent, "persistent_pentest", persistent)
    result, document = invoke(
        [
            "persistent",
            "synthetic.test",
            "--no-resume",
            "--rounds-per-cycle",
            "2",
            "--max-cycles",
            "0",
            "--max-steps",
            "17",
            "--no-report",
        ]
    )
    assert result.exit_code == 0, document
    kwargs = persistent.call_args.kwargs
    assert kwargs["rounds_per_cycle"] == 2
    assert kwargs["max_cycles"] == 0
    assert kwargs["auto_report"] is False
    assert headless_runtime.agent.solve.call_args.kwargs["max_steps"] == 17
    assert callable(kwargs["on_cycle_step"])


def test_runtime_failure_returns_one_and_still_closes_the_session(headless_runtime):
    headless_runtime.agent.solve.side_effect = RuntimeError("synthetic engine failure")
    result, document = invoke(["run", "synthetic.test", "--no-resume", "--format", "jsonl"])
    assert result.exit_code == 1
    assert document["status"] == "failed"
    assert "synthetic engine failure" in document["error"]["message"]
    assert headless_runtime.runtimes[0].stopped


@pytest.mark.parametrize(
    "arguments",
    [
        ["--scan-mode", "invalid"],
        ["--fail-on", "invalid"],
        ["--scope-mode", "invalid"],
        ["--only-port", "0"],
        ["--continuous", "--max-cycles", "-1"],
    ],
)
def test_invalid_task_options_fail_before_runtime_start(headless_runtime, arguments):
    result, document = invoke(["run", "synthetic.test", *arguments])
    assert result.exit_code == 1
    assert document["status"] == "failed"
    assert headless_runtime.runtimes == []


def test_missing_credentials_does_not_start_runtime(headless_runtime):
    headless_runtime.config.llm.api_key = ""
    result, document = invoke(["run", "synthetic.test"])
    assert result.exit_code == 1
    assert document["status"] == "requires_input"
    assert not headless_runtime.runtimes


def test_root_resume_uses_exact_saved_run_and_preserves_its_command(headless_runtime):
    case = headless_runtime
    case.config.session.solve_auto_report = False
    first, initial = invoke(
        ["solve", "https://synthetic.test", "--no-resume", "--run-name", "saved-solve"]
    )
    assert first.exit_code == 0, initial.get("error")
    case.agent.context.state = SessionState()
    resumed, document = invoke(
        ["--resume", "saved-solve", "--runs-dir", str(case.root / "runs"), "--repair"]
    )
    assert resumed.exit_code == 0, document.get("error")
    assert document["result"]["run"]["name"] == "saved-solve"
    assert document["result"]["command"] == "solve"
    assert document["result"]["target"] == "https://synthetic.test"
    assert len(list((case.root / "runs").iterdir())) == 1
    assert "saved-solve" in case.agent.chat.call_args.args[0]
    assert all(runtime.stopped for runtime in case.runtimes)
