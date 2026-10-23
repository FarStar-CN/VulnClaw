"""Dedicated task operations sharing application runtime and persistence ownership."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vulnclaw.application import task_result
from vulnclaw.config.domain_models import validate_action_constraints
from vulnclaw.task_service import (
    SCOPE_FIELDS,
    PreparedTask,
    TaskCreateRequest,
    TaskExecution,
    build_scope_constraints,
    prepare_task,
)


def _run_context(task: TaskCreateRequest) -> dict[str, Any]:
    fields = (
        "target",
        "resume",
        "snapshot_id",
        "run_name",
        "resume_run_name",
        "runs_dir",
        "additional_targets",
        "target_type",
        "mount",
        "repair",
        "force_fresh",
        "no_import",
    )
    return {field: getattr(task, field) for field in fields}


async def execute_solve(application, sink, request: TaskCreateRequest, goal: str | None):
    from vulnclaw.i18n import _
    from vulnclaw.report_service import export_report

    task = prepare_task(request)
    action = None

    async def runner(agent):
        nonlocal action
        agent.apply_task_constraints(task.constraints)
        action = await agent.solve(
            task.prompt,
            target=request.target,
            goal=goal or _("cli.default_goal"),
            max_steps=request.options.max_steps,
            max_tool_rounds=request.options.max_tool_rounds,
            stream_sink=sink,
            task_constraints=task.constraints,
            on_event=lambda kind, payload: (
                sink.on_question(str(payload.get("question", "")))
                if kind == "ask_user"
                else sink.on_status(str(payload.get("reason") or kind))
            ),
        )

    run = await application.run_orchestrated(
        command="solve",
        runner=runner,
        poc_verify=request.options.poc_verify,
        **_run_context(request),
    )
    result = task_result(application.runtime, task, TaskExecution(run, action))
    result["command"] = "solve"
    if (
        application.agent.session_state.agent_state.completed
        and application.config.session.solve_auto_report
    ):
        result["report"] = await export_report(application.agent, application.config, kind="replay")
        result["report"].pop("text", None)
    return result


class NetworkScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: TaskCreateRequest
    profile: Literal["adaptive", "fast", "thorough", "stealth"] = "adaptive"
    max_rounds: int = Field(default=0, ge=0)
    parallel_agents: int = Field(default=1, ge=1)
    parallel_depth: int = Field(default=1, ge=1)
    worker_rounds: int = Field(default=3, ge=1)
    surface_limit: int = Field(default=20, ge=1)
    safe_probes: bool = True

    @model_validator(mode="after")
    def relevant_task_options(self):
        supported = SCOPE_FIELDS | {"ports", "poc_verify"}
        supplied = {
            key
            for key in self.task.options.model_fields_set
            if getattr(self.task.options, key) is not None
            and (key != "continuous" or self.task.options.continuous)
        }
        if supplied - supported:
            raise ValueError(
                f"unsupported network task options: {', '.join(sorted(supplied - supported))}"
            )
        return self


async def execute_network_scan(application, sink, request: NetworkScanRequest):
    task = request.task
    options = task.options
    follow_up = (
        "Then prioritize weak links and perform safe, non-destructive verification probes only."
        if request.safe_probes
        else "Then summarize weak links without running follow-up probes."
    )
    port_hint = f" limited to ports {options.ports}" if options.ports else ""
    prompt = task.prompt or (
        f"Perform an authorized {request.profile} network scan against {task.target}{port_hint}. "
        f"Use the nmap_scan tool with profile={request.profile}"
        f"{f' and ports={options.ports}' if options.ports else ''}. "
        f"{follow_up} Record open services and candidate weak-link findings in target state. "
        "Do not brute force credentials, run destructive payloads, or perform post-exploitation."
    )
    scope = options.model_dump()
    if request.safe_probes and not scope["allow_actions"] and not scope["block_actions"]:
        scope["allow_actions"] = ["recon", "scan"]
    # Preserve network-scan's explicit scope: CIDRs are not narrowed to a URL hostname.
    constraints = build_scope_constraints("", scope)
    violation = validate_action_constraints("scan", constraints)
    if violation is not None:
        raise ValueError(violation)
    block = constraints.to_prompt_block()
    if block:
        prompt += "\n\n" + block
    application.current_constraints = constraints.model_dump(mode="json")
    action = None

    async def runner(agent):
        nonlocal action
        agent.apply_task_constraints(constraints)
        rounds = request.max_rounds or application.config.session.max_rounds
        if request.parallel_agents > 1:
            from vulnclaw.agent.parallel_agents import run_parallel_pentest

            action = await run_parallel_pentest(
                agent,
                agent_factory=lambda: agent.__class__(
                    application.config, application.runtime.mcp_manager
                ),
                user_input=prompt,
                target=task.target,
                discovery_rounds=rounds,
                worker_rounds=request.worker_rounds,
                max_agents=request.parallel_agents,
                max_depth=request.parallel_depth,
                surface_limit=request.surface_limit,
                stream_sink=sink,
            )
        else:
            action = await agent.auto_pentest(
                prompt,
                target=task.target,
                max_rounds=rounds,
                stream_sink=sink,
                task_constraints=constraints,
            )

    run = await application.run_orchestrated(
        command="network-scan",
        runner=runner,
        poc_verify=options.poc_verify,
        **_run_context(task),
    )
    result = task_result(
        application.runtime, PreparedTask(task, prompt, constraints), TaskExecution(run, action)
    )
    result.update(command="network-scan", network_profile=request.profile)
    return result
