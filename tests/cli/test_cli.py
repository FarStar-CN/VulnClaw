"""Public entry points and preserved shared management behaviors."""

import json

import pytest
from typer.testing import CliRunner


@pytest.fixture
def runner():
    return CliRunner()


def test_cli_help(runner):
    from vulnclaw.cli.main import app

    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "VulnClaw" in result.output or "vulnclaw" in result.output.lower()
    assert "TUI" in result.output


def test_cli_version(runner):
    from vulnclaw import __version__
    from vulnclaw.cli.main import app

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_cli_manual_command(runner):
    from vulnclaw.cli.main import app

    result = runner.invoke(app, ["manual"])

    assert result.exit_code == 0
    assert "VULNCLAW(1)" in result.output
    assert "COMMON TASK FLAGS" in result.output
    assert "--only-port" in result.output
    assert "network-scan" in result.output
    assert "--parallel-agents" in result.output


def test_cli_manual_topic_markdown(runner):
    from vulnclaw.cli.main import app

    result = runner.invoke(app, ["manual", "network-scan", "--format", "markdown"])

    assert result.exit_code == 0
    assert "### `network-scan`" in result.output
    assert "`--safe-probes / --no-safe-probes`" in result.output
    assert "### `run`" not in result.output


def test_cli_man_alias_and_root_flag(runner):
    from vulnclaw.cli.main import app

    alias_result = runner.invoke(app, ["man", "config"])
    root_result = runner.invoke(app, ["--man"])

    assert alias_result.exit_code == 0
    assert "CONFIG" in alias_result.output
    assert "llm.api_keys" in alias_result.output
    assert root_result.exit_code == 0
    assert "VULNCLAW(1)" in root_result.output


def test_cli_manual_rejects_unknown_topic(runner):
    from vulnclaw.cli.main import app

    result = runner.invoke(app, ["manual", "does-not-exist"])

    assert result.exit_code == 1
    assert "unknown manual topic" in result.output


def test_cli_config_list(runner):
    from vulnclaw.cli.main import app

    result = runner.invoke(app, ["config", "list"])
    # Should not crash
    assert result.exit_code == 0


def test_cli_config_provider_list(runner):
    from vulnclaw.cli.main import app

    result = runner.invoke(app, ["config", "provider", "--list"])
    # Should show available providers
    assert result.exit_code == 0


def test_cli_config_provider_set(runner):
    from vulnclaw.cli.main import app

    result = runner.invoke(app, ["config", "provider", "deepseek"])
    # Should not crash
    assert result.exit_code == 0


def test_target_state_list_and_clear(runner, monkeypatch, tmp_path):
    import vulnclaw.target_state.store as store_mod
    from vulnclaw.agent.context import SessionState
    from vulnclaw.cli.main import app

    monkeypatch.setattr(store_mod, "TARGETS_DIR", tmp_path / "targets")
    state = SessionState(target="https://example.com")
    store_mod.save_target_state("https://example.com", state, command="recon")

    result_list = runner.invoke(app, ["target-state", "list", "https://example.com"])
    assert result_list.exit_code == 0
    assert "snapshot" in result_list.output.lower() or "蹇収" in result_list.output

    result_clear = runner.invoke(app, ["target-state", "clear", "https://example.com"])
    assert result_clear.exit_code == 0
    assert result_clear.output


def test_target_state_preview_and_diff(runner, monkeypatch, tmp_path):
    import vulnclaw.target_state.store as store_mod
    from vulnclaw.agent.context import SessionState, VulnerabilityFinding
    from vulnclaw.cli.main import app

    monkeypatch.setattr(store_mod, "TARGETS_DIR", tmp_path / "targets")

    state1 = SessionState(target="https://example.com")
    state1.add_finding(VulnerabilityFinding(title="SQLi", severity="High", vuln_type="SQLi"))
    store_mod.save_target_state("https://example.com", state1, command="recon")

    state2 = SessionState(target="https://example.com")
    state2.add_finding(VulnerabilityFinding(title="XSS", severity="Medium", vuln_type="XSS"))
    store_mod.save_target_state("https://example.com", state2, command="scan")

    snapshots = store_mod.list_target_snapshots("https://example.com")
    result_preview = runner.invoke(app, ["target-state", "preview", "https://example.com"])
    assert result_preview.exit_code == 0
    assert json.loads(result_preview.stdout)["result"]["snapshot_id"] == snapshots[0]["snapshot_id"]

    result_diff = runner.invoke(
        app,
        [
            "target-state",
            "diff",
            "https://example.com",
            snapshots[-1]["snapshot_id"],
            "--to",
            snapshots[0]["snapshot_id"],
        ],
    )
    assert result_diff.exit_code == 0
    assert json.loads(result_diff.stdout)["result"]["to_snapshot_id"] == snapshots[0]["snapshot_id"]


def test_config_command_dispatches():
    from vulnclaw.input_service import InputRequest, parse_input

    result = parse_input(InputRequest(text="/config"))

    assert result.kind == "adapter"
    assert result.operation == "config"
    assert result.text == ""


def test_config_alias_dispatches():
    from vulnclaw.input_service import InputRequest, parse_input

    result = parse_input(InputRequest(text="/cfg"))

    assert result.kind == "adapter"
    assert result.operation == "config"


def test_language_command_carries_argument():
    from vulnclaw.input_service import InputRequest, parse_input

    result = parse_input(InputRequest(text="/language en"))

    assert result.kind == "adapter"
    assert result.operation == "language"
    assert result.text == "en"


def test_language_alias_dispatches():
    from vulnclaw.input_service import InputRequest, parse_input

    result = parse_input(InputRequest(text="/lang"))

    assert result.kind == "adapter"
    assert result.operation == "language"


def test_experience_command_dispatches_without_args():
    from vulnclaw.input_service import InputRequest, parse_input

    result = parse_input(InputRequest(text="/experience"))

    assert result.kind == "adapter"
    assert result.operation == "experience"
    assert result.text == ""


def test_experience_alias_and_subcommand_carry_args():
    from vulnclaw.input_service import InputRequest, parse_input

    result = parse_input(InputRequest(text="/exp show L1"))

    assert result.kind == "adapter"
    assert result.operation == "experience"
    assert result.text == "show L1"


def test_learn_command_carries_run_name():
    from vulnclaw.input_service import InputRequest, parse_input

    result = parse_input(InputRequest(text="/learn my-run"))

    assert result.kind == "adapter"
    assert result.operation == "learn"
    assert result.text == "my-run"


def test_feedback_command_carries_full_argument():
    from vulnclaw.input_service import InputRequest, parse_input

    result = parse_input(InputRequest(text="/feedback my-run 4 solid recon coverage"))

    assert result.kind == "adapter"
    assert result.operation == "feedback"
    assert result.text == "my-run 4 solid recon coverage"


def test_experience_show_rejects_unsafe_lesson_id():
    from vulnclaw import experience_service as experience_ops

    result = experience_ops.get_lesson("../../foo")

    assert result.ok is False
    assert "Lesson not found" in str(result.message)


def test_experience_status_survives_unwritable_store(monkeypatch):
    from vulnclaw import experience_service as experience_ops

    class _Boom:
        def approve(self, lesson_id):
            raise OSError("read-only file system")

        def reject(self, lesson_id):
            raise OSError("read-only file system")

    monkeypatch.setattr(experience_ops, "_experience_store", lambda: _Boom())

    result = experience_ops.set_lesson_status("L1", "approved")

    assert result.ok is False
    assert "Could not write the experience store" in str(result.message)


def test_experience_edit_survives_unwritable_store(monkeypatch):
    from vulnclaw import experience_service as experience_ops

    class _Boom:
        def update(self, lesson_id, *, context=None, lesson=None):
            raise OSError("no space left on device")

    monkeypatch.setattr(experience_ops, "_experience_store", lambda: _Boom())

    result = experience_ops.edit_lesson("L1", lesson="new")

    assert result.ok is False
    assert "Could not write the experience store" in str(result.message)


def test_feedback_survives_unwritable_run_dir(monkeypatch, tmp_path):
    from vulnclaw import experience_service as experience_ops
    from vulnclaw.config.schema import VulnClawConfig

    class _RunContext:
        manifest = {"status": "completed"}
        run_dir = tmp_path

        def append_event(*args, **kwargs):
            raise AssertionError("should not be reached")

    monkeypatch.setattr(
        "vulnclaw.run_context.load_run_context",
        lambda *args, **kwargs: _RunContext(),
    )

    def _boom(run_dir, *, rating, notes):
        raise OSError("permission denied")

    monkeypatch.setattr("vulnclaw.feedback.save_feedback", _boom)

    result = experience_ops.save_run_feedback(
        "run-1", rating=4, notes="solid", config=VulnClawConfig()
    )

    assert result.ok is False
    assert "Could not save feedback" in str(result.message)


async def test_agent_apply_config_resets_client():
    from vulnclaw.agent.core import AgentCore
    from vulnclaw.config.settings import load_config

    config = load_config()
    agent = AgentCore.__new__(AgentCore)
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    agent._client = SimpleNamespace(close=AsyncMock())
    agent._key_index = 3

    await agent.apply_config(config)

    assert agent.config is config
    assert agent._client is None
    assert agent._key_index == 0

