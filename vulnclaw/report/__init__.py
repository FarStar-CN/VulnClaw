"""VulnClaw Report Module — structured report generation with vulnerability verification."""

from vulnclaw.report.filter import ReportContentFilter, filter_report_content
from vulnclaw.report.findings_output import (
    build_findings_document,
    is_report_included,
    to_sarif,
    write_findings_artifacts,
)
from vulnclaw.report.generator import (
    generate_persistent_cycle_report,
    generate_report,
    generate_report_from_file,
)

__all__ = [
    # Generator
    "generate_report",
    "generate_report_from_file",
    "generate_persistent_cycle_report",
    # Filter
    "ReportContentFilter",
    "filter_report_content",
    # Structured findings output (findings.json + SARIF)
    "build_findings_document",
    "to_sarif",
    "write_findings_artifacts",
    "is_report_included",
]
