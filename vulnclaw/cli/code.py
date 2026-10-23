"""Local code scanning; --format describes the artifact, --output-format the CLI."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal, Optional
from uuid import uuid4

import typer

from vulnclaw import headless
from vulnclaw.cli.management import MachineFormat, command_group
from vulnclaw.cli.output import CommandError, current_output, perform

code_app = command_group("Local source-code security scanning")


@code_app.command("scan")
def code_scan(
    path: str,
    layer: str = typer.Option("all", "--layer"),
    artifact_format: Literal["text", "json", "sarif", "markdown", "md"] = typer.Option(
        "text", "--format", "-f"
    ),
    output: Optional[str] = typer.Option(None, "--output", "-o"),
    stream: bool = typer.Option(False, "--stream", help="Alias for --output-format jsonl"),
    no_progress: bool = typer.Option(False, "--no-progress"),
    output_format: MachineFormat = typer.Option("json", "--output-format"),
    fail_on: Literal["verified", "any", "never"] = typer.Option("verified", "--fail-on"),
):
    def operation():
        from vulnclaw.codescan.report import format_result
        from vulnclaw.codescan.scanner import scan_code
        from vulnclaw.config.settings import RUNS_DIR

        if not Path(path).exists():
            raise CommandError("path_not_found", f"Path not found: {path}")
        layers = _parse_layers(layer)
        if layers is None:
            raise CommandError("invalid_layer", "--layer must contain L1, L2, L3 or all")
        task_id = f"codescan-{uuid4().hex}"
        result = scan_code(
            path,
            layers=layers,
            progress=None
            if no_progress
            else lambda file, count: current_output().event(
                "status",
                task_id=task_id,
                request_id=current_output().request_id,
                status=f"{file} ({count} findings)",
            ),
        )
        if "L3" in layers:
            client = _resolve_llm_client()
            if client is not None:
                from vulnclaw.codescan.scanner import scan_code_llm

                result.findings = scan_code_llm(path, findings=result.findings, llm_client=client)
        extensions = {"text": "txt", "json": "json", "sarif": "sarif", "markdown": "md", "md": "md"}
        destination = (
            Path(output)
            if output
            else headless.run_directory(RUNS_DIR, path, f"codescan-{uuid4().hex}")
            / f"codescan.{extensions[artifact_format]}"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            format_result(result, artifact_format), encoding="utf-8", newline="\n"
        )
        classification = headless.FindingClassification(0, len(result.findings))
        return current_output().finish(
            result=result,
            exit_code=headless.determine_exit_code(classification, fail_on),
            findings={"verified": 0, "candidates": len(result.findings)},
            artifacts=[{"kind": "codescan", "path": str(destination.resolve())}],
        )

    perform(operation)


def _parse_layers(layer: str) -> set[str] | None:
    if layer.upper() == "ALL":
        return {"L1", "L2"}
    parts = {part.upper() for part in layer.replace(",", " ").split()}
    return parts if parts and parts <= {"L1", "L2", "L3"} else None


def _resolve_llm_client():
    """Preserve L3's best-effort opt-in behavior; diagnostics stay on stderr."""
    try:
        from openai import OpenAI

        from vulnclaw.config.settings import load_config
        from vulnclaw.config.token_provider import resolve_llm_token

        llm = load_config().llm
        client = OpenAI(api_key=resolve_llm_token(llm), base_url=llm.base_url or None)

        class SyncLLM:
            def complete(self, prompt: str, *, temperature: float = 0.2) -> str:
                response = client.chat.completions.create(
                    model=llm.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=temperature,
                )
                return response.choices[0].message.content or ""

        return SyncLLM()
    except Exception as exc:
        print(f"L3 unavailable; skipping semantic review: {exc}", file=sys.stderr)
        return None
