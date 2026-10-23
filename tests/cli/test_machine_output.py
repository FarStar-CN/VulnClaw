import io
import json

import pytest
import typer
from typer.testing import CliRunner

from vulnclaw.cli.output import HeadlessGroup, current_output, perform


def command_app():
    app = typer.Typer(cls=HeadlessGroup)

    @app.callback()
    def root():
        pass

    @app.command()
    def echo(text: str, output_format: str = typer.Option("json", "--format")):
        def operation():
            print("library diagnostic")
            return {"text": text}

        perform(operation)

    @app.command()
    def finding():
        def operation():
            current_output().finish(exit_code=2, findings={"verified": 1, "candidates": 0})

        perform(operation)

    return app


@pytest.mark.parametrize("format", ["json", "jsonl"])
def test_library_output_goes_to_stderr_and_stdout_has_one_result(format):
    result = CliRunner().invoke(command_app(), ["echo", "中文", "--format", format])
    assert result.exit_code == 0, result.exception
    document = json.loads(result.stdout)
    assert document["type"] == "command_result"
    assert document["result"] == {"text": "中文"}
    assert document["status"] == "completed"
    assert "library diagnostic" in result.stderr
    assert "library diagnostic" not in result.stdout


@pytest.mark.parametrize(("arguments", "status", "code"), [
    (["echo"], "requires_input", "requires_input"),
    (["unknown"], "failed", "invalid_arguments"),
    (["echo", "text", "--unknown"], "failed", "invalid_arguments"),
])
def test_parse_errors_are_machine_results_with_exit_one(arguments, status, code):
    result = CliRunner().invoke(command_app(), arguments)
    assert result.exit_code == 1
    document = json.loads(result.stdout)
    assert document["status"] == status
    assert document["error"]["code"] == code
    assert "\x1b" not in result.stdout


def test_completed_finding_exit_two_is_preserved():
    result = CliRunner().invoke(command_app(), ["finding"])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["status"] == "completed"


def test_help_stays_readable():
    result = CliRunner().invoke(command_app(), ["--help"])
    assert result.exit_code == 0
    assert "Usage:" in result.stdout


def test_output_writer_rejects_duplicate_terminal_results():
    from vulnclaw.cli.output import CommandOutput

    stream = io.StringIO()
    output = CommandOutput(stream=stream)
    output.finish()
    with pytest.raises(RuntimeError, match="already written"):
        output.finish()
    assert len(stream.getvalue().splitlines()) == 1
