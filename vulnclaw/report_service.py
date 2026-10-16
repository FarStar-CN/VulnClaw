"""Shared exports of recorded findings and solve evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any


async def export_report(
    agent: Any,
    config: Any,
    *,
    kind: str = "security",
    output: str | None = None,
    partial: bool = False,
) -> dict[str, str]:
    if kind == "replay":
        from vulnclaw.report.solve_report import generate_solve_report

        state = agent.session_state.agent_state
        if not state.goal and not state.evidence:
            raise ValueError("No solve evidence is available for a replay report")
        path = generate_solve_report(state, output)
    elif kind == "security":
        from vulnclaw.report.generator import generate_report

        if not agent.session_state.target:
            raise ValueError("No target results are available for a security report")
        path = generate_report(
            agent.session_state,
            output,
            report_format=config.session.report_format,
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
    if kind == "replay" and config.session.solve_report_show:
        result["text"] = Path(path).read_text(encoding="utf-8")
    if kind == "security":
        result.update(
            {
                "findings_json": str(Path(path).parent / "findings" / "findings.json"),
                "findings_sarif": str(Path(path).parent / "findings" / "findings.sarif"),
            }
        )
    return result


async def generate_report_for_run(run: Any, *, config: Any, agent: Any) -> dict[str, str]:
    output = None
    if run.run_context is not None:
        output = str(run.run_context.run_dir / "reports" / "report.md")
    return await export_report(agent, config, output=output)
