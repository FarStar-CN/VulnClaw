"""Task overview preparation is read-only and shares the execution contract."""

from types import SimpleNamespace

import pytest

from vulnclaw.agent.builtin_tools import enforce_host_path_constraints, enforce_port_constraints
from vulnclaw.config.domain_models import (
    TEST_ACTIONS,
    TaskConstraints,
    complete_action_lists,
    validate_action_constraints,
)
from vulnclaw.config.schema import VulnClawConfig
from vulnclaw.task_preview import preview_task
from vulnclaw.task_service import (
    TaskCreateRequest,
    TaskOptions,
    prepare_task,
    task_request_from_payload,
)


@pytest.fixture
def config():
    config = VulnClawConfig()
    config.session.persistent_rounds_per_cycle = 37
    config.session.persistent_max_cycles = 0
    config.session.persistent_auto_report = False
    config.session.poc_verify_enabled = True
    return config


def test_preview_resolves_real_defaults_without_mutating_inputs(config):
    defaults = {"only_port": 443, "resume": False}
    before = config.model_dump()
    result = preview_task(
        {"text": '/run https://lab.test --continuous --prompt "Inspect login" --engine team'},
        defaults=defaults, config=config,
    )
    assert result["errors"] == {}
    assert result["task"]["options"] == {
        "engine": "team", "continuous": True, "rounds_per_cycle": 37,
        "max_cycles": 0, "auto_report": False, "poc_verify": True, "only_port": 443,
        "allow_actions": [*TEST_ACTIONS, "report"], "block_actions": [],
    }
    assert result["task"]["resume"] is False
    assert result["task"]["prompt"] == "Inspect login"
    assert result["constraints"]["allowed_hosts"] == ["lab.test"]
    assert defaults == {"only_port": 443, "resume": False}
    assert config.model_dump() == before


def test_explicit_false_and_zero_override_config(config):
    result = preview_task({"task": {
        "command": "run", "target": "lab.test", "resume": True,
        "options": {"continuous": True, "poc_verify": False, "auto_report": True,
                    "rounds_per_cycle": 2, "max_cycles": 0},
    }}, defaults={"resume": False}, config=config)
    options = result["task"]["options"]
    assert (options["poc_verify"], options["auto_report"], options["max_cycles"]) == (False, True, 0)
    assert result["task"]["resume"] is True
    assert result["defaults"]["auto_report_explicit"] is True


@pytest.mark.parametrize("target,options,field", [
    ("https://lab.test", {"allowed_ports": "443,65536"}, "allowed_ports"),
    ("https://lab.test", {"blocked_ports": [0]}, "blocked_ports"),
    ("https://lab.test:bad", {}, "target"),
    ("https://bad host.test", {}, "target"),
    ("http://[broken", {}, "target"),
    ("https://lab.test", {"only_host": "elsewhere.test"}, "only_host"),
    ("https://lab.test", {"blocked_host": "lab.test"}, "blocked_host"),
    ("https://lab.test", {"allowed_ports": [80]}, "allowed_ports"),
    ("https://lab.test", {"blocked_ports": [443]}, "blocked_ports"),
    ("lab.test", {"allowed_ports": [443], "blocked_ports": [443]}, "blocked_ports"),
    ("lab.test", {"only_path": "admin"}, "only_path"),
    ("https://lab.test/admin", {"blocked_path": "/admin"}, "blocked_path"),
    ("https://lab.test/admin", {"only_path": "/login"}, "only_path"),
    ("lab.test", {"only_path": "/admin", "blocked_path": "/admin"}, "blocked_path"),
    ("lab.test", {"block_actions": ["recon", "scan", "exploit", "post_exploitation"]}, "allow_actions"),
    ("lab.test", {"allow_actions": ["recon", "scan"], "poc_verify": True}, "poc_verify"),
    ("lab.test", {"rounds_per_cycle": 4}, "target"),
])
def test_preview_returns_field_errors(config, target, options, field):
    result = preview_task({"task": {
        "command": "run", "target": target, "options": options,
    }}, defaults={}, config=config)
    assert field in result["errors"]


def test_cleared_scope_does_not_restore_session_defaults(config):
    result = preview_task({"task": {
        "command": "run", "target": "https://lab.test",
        "options": {"allowed_ports": [], "blocked_ports": [], "only_host": "", "only_path": ""},
    }}, defaults={"only_port": 80, "blocked_ports": [443], "only_host": "old.test", "only_path": "/old"}, config=config)
    assert result["errors"] == {}
    assert result["constraints"]["allowed_ports"] == []
    assert result["constraints"]["blocked_ports"] == []
    assert result["constraints"]["allowed_hosts"] == []
    assert result["constraints"]["allowed_paths"] == []


def test_multiple_ports_and_hosts_use_existing_tool_enforcement(config):
    result = preview_task({"text": "/run lab.test --allowed-ports 80,443 --blocked-ports 80 --only-host LAB.TEST,api.test"}, defaults={}, config=config)
    assert result["errors"] == {}
    task = prepare_task(task_request_from_payload(result["task"]))
    agent = SimpleNamespace(session_state=SimpleNamespace(task_constraints=task.constraints))
    assert enforce_port_constraints(agent, [443]) is None
    assert "blocked" in enforce_port_constraints(agent, [80])
    assert "outside" in enforce_port_constraints(agent, [8080])
    assert enforce_host_path_constraints(agent, host="api.test") is None
    assert "outside" in enforce_host_path_constraints(agent, host="outside.test")


def test_initial_lists_respect_blocks_and_partition_test_actions(config):
    config.session.poc_verify_enabled = False
    result = preview_task({"text": "/run lab.test --allow-actions recon,scan,exploit --block-actions exploit"}, defaults={}, config=config)
    assert result["actions"] == ["recon", "scan"]
    assert result["task"]["options"]["allow_actions"] == ["recon", "scan"]
    assert result["task"]["options"]["block_actions"] == ["exploit", "post_exploitation"]


@pytest.mark.parametrize("mask", range(16))
def test_edited_action_lists_override_initial_defaults_including_empty_blacklist(config, mask):
    allowed = [action for index, action in enumerate(TEST_ACTIONS) if mask & (1 << index)]
    blocked = [action for action in TEST_ACTIONS if action not in allowed]
    config.session.poc_verify_enabled = False
    result = preview_task({"task": {"command": "run", "target": "lab.test", "options": {
        "allow_actions": allowed, "block_actions": blocked,
    }}}, defaults={"allow_actions": ["recon", "scan"], "block_actions": ["post_exploitation"]},
        config=config)
    options = result["task"]["options"]
    assert result["actions"] == allowed
    assert options["allow_actions"] == (allowed or ["report"])
    assert options["block_actions"] == blocked
    assert ("allow_actions" in result["errors"]) is (not allowed)
    assert set(options["allow_actions"]).isdisjoint(options["block_actions"])


@pytest.mark.parametrize("allowed,blocked", [
    ([], []), ([], ["report", "persistent"]),
    (["recon", "report", "run"], ["exploit", "persistent"]),
    (["recon", "scan"], ["scan", "post"]),
])
def test_action_partition_preserves_non_editor_permissions_and_is_idempotent(allowed, blocked):
    original = TaskConstraints(allowed_actions=allowed, blocked_actions=blocked)
    resolved = complete_action_lists(original)
    assert complete_action_lists(resolved) == resolved
    for action in (*TEST_ACTIONS, "report", "run", "persistent"):
        assert (validate_action_constraints(action, original) is None) == (
            validate_action_constraints(action, resolved) is None
        )


async def test_preview_control_does_not_change_application_state(config):
    from tests.cli.test_application import Runtime, open_session

    runtime = Runtime()
    runtime.config = config
    application = await open_session(runtime)
    previous = application.state_snapshot()
    defaults = dict(application.bootstrap)
    result, state = await application._execute_control_operation(
        "task.preview", {"text": "/run https://new.test --continuous"},
    )
    assert result["errors"] == {}
    assert state is None
    assert application.state_snapshot() == previous
    assert application.bootstrap == defaults
    assert application.active_task is None
    await application.close()


async def test_preview_jsonl_control_returns_field_errors_and_schema_valid_events(config):
    import io

    from tests.cli.test_application import Runtime
    from tests.cli.test_session_backend import events, protocol_validator, request
    from vulnclaw.session_backend import BackendSession
    from vulnclaw.session_protocol import JsonlWriter

    runtime = Runtime()
    runtime.config = config
    output = io.StringIO()
    session = BackendSession(JsonlWriter(output), runtime_factory=lambda: runtime)
    await session.handle(request("initialize", "init"))
    before = session.application.state_snapshot()
    for index, text in enumerate([
        "/run https://lab.test --allowed-ports 443 --blocked-ports 8080",
        "/run https://lab.test --allowed-ports 65536",
    ]):
        await session.handle(request("control", f"preview-{index}", payload={
            "operation": "task.preview", "arguments": {"text": text},
        }))
    emitted = events(output)
    for event in emitted:
        protocol_validator().validate(event)
    assert emitted[-2]["result"]["errors"] == {}
    assert "allowed_ports" in emitted[-1]["result"]["errors"]
    assert session.application.state_snapshot() == before
    assert not any(event["type"] == "task_started" for event in emitted)
    await session.close()


@pytest.mark.parametrize("poc", [False, True])
async def test_task_poc_override_reaches_normal_and_cycle_audits(config, monkeypatch, tmp_path, poc):
    from unittest.mock import AsyncMock

    from vulnclaw.agent.core import AgentCore
    from vulnclaw.task_service import execute_task

    config.session.output_dir = tmp_path / "output"
    config.session.runs_dir = tmp_path / "runs"
    config.session.poc_verify_enabled = not poc
    monkeypatch.setattr("vulnclaw.target_state.store.TARGETS_DIR", tmp_path / "targets")
    agent = AgentCore(config)
    agent.solve = AsyncMock(return_value=SimpleNamespace(completed=True))
    calls = []

    async def audit(state, **kwargs):
        calls.append(kwargs.get("poc_verify"))
        return state.model_copy(deep=True)

    monkeypatch.setattr("vulnclaw.findings.verification.verify_findings", audit)
    for continuous in (False, True):
        options = TaskOptions(poc_verify=poc, auto_report=False, continuous=continuous,
                              **({"max_cycles": 2, "rounds_per_cycle": 1} if continuous else {}))
        await execute_task(agent, prepare_task(TaskCreateRequest(
            command="run", target="https://lab.test", resume=False, options=options,
        )))
    assert calls == [poc, poc, poc]
    assert config.session.poc_verify_enabled is not poc


@pytest.mark.parametrize("continuous", [False, True])
async def test_edited_permissions_reach_execution_cycles_and_child_agents(
    config, monkeypatch, tmp_path, continuous,
):
    import json

    from vulnclaw.agent.core import AgentCore
    from vulnclaw.agent.solver import SolveResult
    from vulnclaw.agent.subagent.integration import execute_agent_run, get_task_runtime
    from vulnclaw.task_service import execute_task

    config.session.output_dir = tmp_path / "output"
    config.session.runs_dir = tmp_path / "runs"
    monkeypatch.setattr("vulnclaw.config.settings.SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr("vulnclaw.target_state.store.TARGETS_DIR", tmp_path / "targets")
    allowed = ["recon", "scan", "post_exploitation"]
    result = preview_task({"task": {"command": "run", "target": "https://lab.test",
        "resume": False, "options": {
            "engine": "solve", "continuous": continuous, "auto_report": False,
            "poc_verify": False, "allow_actions": allowed, "block_actions": ["exploit"],
            **({"max_cycles": 2, "rounds_per_cycle": 1} if continuous else {}),
        },
    }}, defaults={"allow_actions": ["recon", "scan"], "block_actions": ["post_exploitation"]},
        config=config)
    assert result["errors"] == {}
    visits = []

    async def solve(agent, **kwargs):
        constraints = agent.context.state.task_constraints
        assert constraints.allowed_actions == allowed
        assert constraints.blocked_actions == ["exploit"]
        assert validate_action_constraints("post_exploitation", constraints) is None
        assert validate_action_constraints("exploit", constraints) is not None
        visits.append(agent.active_role)
        if not agent.active_role:
            child = json.loads(await execute_agent_run(agent, {
                "description": "Check inherited permissions", "prompt": "Inspect task scope",
                "agent_type": "researcher", "task_kind": "research",
            }))
            assert child["ok"]
        return SolveResult(completed=True, reason="done", steps=1, evidence=0,
                           agent_state=agent.context.state.agent_state)

    async def audit(state, **kwargs):
        return state.model_copy(deep=True)

    monkeypatch.setattr("vulnclaw.agent.solver.solve", solve)
    monkeypatch.setattr("vulnclaw.findings.verification.verify_findings", audit)
    agent = AgentCore(config)
    await execute_task(agent, prepare_task(task_request_from_payload(result["task"])))
    cycles = 2 if continuous else 1
    assert visits == [None, "researcher"] * cycles
    await get_task_runtime(agent).service.shutdown()


def test_codescan_preview_round_trips_without_inserting_unsupported_execution_defaults(config):
    result = preview_task({"text": "/codescan demo/unsafe-ai-sample.ts"}, defaults={}, config=config)
    request = TaskCreateRequest.model_validate(result["task"])
    assert request.command == "codescan"
    assert result["errors"] == {}
    assert result["presets"] == []
    assert request.options.poc_verify is None
    assert request.options.auto_report is None
