"""Launch the shared executable, retaining task defaults without running business work."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
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


@pytest.mark.skipif(sys.platform == "win32", reason="PTY smoke check requires a Unix terminal")
def test_built_terminal_connects_to_current_backend(tmp_path):
    """Exercise the on-disk executable, including its embedded protocol schema."""
    import fcntl
    import pty
    import select
    import signal
    import struct
    import termios

    root = Path(__file__).resolve().parents[2]
    binary = root / "tui/dist/vulnclaw-terminal"
    if not binary.is_file():
        pytest.skip("terminal has not been built; Python headless tests do not require it")
    (tmp_path / "config.yaml").write_text(json.dumps({
        "session": {"language": "en"},
        "mcp": {"servers": {name: {"enabled": False} for name in [
            "fetch", "memory", "chrome-devtools", "burp",
        ]}},
    }))
    env = {
        **{key: value for key, value in os.environ.items() if not key.startswith("VULNCLAW_")},
        "TERM": "xterm-256color", "COLORTERM": "truecolor", "VULNCLAW_LANG": "en",
        "VULNCLAW_CONFIG_DIR": str(tmp_path), "VULNCLAW_HOME": str(tmp_path),
        "VULNCLAW_TERMINAL_BINARY": str(binary),
    }
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    child = subprocess.Popen(
        [sys.executable, "-m", "vulnclaw", "tui"], cwd=root, env=env,
        stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
    )
    os.close(slave)
    output = bytearray()
    connected = False
    keys = list(b"/quit\r")
    next_key = None
    deadline = time.monotonic() + 8
    try:
        while child.poll() is None and time.monotonic() < deadline:
            now = time.monotonic()
            if next_key is not None and now >= next_key and keys:
                os.write(master, bytes([keys.pop(0)]))
                next_key = now + 0.05
            if not select.select([master], [], [], 0.02)[0]:
                continue
            try:
                output.extend(os.read(master, 65536))
            except OSError:
                break
            if not connected and b"provider:" in output:
                connected = True
                next_key = time.monotonic() + 0.1
        assert connected, (
            "Built terminal did not accept the backend handshake: "
            + output[-1200:].decode("utf-8", errors="replace")
        )
        assert child.wait(timeout=2) == 0
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        os.close(master)
