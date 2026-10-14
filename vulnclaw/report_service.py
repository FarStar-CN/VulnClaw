"""Shared application orchestration before entering the report renderer."""

from __future__ import annotations

from vulnclaw.agent.context import SessionState
from vulnclaw.finding_service import verify_session_file, verify_target
from vulnclaw.findings.verification import verify_findings
from vulnclaw.report.generator import generate_report
from vulnclaw.target_state.store import load_target_state

CONTEXT_FIELDS = ("resume_meta", "resume_summary", "recon_meta", "runtime_meta", "finding_meta")


async def generate_report_from_file(session_path: str, *, config=None):
    session, _path = await verify_session_file(session_path, config=config, reuse=True)
    return await generate_report(session)


async def generate_report_for_target(
    target: str, *, current_session=None, report_format="markdown", output_path=None, config=None,
):
    """Use a task's audited snapshot, or audit persisted data before exporting."""
    if current_session is not None:
        from vulnclaw.findings.types import require_assessed

        try:
            require_assessed(current_session)
        except ValueError:
            current_session = await verify_findings(current_session, config=config)
        return await generate_report(current_session, output_path, report_format=report_format)
    if load_target_state(target):
        session, _path, raw = await verify_target(target, config=config, reuse=True)
        return await generate_report(
            session, output_path, report_format=report_format,
            target_state_context={key: raw.get(key) for key in CONTEXT_FIELDS},
        )
    return await generate_report(SessionState(target=target), output_path, report_format=report_format)
