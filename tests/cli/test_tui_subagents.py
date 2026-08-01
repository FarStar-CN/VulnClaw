import io
import json

import pytest
from rich.console import Console

from vulnclaw.cli._helpers import (
    TUI_EVENT_PREFIX,
    TUI_EVENT_STREAM_ENV,
    TUI_EVENT_TOKEN_ENV,
    _emit_tui_solve_event,
    _make_solve_event_printer,
)
from vulnclaw.cli._helpers import (
    SubagentTuiEvent as _TuiSolveEvent,
)
from vulnclaw.cli.tui import TuiState, _draft_from_state
from vulnclaw.cli.tui_textual import (
    DashboardScreen,
    SubagentMonitor,
    SubagentMonitorState,
    VulnClawApp,
    _parse_tui_solve_event,
)
from vulnclaw.config.schema import VulnClawConfig


def _encoded_event(kind, payload, *, token="test-token"):
    return TUI_EVENT_PREFIX + token + ":" + json.dumps(
        {"kind": kind, "payload": payload},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _session():
    return {
        "config": VulnClawConfig(),
        "state": TuiState(target="https://example.com", mode="standard"),
        "launcher": None,
        "_action": None,
        "_prompt": None,
        "_message": "",
        "_launch": False,
    }


def test_cli_emits_private_group_event_only_when_tui_bridge_enabled(monkeypatch):
    stream = io.StringIO()
    console = Console(file=stream, force_terminal=False, width=40)

    _emit_tui_solve_event(console, "group_started", {"group_id": "g1"})
    assert stream.getvalue() == ""

    monkeypatch.setenv(TUI_EVENT_STREAM_ENV, "1")
    monkeypatch.setenv(TUI_EVENT_TOKEN_ENV, "test-token")
    _emit_tui_solve_event(console, "agent_step", {"step": 1})
    assert stream.getvalue() == ""

    _emit_tui_solve_event(
        console,
        "group_progress",
        {"group_id": "g1", "member_status": "completed"},
    )
    event = _parse_tui_solve_event(
        stream.getvalue(), expected_token="test-token"
    )

    assert event is not None
    assert event.kind == "group_progress"
    assert event.payload["member_status"] == "completed"


def test_parse_tui_event_rejects_normal_or_malformed_lines():
    assert (
        _parse_tui_solve_event(
            "ordinary CLI output\n", expected_token="test-token"
        )
        is None
    )
    assert (
        _parse_tui_solve_event(
            f"{TUI_EVENT_PREFIX}test-token:not-json\n",
            expected_token="test-token",
        )
        is None
    )
    assert (
        _parse_tui_solve_event(
            _encoded_event("agent_step", {"step": 1}),
            expected_token="test-token",
        )
        is None
    )
    assert (
        _parse_tui_solve_event(
            _encoded_event(
                "group_started", {"group_id": "g1"}, token="spoofed"
            ),
            expected_token="test-token",
        )
        is None
    )


def test_subagent_monitor_tracks_group_runtime_for_operator():
    state = SubagentMonitorState()
    assert state.apply(
        "group_created",
        {
            "group_id": "g1",
            "status": "pending",
            "phase": "created",
            "goal": "verify [admin] bypass safely",
            "member_total": 2,
            "member_done": 0,
        },
    )
    assert state.groups["g1"].phase == "created"
    assert state.groups["g1"].status == "pending"

    state.apply(
        "group_progress",
        {
            "group_id": "g1",
            "status": "running",
            "phase": "executing",
            "member_task_id": "a-0002",
            "member_status": "running",
            "wave_count": 1,
        },
    )
    assert state.groups["g1"].phase == "executing"
    assert state.groups["g1"].activity == "member a-0002: running"

    state.apply(
        "group_finished",
        {
            "group_id": "g1",
            "status": "completed",
            "phase": "completed",
            "member_total": 2,
            "member_done": 2,
            "evidence_count": 3,
            "conclusion": "independent verifier confirmed the bypass",
        },
    )
    group = state.groups["g1"]
    assert group.status == "completed"
    assert group.member_done == 2
    rendered = state.render().plain
    assert "[g1] completed · completed" in rendered
    assert "members 2/2" in rendered
    assert "waves 1" in rendered
    assert "evidence 3" in rendered
    assert "independent verifier confirmed" in rendered


def test_group_events_are_printed_to_log_as_safe_operator_progress():
    stream = io.StringIO()
    console = Console(file=stream, force_terminal=False, width=120)
    printer = _make_solve_event_printer(console)

    printer(
        "group_progress",
        {
            "group_id": "g2",
            "status": "executing",
            "phase": "executing",
            "goal": "inspect [/quote] without markup",
            "member_total": 3,
            "member_done": 2,
            "wave_count": 1,
            "member_task_id": "a-0002",
            "member_status": "completed",
        },
    )

    output = stream.getvalue()
    assert "◈ [g2] executing:" in output
    assert "member a-0002=completed" in output
    assert "members 2/3" in output
    assert "waves 1" in output
    printer(
        "group_failed",
        {
            "group_id": "g2",
            "status": "failed",
            "phase": "failed",
            "conclusion": "plan request exhausted retries",
        },
    )

    output = stream.getvalue()
    assert "plan request exhausted retries" in output


@pytest.mark.asyncio
async def test_dashboard_renders_subagent_events_incrementally():
    app = VulnClawApp(_session())
    async with app.run_test() as pilot:
        screen = app.screen
        monitor = screen.query_one(SubagentMonitor)
        assert not monitor.has_class("-active")

        screen._output_queue.put(
            _TuiSolveEvent(
                "group_created",
                {
                    "group_id": "g-live",
                    "goal": "probe live target",
                    "status": "pending",
                    "phase": "created",
                },
            )
        )
        screen._output_queue.put(
            _TuiSolveEvent(
                "group_progress",
                {
                    "group_id": "g-live",
                    "status": "running",
                    "phase": "executing",
                    "member_task_id": "a-0002",
                    "member_status": "running",
                },
            )
        )
        screen._poll_output()
        await pilot.pause()

        assert monitor.has_class("-active")
        group = monitor.state.groups["g-live"]
        assert group.status == "running"
        assert group.activity == "member a-0002: running"


def test_tui_subprocess_enables_event_bridge_and_separates_event_lines(monkeypatch):
    import subprocess

    captured = {}

    class _Process:
        def __init__(self, event_line):
            self.stdout = iter([event_line + "\n", "normal output\n"])

        def wait(self):
            return 0

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        token = kwargs["env"][TUI_EVENT_TOKEN_ENV]
        event_line = _encoded_event(
            "group_started",
            {"group_id": "g1", "status": "running"},
            token=token,
        )
        return _Process(event_line)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    session = _session()
    screen = DashboardScreen(session)
    screen._run_subprocess(
        _draft_from_state(session["state"]),
        None,
    )

    first = screen._output_queue.get_nowait()
    second = screen._output_queue.get_nowait()
    final = screen._output_queue.get_nowait()

    assert isinstance(first, _TuiSolveEvent)
    assert first.kind == "group_started"
    assert second == "normal output\n"
    assert final is None
    assert captured["kwargs"]["env"][TUI_EVENT_STREAM_ENV] == "1"
    assert captured["kwargs"]["env"][TUI_EVENT_TOKEN_ENV]
