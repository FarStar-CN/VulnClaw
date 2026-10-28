"""Headless task command adapters. Python services own parsing and execution."""

from __future__ import annotations

from typing import Optional

import typer

from vulnclaw import headless
from vulnclaw.cli.execution import HeadlessExecutor
from vulnclaw.cli.output import CommandError, current_output, perform, run_async
from vulnclaw.cli.task_input import task_from_context
from vulnclaw.config.settings import load_config
from vulnclaw.config.token_provider import has_llm_credentials
from vulnclaw.input_service import InputRequest


def _network(ctx: typer.Context):
    from vulnclaw.cli.task_input import explicit, merge_task_fields, read_input_json
    from vulnclaw.task_operations import NetworkScanRequest, execute_network_scan
    from vulnclaw.task_service import TaskCreateRequest

    values = ctx.params
    if values["output_format"] not in {"json", "jsonl"}:
        raise CommandError("invalid_format", "--format must be json or jsonl")
    if values["fail_on"] not in headless.FAIL_ON_MODES:
        raise CommandError("invalid_arguments", "--fail-on must be verified, any or never")
    raw = read_input_json(values["input_json"]) if values["input_json"] else {}
    for name in NetworkScanRequest.model_fields.keys() - {"task"}:
        if explicit(ctx, name) and name in raw:
            raise CommandError(
                "conflicting_input", f"{name} was supplied in both CLI and input JSON", path=[name],
                constraints={"sources": ["cli", "input_json"]},
            )
        raw.setdefault(name, values[name])
    task = merge_task_fields(ctx, raw.get("task") or {}, "run", skip=frozenset({"max_rounds"}))
    if not task.get("target"):
        from vulnclaw.agent.network_scan import detect_connected_wifi_target

        task["target"] = detect_connected_wifi_target().cidr
    raw["task"] = TaskCreateRequest.model_validate(task)
    request = NetworkScanRequest.model_validate(raw)
    config = load_config()
    if not has_llm_credentials(config.llm):
        raise CommandError(
            "requires_input",
            "Configure LLM credentials before executing this task.",
            status="requires_input",
            path=["llm", "credentials"],
        )
    executor = HeadlessExecutor(current_output())

    async def direct(application, sink):
        return await execute_network_scan(application, sink, request)

    return run_async(
        executor.execute(InputRequest(task=request.task), direct=direct, fail_on=values["fail_on"])
    )


def ask(
    text: Optional[str] = typer.Argument(None, help="One natural-language input"),
    input_json: Optional[str] = typer.Option(
        None, "--input-json", help="JSON containing text; - reads stdin once"
    ),
    output_format: str = typer.Option("json", "--format"),
) -> None:
    def operation():
        from vulnclaw.cli.task_input import read_input_json

        if output_format not in {"json", "jsonl"}:
            raise CommandError("invalid_format", "--format must be json or jsonl")
        payload = read_input_json(input_json) if input_json else {}
        if text is not None and "text" in payload:
            raise CommandError("conflicting_input", "text was supplied in both CLI and input JSON",
                               path=["text"], constraints={"sources": ["cli", "input_json"]})
        if set(payload) - {"text"}:
            raise CommandError("invalid_input_json", "ask input JSON accepts text only",
                               path=["input_json"], constraints={"fields": ["text"]})
        value = text if text is not None else payload.get("text")
        if not isinstance(value, str) or not value.strip():
            raise CommandError(
                "requires_input", "Provide a natural-language input.", status="requires_input",
                path=["text"], constraints={"sources": ["argument:text", "input_json:text"]},
            )
        request = InputRequest(text=value)
        config = load_config()
        if not has_llm_credentials(config.llm):
            from vulnclaw.input_service import parse_input

            parsed = parse_input(request)
            if (
                parsed.kind == "conversation"
                or parsed.kind == "task"
                and parsed.task.get("command") != "codescan"
            ):
                raise CommandError(
                    "requires_input",
                    "Configure LLM credentials before executing this input.",
                    status="requires_input",
                    path=["llm", "credentials"],
                )
        return run_async(HeadlessExecutor(current_output()).execute(request))

    perform(operation)


def _execute(ctx: typer.Context):
    from vulnclaw.i18n import init_i18n

    if ctx.params.get("output_format") not in {"json", "jsonl"}:
        raise CommandError("invalid_format", "--format must be json or jsonl")
    config = load_config()
    init_i18n(config=config)
    task, profile = task_from_context(ctx, config)
    if task.command != "codescan" and not has_llm_credentials(config.llm):
        raise CommandError(
            "requires_input",
            "Configure LLM credentials before executing this task.",
            status="requires_input",
            path=["llm", "credentials"],
        )
    executor = HeadlessExecutor(current_output())
    direct = None
    if ctx.info_name == "solve":
        from vulnclaw.task_operations import execute_solve

        async def direct(application, sink):
            return await execute_solve(application, sink, task, ctx.params.get("goal"))

    return run_async(
        executor.execute(
            InputRequest(task=task),
            profile=profile,
            scan_mode=ctx.params.get("scan_mode", headless.DEFAULT_SCAN_MODE),
            scope_mode=ctx.params.get("scope_mode", headless.DEFAULT_SCOPE_MODE),
            fail_on=ctx.params.get("fail_on", headless.DEFAULT_FAIL_ON),
            direct=direct,
        )
    )


def run(
    ctx: typer.Context,
    target: Optional[str] = typer.Argument(None, help="Target, or supply --input-json"),
    scope: str = typer.Option("full", help="Test scope: full, web, api, mobile"),
    output: Optional[str] = typer.Option(None, help="Output report file path"),
    prompt: Optional[str] = typer.Option(
        None, "--prompt", help="Custom natural language prompt (overrides auto-generated prompt)"
    ),
    engine: Optional[str] = typer.Option(
        None, "--engine", help="Autonomous engine for this run: solve, team, or rounds"
    ),
    only_port: Optional[int] = typer.Option(
        None, "--only-port", help="Restrict testing to a single port"
    ),
    only_host: Optional[str] = typer.Option(
        None, "--only-host", help="Restrict testing to a single host"
    ),
    only_path: Optional[str] = typer.Option(
        None, "--only-path", help="Restrict testing to a single path"
    ),
    blocked_host: Optional[str] = typer.Option(
        None, "--blocked-host", help="Explicitly blocked host"
    ),
    blocked_path: Optional[str] = typer.Option(
        None, "--blocked-path", help="Explicitly blocked path"
    ),
    allow_actions: Optional[str] = typer.Option(
        None, "--allow-actions", help="Comma-separated allowed actions"
    ),
    block_actions: Optional[str] = typer.Option(
        None, "--block-actions", help="Comma-separated blocked actions"
    ),
    resume: bool = typer.Option(True, "--resume/--no-resume", help="Resume previous target state"),
    snapshot: Optional[str] = typer.Option(
        None, "--snapshot", help="Resume from a specific target snapshot id"
    ),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="Accepted during the entrypoint migration; execution is always headless.",
    ),
    scan_mode: str = typer.Option(
        headless.DEFAULT_SCAN_MODE,
        "--scan-mode",
        help="Depth preset over the effort knobs: quick | standard | deep. Explicit --max-* flags override the preset.",
    ),
    fail_on: str = typer.Option(
        headless.DEFAULT_FAIL_ON,
        "--fail-on",
        help="Which finding class trips a nonzero exit: verified | any | never.",
    ),
    scope_mode: str = typer.Option(
        headless.DEFAULT_SCOPE_MODE,
        "--scope-mode",
        help="Scope selection recorded for the run: full (whole surface, current behaviour) | auto (diff-scope to changed code — consumed by the Target diff-scope model, #35; falls back to full until that lands).",
    ),
    max_steps: Optional[int] = typer.Option(
        None, "--max-steps", help="Override the scan-mode explore-step cap"
    ),
    max_directions: Optional[int] = typer.Option(
        None,
        "--max-directions",
        help="Deprecated compatibility option; model-led solve ignores research-direction limits",
    ),
    max_tool_rounds: Optional[int] = typer.Option(
        None, "--max-tool-rounds", help="Override the scan-mode tool-use compatibility budget"
    ),
    max_parallel: Optional[int] = typer.Option(
        None, "--max-parallel", help="Override the scan-mode fan-out cap (1 = single agent)"
    ),
    max_rounds: Optional[int] = typer.Option(
        None, "--max-rounds", help="Override the scan-mode legacy-engine round cap"
    ),
    run_name: Optional[str] = typer.Option(
        None, "--run-name", help="Explicit name for a new run directory"
    ),
    resume_run: Optional[str] = typer.Option(
        None, "--resume-run", help="Resume an exact run by run name"
    ),
    runs_dir: Optional[str] = typer.Option(
        None, "--runs-dir", help="Run-directory root (overrides VULNCLAW_RUNS_DIR)"
    ),
    additional_targets: Optional[list[str]] = typer.Option(
        None, "--target", help="Additional target to include in this run"
    ),
    target_type: Optional[str] = typer.Option(
        None,
        "--target-type",
        help="Override target type: local_repo, repo_url, web_url, domain, ip",
    ),
    mount: bool = typer.Option(
        False, "--mount", help="Use mount ingress mode for local repository targets"
    ),
    repair: bool = typer.Option(
        False, "--repair", help="Repair a corrupt run by rolling back to the last valid snapshot"
    ),
    force_fresh: bool = typer.Option(
        False, "--force-fresh", help="Start a fresh run without overwriting a corrupt one"
    ),
    no_import: bool = typer.Option(
        False, "--no-import", help="Read legacy target state without importing it into a run"
    ),
    stream: bool = typer.Option(
        False,
        "--stream",
        help="Emit newline-delimited JSON events (status/log/finding/complete) for machine consumers",
    ),
    continuous: bool = typer.Option(False, "--continuous", help="Repeat execution in cycles"),
    rounds_per_cycle: Optional[int] = typer.Option(None, "--rounds-per-cycle", "--rounds", "-r"),
    max_cycles: Optional[int] = typer.Option(None, "--max-cycles", "--cycles", "-c"),
    no_report: bool = typer.Option(False, "--no-report", help="Disable automatic report exports"),
    ports: Optional[str] = typer.Option(None, "--ports"),
    cve: Optional[str] = typer.Option(None, "--cve"),
    cmd: Optional[str] = typer.Option(None, "--cmd"),
    input_json: Optional[str] = typer.Option(
        None, "--input-json", help="Task JSON file, or - for one stdin document"
    ),
    output_format: str = typer.Option("json", "--format", help="Machine output: json or jsonl"),
    allowed_ports: Optional[str] = typer.Option(None, "--allowed-ports"),
    blocked_ports: Optional[str] = typer.Option(None, "--blocked-ports"),
    poc_verify: Optional[bool] = typer.Option(None, "--poc-verify/--no-poc-verify"),
) -> None:
    perform(lambda: _execute(ctx))


def solve(
    ctx: typer.Context,
    target: Optional[str] = typer.Argument(None, help="Target, or supply --input-json"),
    goal: Optional[str] = typer.Option(
        None, "--goal", help="Success condition, e.g. 'capture the flag' / 'get a shell'"
    ),
    prompt: Optional[str] = typer.Option(
        None, "--prompt", help="Custom task description (overrides auto-generated)"
    ),
    max_steps: int = typer.Option(
        240,
        "--max-steps",
        help="Runaway safety budget for autonomous turns (not a planned round count)",
    ),
    max_directions: int = typer.Option(
        3,
        "--max-directions",
        help="Deprecated compatibility option; model-led solve ignores research-direction limits",
    ),
    max_tool_rounds: int = typer.Option(
        6,
        "--max-tool-rounds",
        help="Compatibility option; model-led solve decides tool use per step",
    ),
    resume: bool = typer.Option(True, "--resume/--no-resume", help="Resume previous target state"),
    snapshot: Optional[str] = typer.Option(None, "--snapshot", help="Resume from a snapshot id"),
    run_name: Optional[str] = typer.Option(
        None, "--run-name", help="Explicit name for a new run directory"
    ),
    resume_run: Optional[str] = typer.Option(
        None, "--resume-run", help="Resume an exact run by run name"
    ),
    runs_dir: Optional[str] = typer.Option(None, "--runs-dir", help="Run-directory root"),
    additional_targets: Optional[list[str]] = typer.Option(
        None, "--target", help="Additional target to include in this run"
    ),
    target_type: Optional[str] = typer.Option(None, "--target-type", help="Override target type"),
    mount: bool = typer.Option(False, "--mount", help="Use mount ingress mode"),
    repair: bool = typer.Option(False, "--repair", help="Repair a corrupt run"),
    force_fresh: bool = typer.Option(False, "--force-fresh", help="Start a fresh run"),
    no_import: bool = typer.Option(False, "--no-import", help="Do not import legacy state"),
    stream: bool = typer.Option(
        False, "--stream", help="Emit newline-delimited JSON events for machine consumers"
    ),
    input_json: Optional[str] = typer.Option(
        None, "--input-json", help="Task JSON file, or - for one stdin document"
    ),
    output_format: str = typer.Option("json", "--format", help="Machine output: json or jsonl"),
    fail_on: str = typer.Option(headless.DEFAULT_FAIL_ON, "--fail-on"),
    only_port: Optional[int] = typer.Option(None, "--only-port"),
    allowed_ports: Optional[str] = typer.Option(None, "--allowed-ports"),
    blocked_ports: Optional[str] = typer.Option(None, "--blocked-ports"),
    only_host: Optional[str] = typer.Option(None, "--only-host"),
    only_path: Optional[str] = typer.Option(None, "--only-path"),
    blocked_host: Optional[str] = typer.Option(None, "--blocked-host"),
    blocked_path: Optional[str] = typer.Option(None, "--blocked-path"),
    allow_actions: Optional[str] = typer.Option(None, "--allow-actions"),
    block_actions: Optional[str] = typer.Option(None, "--block-actions"),
) -> None:
    perform(lambda: _execute(ctx))


def network_scan(
    ctx: typer.Context,
    target: Optional[str] = typer.Argument(
        None, help="目标主机/IP/CIDR，默认使用当前连接的 Wi-Fi 子网"
    ),
    profile: str = typer.Option(
        "adaptive", "--profile", help="网络扫描画像：adaptive、fast、thorough、stealth"
    ),
    ports: Optional[str] = typer.Option(None, "--ports", help="端口范围，如 80,443,1-1000"),
    max_rounds: int = typer.Option(
        0, "--max-rounds", help="Agent 后续跟进轮数（0=使用配置默认值）"
    ),
    parallel_agents: int = typer.Option(
        1,
        "--parallel-agents",
        min=1,
        help="在已发现的攻击面上并行派生的子 Agent 数量（1 表示不启用并行）",
    ),
    parallel_depth: int = typer.Option(
        1, "--parallel-depth", min=1, help="子 Agent 攻击面发现的有界波次数"
    ),
    worker_rounds: int = typer.Option(
        3, "--worker-rounds", min=1, help="每个子 Agent worker 的执行轮数"
    ),
    surface_limit: int = typer.Option(
        20, "--surface-limit", min=1, help="用于子 Agent 并行派生的最大攻击面数量"
    ),
    safe_probes: bool = typer.Option(
        True, "--safe-probes/--no-safe-probes", help="nmap 扫描后默认仅执行非破坏性的验证探测"
    ),
    prompt: Optional[str] = typer.Option(
        None, "--prompt", help="Custom natural language prompt (overrides auto-generated prompt)"
    ),
    only_port: Optional[int] = typer.Option(
        None, "--only-port", help="Restrict testing to a single port"
    ),
    only_host: Optional[str] = typer.Option(
        None, "--only-host", help="Restrict testing to a single host"
    ),
    blocked_host: Optional[str] = typer.Option(
        None, "--blocked-host", help="Explicitly blocked host"
    ),
    allow_actions: Optional[str] = typer.Option(
        None, "--allow-actions", help="Comma-separated allowed actions"
    ),
    block_actions: Optional[str] = typer.Option(
        None, "--block-actions", help="Comma-separated blocked actions"
    ),
    resume: bool = typer.Option(True, "--resume/--no-resume", help="Resume previous target state"),
    snapshot: Optional[str] = typer.Option(
        None, "--snapshot", help="Resume from a specific target snapshot id"
    ),
    run_name: Optional[str] = typer.Option(
        None, "--run-name", help="Explicit name for a new run directory"
    ),
    resume_run: Optional[str] = typer.Option(
        None, "--resume-run", help="Resume an exact run by run name"
    ),
    runs_dir: Optional[str] = typer.Option(None, "--runs-dir", help="Run-directory root"),
    additional_targets: Optional[list[str]] = typer.Option(
        None, "--target", help="Additional target to include in this run"
    ),
    target_type: Optional[str] = typer.Option(None, "--target-type", help="Override target type"),
    mount: bool = typer.Option(False, "--mount", help="Use mount ingress mode"),
    repair: bool = typer.Option(False, "--repair", help="Repair a corrupt run"),
    force_fresh: bool = typer.Option(False, "--force-fresh", help="Start a fresh run"),
    no_import: bool = typer.Option(False, "--no-import", help="Do not import legacy state"),
    stream: bool = typer.Option(
        False, "--stream", help="Emit newline-delimited JSON events for machine consumers"
    ),
    input_json: Optional[str] = typer.Option(
        None, "--input-json", help="NetworkScanRequest JSON file or -"
    ),
    output_format: str = typer.Option("json", "--format"),
    fail_on: str = typer.Option(headless.DEFAULT_FAIL_ON, "--fail-on"),
) -> None:
    perform(lambda: _network(ctx))
