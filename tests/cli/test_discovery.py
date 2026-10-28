"""Agents can discover callable interfaces without starting business services."""

import json

import pytest
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from vulnclaw.cli.main import app
from vulnclaw.interaction_contracts import ApprovalResolution, describe_protocol
from vulnclaw.session_protocol import CLIENT_MESSAGE_TYPES, decode_client_message


@pytest.fixture(autouse=True)
def no_runtime(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("discovery must not load configuration or start a runtime")

    monkeypatch.setattr("vulnclaw.application.create_runtime", unexpected)
    monkeypatch.setattr("vulnclaw.config.settings.load_config", unexpected)
    monkeypatch.setattr("vulnclaw.cli.management.load_config", unexpected)


def invoke(args):
    response = CliRunner().invoke(app, args)
    assert response.exit_code == 0, response.stdout
    messages = [json.loads(line) for line in response.stdout.splitlines()]
    assert messages[-1]["type"] == "command_result"
    assert sum(message["type"] == "command_result" for message in messages) == 1
    return messages[-1]["result"]


@pytest.mark.parametrize(
    "path", [[], ["config"], ["kb"], ["plugins"], ["experience"], ["target-state"], ["code"]]
)
@pytest.mark.parametrize("format", ["json", "jsonl"])
def test_bare_groups_describe_operations_without_business_runtime(path, format):
    document = invoke([*path, "--format", format])
    assert document["kind"] == "command_group"
    assert document["path"] == path
    names = [operation["name"] for operation in document["operations"]]
    assert names == sorted(names)
    assert names
    for operation in document["operations"]:
        assert operation["summary"]
        assert operation["summary"] != " ".join([*path, operation["name"]])
        assert operation["describe_argv"] == ["vulnclaw", "describe", *path, operation["name"]]
        assert operation["example_argv"][: len(path) + 2] == ["vulnclaw", *path, operation["name"]]


def test_command_schema_explains_alternative_inputs_and_aliases():
    for command in ["run", "recon", "scan", "exploit", "persistent", "solve", "codescan"]:
        document = invoke(["describe", command])
        assert document["input_requirements"] == [
            {
                "field": "target",
                "required": True,
                "sources": ["argument:target", "input_json:target"],
            }
        ]
        assert document["input_json_command"] == ("run" if command == "solve" else command)
        assert "target" in document["input_json_schema"]["required"]
        assert next(p for p in document["parameters"] if p["name"] == "target")["required"] is False


def test_parser_constraints_and_config_schema_are_discoverable():
    document = invoke(["describe", "feedback"])
    rating = next(p for p in document["parameters"] if p["name"] == "rating")
    assert rating["required"]
    assert rating["type"] == "integer"
    assert rating["constraints"] == {"minimum": 1, "maximum": 5}
    settings = invoke(["describe", "config", "set"])
    assert "llm" in settings["configuration_schema"]["properties"]
    assert [p["name"] for p in settings["parameters"] if p["required"]] == ["key", "value"]


def test_code_artifact_format_and_machine_format_are_distinct():
    document = invoke(["describe", "code", "scan"])
    parameters = {p["name"]: p for p in document["parameters"]}
    assert "sarif" in parameters["artifact_format"]["choices"]
    assert parameters["output_format"]["choices"] == ["json", "jsonl"]
    assert parameters["output_format"]["flags"] == ["--output-format"]


def test_protocol_overview_has_launch_sequence_and_interactions():
    overview = invoke(["describe", "--protocol"])
    assert overview["transport"]["command_argv"][-3:] == ["-u", "-m", "vulnclaw.session_backend"]
    assert overview["initialization"] == {"send": "initialize", "wait_for": "ready"}
    for name in [*CLIENT_MESSAGE_TYPES, *overview["interactions"]]:
        document = invoke(["describe", "--protocol", name])
        example = document["example_request"]
        decode_client_message(json.dumps(example))
        schema = document.get("request_schema") or document["response"]["request_schema"]
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(example)


def test_approval_contract_and_runtime_require_identical_decisions():
    contract = describe_protocol("approval_required")
    validator = Draft202012Validator(contract["response"]["arguments_schema"])
    for decision in ["approve", "deny"]:
        arguments = {"request_hash": "current-pending-hash", "decision": decision}
        validator.validate(arguments)
        assert ApprovalResolution.model_validate(arguments).decision == decision
    for arguments in [
        {"decision": "approve"},
        {"request_hash": "x", "decision": "yes"},
        {"request_hash": "", "decision": "approve"},
        {"request_hash": "x", "decision": "approve", "extra": True},
    ]:
        assert list(validator.iter_errors(arguments))
        with pytest.raises(ValueError):
            ApprovalResolution.model_validate(arguments)
    assert contract["completion"]["accepted_result_status"] == "resolved"
    answer = describe_protocol("waiting_for_input")
    assert answer["trigger"] == {"state_path": "task.waiting_for_input", "value": True}


@pytest.mark.parametrize(
    "args",
    [
        ["describe", "absent"],
        ["describe", "config", "absent"],
        ["describe", "--protocol", "absent"],
    ],
)
def test_unknown_discovery_path_returns_candidates(args):
    response = CliRunner().invoke(app, args)
    assert response.exit_code == 1
    result = json.loads(response.stdout)
    assert result["result"]["feedback"]["issues"][0]["constraints"]["choices"]
