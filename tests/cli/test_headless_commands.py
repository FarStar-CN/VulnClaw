"""Exercise public adapters without a TTY, LLM or external target."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vulnclaw.cli.main import app
from vulnclaw.config.schema import VulnClawConfig


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    import vulnclaw.config.settings as settings

    config = VulnClawConfig()
    config.llm.api_key = "synthetic-secret"
    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "config.yaml")
    monkeypatch.setattr(settings, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr("vulnclaw.cli.tasks.load_config", lambda: config)
    monkeypatch.setattr("vulnclaw.cli.management.load_config", lambda: config)
    return config


def invoke(args, **kwargs):
    result = CliRunner().invoke(app, args, **kwargs)
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert lines[-1]["type"] == "command_result"
    assert sum(line["type"] == "command_result" for line in lines) == 1
    assert "\x1b" not in result.stdout
    return result, lines[-1]


@pytest.mark.parametrize(
    "args",
    [
        ["run"],
        ["solve"],
        ["ask"],
        ["login"],
        ["feedback", "run"],
        ["verify"],
        ["report"],
        ["target-state", "preview"],
    ],
)
def test_missing_input_is_structured_and_terminates(args, isolated):
    result, document = invoke(args)
    assert result.exit_code == 1
    assert document["status"] == "requires_input"


@pytest.mark.parametrize(
    "command", ["run", "recon", "scan", "exploit", "persistent", "solve", "network-scan", "ask"]
)
def test_task_commands_reject_invalid_machine_format(command, isolated):
    args = [command, "synthetic.test", "--format", "sarif"]
    result, document = invoke(args)
    assert result.exit_code == 1
    assert document["status"] == "failed"


@pytest.mark.parametrize("entry", ["run", "recon", "scan", "exploit", "persistent", "solve"])
def test_task_adapters_pass_typed_request_to_shared_executor(entry, isolated, monkeypatch):
    from vulnclaw.cli.output import current_output

    captured = {}

    async def execute(self, request, **kwargs):
        captured.update(request=request, **kwargs)
        return current_output().finish(result={"accepted": True})

    monkeypatch.setattr("vulnclaw.cli.tasks.HeadlessExecutor.execute", execute)
    result, document = invoke(
        [entry, "https://synthetic.test", "--only-port", "443", "--no-resume"]
    )
    assert result.exit_code == 0, document
    task = captured["request"].task
    assert task.command == "run"
    assert task.target == "https://synthetic.test"
    assert task.options.only_port == 443
    assert task.resume is False
    assert task.options.continuous is (entry == "persistent")
    if entry == "solve":
        assert task.options.max_steps == 240
        assert captured["direct"] is not None
    if entry in {"recon", "scan", "exploit"}:
        assert task.options.allow_actions


@pytest.mark.parametrize(
    "args,payload",
    [
        (["run", "synthetic.test"], {"target": "synthetic.test"}),
        (
            ["run", "--only-port", "443"],
            {"target": "synthetic.test", "options": {"only_port": 443}},
        ),
        (["run", "--no-resume"], {"target": "synthetic.test", "resume": False}),
        (
            ["run", "--output", "report.md"],
            {"target": "synthetic.test", "options": {"report_output": "report.md"}},
        ),
        (["ask", "same"], {"text": "same"}),
    ],
)
def test_cli_and_json_conflict_even_when_values_match(args, payload, isolated):
    result, document = invoke([*args, "--input-json", "-"], input=json.dumps(payload))
    assert result.exit_code == 1
    assert document["error"]["code"] == "conflicting_input"


@pytest.mark.parametrize("input", ["[]", "{", '{"target":"test", "options":{"unrecognized":true}}'])
def test_task_json_is_validated(input, isolated):
    result, document = invoke(["run", "--input-json", "-", "--format", "jsonl"], input=input)
    assert result.exit_code == 1
    assert document["status"] == "failed"


def test_json_options_and_report_destination_are_preserved(isolated, monkeypatch):
    from vulnclaw.cli.output import current_output

    async def execute(self, request, **kwargs):
        task = request.task
        assert task.options.max_steps == 53
        assert task.options.report_output == "指定报告.md"
        assert task.options.allowed_ports == [80, 443]
        return current_output().finish(result={"accepted": True})

    monkeypatch.setattr("vulnclaw.cli.tasks.HeadlessExecutor.execute", execute)
    result, _ = invoke(
        ["run", "--input-json", "-", "--non-interactive"],
        input=json.dumps(
            {
                "target": "test",
                "options": {
                    "max_steps": 53,
                    "report_output": "指定报告.md",
                    "allowed_ports": [80, 443],
                },
            }
        ),
    )
    assert result.exit_code == 0


def test_no_args_discovers_commands_without_starting_a_terminal():
    result = CliRunner().invoke(app, [])
    assert result.exit_code == 0
    document = json.loads(result.stdout)
    assert document["result"]["kind"] == "command_group"
    assert "repl" in {item["name"] for item in document["result"]["operations"]}


def test_configuration_is_masked_and_invalid_change_is_not_saved(isolated, tmp_path):
    result, document = invoke(["config", "list", "--format", "jsonl"])
    assert result.exit_code == 0
    assert "synthetic-secret" not in result.stdout
    assert document["result"]["config"]["llm"]["api_key"] == "***"
    result, _ = invoke(["config", "set", "llm.max_tokens", "invalid"])
    assert result.exit_code == 1
    assert not (tmp_path / "config.yaml").exists()


def test_login_requires_explicit_credentials_without_browser(isolated, monkeypatch, tmp_path):
    monkeypatch.setenv("VULNCLAW_CONFIG_DIR", str(tmp_path))
    result, _ = invoke(
        ["login", "--token-file", "-"],
        input='{"access_token":"synthetic-oauth-token","flow":"chatgpt"}',
    )
    assert result.exit_code == 0
    assert "synthetic-oauth-token" not in result.stdout
    assert (
        json.loads((tmp_path / "oauth_tokens.json").read_text())["access_token"]
        == "synthetic-oauth-token"
    )
    result, _ = invoke(["logout"])
    assert result.exit_code == 0
    assert not (tmp_path / "oauth_tokens.json").exists()


@pytest.mark.parametrize("format", ["text", "json", "sarif", "markdown"])
@pytest.mark.parametrize("flags", [["--output-format", "jsonl"], ["--stream"]])
def test_code_artifact_format_is_separate_from_jsonl_envelope(format, flags, isolated, tmp_path):
    source = tmp_path / "sample.py"
    source.write_text('eval(input("synthetic"))\n')
    artifact = tmp_path / f"result.{format}"
    result, document = invoke(
        [
            "code",
            "scan",
            str(source),
            "--format",
            format,
            "--output",
            str(artifact),
            *flags,
            "--fail-on",
            "any",
        ]
    )
    assert result.exit_code == 3, document
    assert len(result.stdout.splitlines()) > 1
    assert json.loads(result.stdout.splitlines()[0])["type"] == "status"
    assert document["findings"]["verified"] == 0
    assert document["findings"]["candidates"] > 0
    assert Path(document["artifacts"][0]["path"]) == artifact
    if format in {"json", "sarif"}:
        assert isinstance(json.loads(artifact.read_text()), dict)


@pytest.mark.parametrize("command", ["verify", "report"])
def test_audit_and_report_abort_for_approval_without_starting_a_runtime(
    command, isolated, monkeypatch
):
    from vulnclaw.agent.exec_gate import GateRequest, get_execution_gate, reset_execution_gate

    reset_execution_gate()
    isolated.safety.permission_mode = "ask"
    monkeypatch.setattr("vulnclaw.cli.reports.load_config", lambda: isolated)
    continued = []

    async def needs_approval(*args, **kwargs):
        await get_execution_gate().authorize(GateRequest("shell", "echo synthetic"))
        continued.append(True)

    path = (
        "vulnclaw.finding_service.verify_session_file"
        if command == "verify"
        else "vulnclaw.report_service.generate_report_from_file"
    )
    monkeypatch.setattr(path, needs_approval)
    try:
        result, document = invoke([command, "synthetic.json", "--format", "jsonl"])
        assert result.exit_code == 1
        assert document["status"] == "requires_approval"
        assert not continued
        assert get_execution_gate().mode == "ask"
        assert get_execution_gate().channel is None
        assert not get_execution_gate()._pending_by_hash
    finally:
        reset_execution_gate()


def test_natural_language_missing_credentials_requires_input_before_runtime(isolated, monkeypatch):
    isolated.llm.api_key = ""
    monkeypatch.setattr(
        "vulnclaw.application.create_runtime", lambda: pytest.fail("runtime should not start")
    )
    result, document = invoke(["ask", "Inspect the authorized synthetic target"])
    assert result.exit_code == 1
    assert document["status"] == "requires_input"
