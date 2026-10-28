"""Shared application orchestration before entering the report renderer."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from vulnclaw.agent.context import SessionState
from vulnclaw.agent.exec_gate import get_execution_gate
from vulnclaw.finding_service import verify_session_file, verify_target
from vulnclaw.findings.verification import verify_findings
from vulnclaw.report.generator import generate_report
from vulnclaw.target_state.store import load_target_state

if TYPE_CHECKING:
    from vulnclaw.config.schema import VulnClawConfig
    from vulnclaw.orchestrator import OrchestratorRunResult

CONTEXT_FIELDS = ("resume_meta", "resume_summary", "recon_meta", "runtime_meta", "finding_meta")


async def generate_report_for_run(
    run: OrchestratorRunResult, *, config: VulnClawConfig, output_path: str | None = None,
) -> dict[str, str]:
    """Export a completed run's audited snapshot without loading or auditing data."""
    get_execution_gate(config).require_execution("report export")
    if run.audited_session is None:
        raise ValueError("Report export requires the run's audited session snapshot")
    if output_path is None and run.run_context is not None:
        output_path = str(
            run.run_context.run_dir / "reports"
            / f"report_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:8]}.md"
        )
    path = Path(await generate_report(
        run.audited_session, output_path, report_format=config.session.report_format,
    )).resolve()
    artifacts = path.parent / (path.stem + ".artifacts") / "findings"
    return {
        "path": str(path),
        "findings_json": str(artifacts / "findings.json"),
        "findings_sarif": str(artifacts / "findings.sarif"),
    }


async def generate_report_from_file(session_path: str, *, config=None):
    get_execution_gate(config).require_execution("report export")
    session, _path = await verify_session_file(session_path, config=config, reuse=True)
    return await generate_report(session)


async def generate_report_for_target(
    target: str, *, current_session=None, report_format="markdown", output_path=None, config=None,
):
    """Use a task's audited snapshot, or audit persisted data before exporting."""
    get_execution_gate(config).require_execution("report export")
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


async def export_report(
    agent: Any,
    config: Any,
    *,
    kind: str = "security",
    output: str | None = None,
    partial: bool = False,
) -> dict[str, str]:
    get_execution_gate(config).require_execution("report export")
    if kind == "replay":
        from vulnclaw.report.solve_report import generate_solve_report

        state = agent.session_state.agent_state
        if not state.goal and not state.evidence:
            raise ValueError("No solve evidence is available for a replay report")
        path = generate_solve_report(state, output)
    elif kind == "security":
        if not agent.session_state.target:
            raise ValueError("No target results are available for a security report")
        path = await generate_report_for_target(
            agent.session_state.target,
            current_session=agent.session_state,
            output_path=output,
            report_format=config.session.report_format,
            config=config,
        )
    else:
        raise ValueError("report kind must be security or replay")
    if partial and kind == "security":
        report_path = Path(path)
        content = report_path.read_text(encoding="utf-8")
        note = "Status: partial (engagement not completed)"
        content = (
            f"<p>{note}</p>\n{content}"
            if report_path.suffix == ".html"
            else f"> {note}\n\n{content}"
        )
        report_path.write_text(content, encoding="utf-8")
    result = {"kind": kind, "path": str(Path(path).resolve())}
    if kind == "security":
        result.update(
            {
                "findings_json": str(Path(path).with_suffix(".artifacts") / "findings" / "findings.json"),
                "findings_sarif": str(Path(path).with_suffix(".artifacts") / "findings" / "findings.sarif"),
            }
        )
    return result
