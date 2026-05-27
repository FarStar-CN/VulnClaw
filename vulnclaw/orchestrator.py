"""Shared task orchestration helpers for CLI and Web flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from vulnclaw.agent.core import AgentCore
from vulnclaw.target_state.store import (
    SessionRestoreResult,
    apply_target_state_to_agent,
    build_task_session_summary,
    save_target_state,
)


@dataclass
class OrchestratorRunResult:
    restore_result: SessionRestoreResult
    summary: dict[str, Any]


async def run_agent_task(
    *,
    agent: AgentCore,
    command: str,
    target: str,
    resume: bool = True,
    snapshot_id: Optional[str] = None,
    before_restore: Optional[Callable[[SessionRestoreResult | None], None]] = None,
    on_restored: Optional[Callable[[SessionRestoreResult], None]] = None,
    runner: Callable[[AgentCore], Awaitable[Any]],
) -> OrchestratorRunResult:
    """Run a shared task flow with optional restore and summary generation."""
    restore_result = None
    if before_restore is not None:
        before_restore(None)

    if resume:
        restore_result = apply_target_state_to_agent(agent, target, snapshot_id=snapshot_id)
        if restore_result.restored and on_restored is not None:
            on_restored(restore_result)
    else:
        agent.context.state.target = target
        restore_result = SessionRestoreResult(
            restored=False,
            target=target,
            phase=getattr(agent.context.state.phase, "value", str(agent.context.state.phase)),
            snapshot_id=snapshot_id or "",
            preview={"target": target},
        )

    await runner(agent)

    if agent.session_state.target:
        save_target_state(
            agent.session_state.target,
            agent.session_state,
            command=command,
            runtime=agent.runtime,
        )

    summary = build_task_session_summary(
        agent.session_state,
        command=command,
        restored=bool(restore_result and restore_result.restored),
        snapshot_id=restore_result.snapshot_id if restore_result else "",
    )
    return OrchestratorRunResult(
        restore_result=restore_result or SessionRestoreResult(target=target),
        summary=summary,
    )
