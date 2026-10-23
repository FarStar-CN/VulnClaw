import pytest
from typer.testing import CliRunner


def test_manual_command_renders_roff_man_page():
    from vulnclaw.cli.main import app

    result = CliRunner().invoke(app, ["manual", "run", "--format", "man"])

    assert result.exit_code == 0
    assert ".TH VULNCLAW 1" in result.output
    assert ".SS run" in result.output
    assert ".SH COMMON TASK FLAGS" in result.output
    assert "\\-" in result.output


def test_manual_rejects_invalid_render_format():
    from vulnclaw.cli.manual import render_manual

    with pytest.raises(ValueError, match="text, markdown, man"):
        render_manual("html")

