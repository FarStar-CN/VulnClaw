"""Headless report and independent finding audit adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from vulnclaw.cli.execution import execute_service
from vulnclaw.cli.management import MachineFormat
from vulnclaw.cli.output import perform, run_async
from vulnclaw.config.settings import load_config
from vulnclaw.i18n import init_i18n


def verify(
    session: str,
    target_mode: bool = typer.Option(False, "--target"),
    poc_verify: Optional[bool] = typer.Option(None, "--poc-verify/--no-poc-verify"),
    output_format: MachineFormat = typer.Option("json", "--format"),
):
    def operation():
        from vulnclaw.finding_service import assessment_summary, verify_session_file, verify_target

        config = load_config()
        init_i18n(config=config)

        async def audit():
            if target_mode:
                view, path, _raw = await verify_target(
                    session, config=config, poc_verify=poc_verify
                )
            else:
                view, path = await verify_session_file(
                    session, config=config, poc_verify=poc_verify
                )
            return {"path": str(path), "summary": assessment_summary(view)}

        return run_async(execute_service(audit, config))

    perform(operation)


def report(
    session: str,
    target_mode: bool = typer.Option(False, "--target"),
    pdf: bool = typer.Option(False, "--pdf"),
    pdf_out: str = typer.Option("", "--pdf-out"),
    output_format: MachineFormat = typer.Option("json", "--format"),
):
    def operation():
        from vulnclaw.report_service import generate_report_for_target, generate_report_from_file

        config = load_config()
        init_i18n(config=config)

        async def generate():
            path = await (
                generate_report_for_target(session, config=config)
                if target_mode
                else generate_report_from_file(session, config=config)
            )
            artifacts = [{"kind": "report", "path": str(path)}]
            if pdf:
                from vulnclaw.report.pdf_exporter import export_pdf

                out = Path(pdf_out) if pdf_out else Path(path).with_suffix(".pdf")
                export_pdf(Path(path).read_text(encoding="utf-8"), out, title="VulnClaw Report")
                artifacts.append({"kind": "pdf", "path": str(out)})
            return {"artifacts": artifacts}

        return run_async(execute_service(generate, config))

    perform(operation)
