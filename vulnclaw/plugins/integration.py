"""Persist plugin observations for later explicit structured finding submission."""

from __future__ import annotations

from typing import Any

from vulnclaw.agent.context import SessionState
from vulnclaw.evidence import CapturedObservation, EvidenceScope, EvidenceStore, new_evidence_id
from vulnclaw.evidence.models import (
    ObservationExecution,
    ObservationProducer,
    ObservationTool,
)
from vulnclaw.evidence.store import utc_now_iso
from vulnclaw.plugins.base import PluginContext
from vulnclaw.plugins.result import PluginResult


def record_plugin_result(
    session: SessionState,
    result: PluginResult,
    context: PluginContext,
    *,
    store: EvidenceStore | None = None,
    scope: EvidenceScope | None = None,
    target_id: str = "",
) -> str:
    """Record the real plugin output without automatically creating vulnerability claims."""

    if session.target and context.target and session.target != context.target:
        raise ValueError("Plugin result belongs to another session target")
    session.target = session.target or context.target
    tool = f"plugin:{result.plugin_id}"
    output = result.model_dump_json()
    if store is not None and scope is not None:
        evidence_id = new_evidence_id()
        outcome = store.commit_observation(
            CapturedObservation(
                evidence_id=evidence_id,
                run_id=store.run_id,
                target_id=target_id,
                target=session.target or "",
                resource=context.target or tool,
                producer=ObservationProducer(task_id=scope.task_id or "main"),
                tool=ObservationTool(
                    name=tool, arguments=context.model_dump(mode="json")
                ),
                acquisition=_acquisition(),
                execution=ObservationExecution(
                    outcome="succeeded" if result.ok else "failed",
                    duration_ms=int(result.elapsed_seconds * 1000),
                ),
                text=output,
                summary=f"{tool} {'succeeded' if result.ok else 'failed'}",
            ),
            scope=scope,
            operation_id=f"plugin_{result.plugin_id}",
        )
        descriptor = outcome.descriptor or store.describe(evidence_id, scope=scope)
        session.agent_state.remember_observation(descriptor, preview=output[:6000])
        record: Any = descriptor
    else:
        record = session.agent_state.remember_tool_result(
            tool=tool, arguments=context.model_dump(mode="json"),
            output=output, target=session.target or "",
        )
    session.agent_state.record_tool_call(
        tool=record.tool, arguments=record.arguments if hasattr(record, "arguments") else {},
        evidence_id=record.id,
        summary=record.summary, duration_ms=int(result.elapsed_seconds * 1000),
        ok=result.ok, error_type=result.error_type or "",
    )
    session._notify_checkpoint("plugin_result_recorded")
    return record.id


def _acquisition():
    from vulnclaw.evidence.models import ObservationAcquisition

    now = utc_now_iso()
    return ObservationAcquisition(started_at=now, finished_at=now, time_source="runtime")
