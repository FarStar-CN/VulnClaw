"""Launch the shared executable, retaining task defaults without running business work."""

import json
import sys
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from vulnclaw.cli.main import app
from vulnclaw.config.domain_models import ACTION_PRESETS, TEST_ACTIONS
from vulnclaw.config.schema import VulnClawConfig
from vulnclaw.task_preview import preview_task


@pytest.mark.parametrize("mode,preset,command", [
    ("quick", "Quick", "recon"), ("standard", "Standard", "run"),
    ("deep", "Deep", "run"), ("continuous", None, "persistent"),
])
def test_startup_draft_uses_shared_presets(mode, preset, command):
    result = CliRunner().invoke(app, ["tui", "--mode", mode, "--target", "lab.test", "--dry-run"])
    assert result.exit_code == 0, result.output
    bootstrap = json.loads(result.stdout)["result"]["bootstrap"]
    assert bootstrap["command"] == command
    config = VulnClawConfig()
    config.session.poc_verify_enabled = False
    config.session.persistent_rounds_per_cycle = 37
    config.session.persistent_max_cycles = 0
    preview = preview_task({"text": f"/{command} lab.test"}, defaults=bootstrap, config=config)
    assert preview["errors"] == {}
    assert preview["actions"] == list(ACTION_PRESETS[preset] if preset else TEST_ACTIONS)
    assert preview["task"]["options"].get("continuous", False) is (preset is None)
    if preset is None:
        assert preview["task"]["options"]["max_cycles"] == 0
        assert preview["task"]["options"]["rounds_per_cycle"] == 37


@pytest.fixture
def terminal_tty(monkeypatch):
    from vulnclaw.cli import terminal

    stream = SimpleNamespace(isatty=lambda: True)
    monkeypatch.setattr(terminal, "sys", SimpleNamespace(
        stdin=stream, stdout=stream, executable=sys.executable,
    ))


@pytest.mark.parametrize("shell", ["tui", "repl"])
def test_launcher_passes_python_and_bootstrap_to_same_executable(shell, monkeypatch, terminal_tty):
    from vulnclaw.cli import terminal

    captured = {}
    def spawn(argv, *, env):
        captured.update(argv=argv, env=env)
        return SimpleNamespace(wait=lambda: 0)
    monkeypatch.setenv("VULNCLAW_TERMINAL_BINARY", "custom-terminal")
    monkeypatch.setattr(terminal.subprocess, "Popen", spawn)
    result = CliRunner().invoke(app, [shell])
    assert result.exit_code == 0
    assert captured["argv"] == ["custom-terminal", "--mode", shell]
    assert captured["env"]["VULNCLAW_PYTHON"] == sys.executable
    bootstrap = json.loads(captured["env"]["VULNCLAW_TERMINAL_BOOTSTRAP"])
    assert bootstrap.get("mode", "standard") == "standard"


def test_interactive_launcher_requires_a_terminal():
    result = CliRunner().invoke(app, ["repl"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "terminal_required"


def test_interrupted_launcher_terminates_then_reaps_child(monkeypatch, terminal_tty):
    from vulnclaw.cli import terminal

    events = []
    class Child:
        def wait(self, timeout=None):
            events.append(("wait", timeout))
            if timeout is None:
                raise KeyboardInterrupt
            raise terminal.subprocess.TimeoutExpired("terminal", timeout)
        def terminate(self): events.append("terminate")
        def kill(self):
            events.append("kill")
            self.wait = lambda: events.append("reaped")
    monkeypatch.setenv("VULNCLAW_TERMINAL_BINARY", "custom-terminal")
    monkeypatch.setattr(terminal.subprocess, "Popen", lambda *a, **kw: Child())
    result = CliRunner().invoke(app, ["repl"])
    assert result.exit_code == 1
    assert events == [("wait", None), "terminate", ("wait", 5), "kill", "reaped"]
