"""Actionable validation feedback and the saved configuration summary."""

import json
import os

import pytest
import yaml
from typer.testing import CliRunner

from vulnclaw.cli.main import app
from vulnclaw.config.schema import VulnClawConfig


@pytest.fixture
def configuration(monkeypatch, tmp_path):
    import vulnclaw.config.settings as settings

    for name in list(os.environ):
        if name.startswith("VULNCLAW_LLM_"):
            monkeypatch.delenv(name)
    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "config.yaml")
    config = VulnClawConfig()
    config.llm.api_key = ""
    config.llm.api_keys = []
    config.llm.auth_mode = "static"
    settings.save_config(config)
    return settings


def invoke(args, **kwargs):
    response = CliRunner().invoke(app, args, **kwargs)
    events = [json.loads(line) for line in response.stdout.splitlines()]
    assert len([e for e in events if e["type"] == "command_result"]) == 1
    final = events[-1]
    assert response.exit_code == final["exit_code"]
    return response, final


@pytest.mark.parametrize(
    "args,path,code",
    [
        (["run"], ["target"], "missing_field"),
        (["config", "set"], ["key"], "missing_field"),
        (["feedback", "run", "--rating", "9"], ["rating"], "invalid_arguments"),
        (["run", "test", "--max-steps", "abc"], ["max_steps"], "invalid_arguments"),
        (["run", "test", "--scan-mode", "ultra"], ["scan_mode"], "invalid_arguments"),
        (["config", "get", "llm.modle"], ["llm", "modle"], "unknown_config_key"),
        (["config", "unknown"], ["command"], "invalid_arguments"),
    ],
)
def test_failures_have_field_location_and_discovery_action(configuration, args, path, code):
    response, final = invoke(args)
    assert response.exit_code == 1
    feedback = final["result"]["feedback"]
    assert feedback["issues"][0]["path"] == path
    assert feedback["issues"][0]["code"] == code
    assert feedback["next_actions"]
    if args == ["run"]:
        assert feedback["next_actions"][0]["example_argv"][1] == "run"
    if "llm.modle" in args:
        assert "llm.model" in feedback["issues"][0]["constraints"]["choices"]


@pytest.mark.parametrize(
    "input,code,path",
    [
        ("{", "invalid_json", ["input_json"]),
        ("[]", "invalid_json", ["input_json"]),
        (
            '{"target":"test","options":{"max_steps":"abc"}}',
            "invalid_value",
            ["options", "max_steps"],
        ),
    ],
)
def test_json_validation_uses_structured_errors(configuration, input, code, path):
    _, final = invoke(["run", "--input-json", "-"], input=input)
    issue = final["result"]["feedback"]["issues"][0]
    assert issue["code"] == code
    assert issue["path"] == path
    if input == "{":
        assert issue["constraints"]["line"] == 1


def test_conflict_lists_both_input_sources(configuration):
    _, final = invoke(["run", "test", "--input-json", "-"], input='{"target":"test"}')
    issue = final["result"]["feedback"]["issues"][0]
    assert issue["path"] == ["target"]
    assert issue["constraints"]["sources"] == ["cli", "input_json"]


@pytest.mark.parametrize("args", [["run", "test"], ["doctor", "--probe-llm"]])
def test_missing_credentials_provides_noninteractive_setup(configuration, args):
    _, final = invoke(args)
    feedback = final["result"]["feedback"]
    assert feedback["issues"][0]["code"] == "missing_credentials"
    assert feedback["next_actions"][0]["environment_variables"] == ["VULNCLAW_LLM_API_KEY"]


def test_config_set_returns_only_masked_saved_value_and_env_still_wins(configuration, monkeypatch):
    monkeypatch.setenv("VULNCLAW_LLM_API_KEY", "environment-fixture-secret")
    response, final = invoke(["config", "set", "llm.api_key", "saved-fixture-secret"])
    assert final["result"] == {"key": "llm.api_key", "value": "***", "saved": True}
    assert "fixture-secret" not in response.stdout
    assert (
        yaml.safe_load(configuration.CONFIG_FILE.read_text())["llm"]["api_key"]
        == "saved-fixture-secret"
    )
    assert configuration.load_config().llm.api_key == "environment-fixture-secret"
    _, listing = invoke(["config", "list"])
    assert listing["result"]["config"]["llm"]["api_key"] == "***"


def test_invalid_secret_value_is_not_echoed_or_saved(configuration):
    before = configuration.CONFIG_FILE.read_bytes()
    response, final = invoke(
        ["config", "set", "llm.api_keys", '[{"private":"fixture-private-value"}]']
    )
    assert response.exit_code == 1
    assert "fixture-private-value" not in response.stdout
    assert final["result"]["feedback"]["issues"][0]["path"] == ["llm", "api_keys", 0]
    assert configuration.CONFIG_FILE.read_bytes() == before
