"""Launch the shared OpenTUI executable; headless commands never import it."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer

from vulnclaw.cli.output import CommandError, perform
from vulnclaw.config.domain_models import ACTION_PRESETS
from vulnclaw.task_service import TaskCreateRequest


def _binary() -> str:
    override = os.environ.get("VULNCLAW_TERMINAL_BINARY")
    if override:
        return override
    if binary := shutil.which("vulnclaw-terminal"):
        return binary
    local = (
        Path(__file__).resolve().parents[2]
        / "tui"
        / "dist"
        / ("vulnclaw-terminal.exe" if os.name == "nt" else "vulnclaw-terminal")
    )
    if local.is_file():
        return str(local)
    raise CommandError(
        "terminal_not_installed",
        "Install the platform's vulnclaw-terminal executable or build tui/ with Bun; set VULNCLAW_TERMINAL_BINARY to its path. Headless commands only require Python.",
    )


def launch(mode: str, *, bootstrap: dict | None = None) -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise CommandError(
            "terminal_required",
            "An interactive terminal is required; use headless commands for Agent/CI.",
        )
    env = os.environ.copy()
    env["VULNCLAW_PYTHON"] = sys.executable
    env["VULNCLAW_TERMINAL_BOOTSTRAP"] = json.dumps(bootstrap or {}, ensure_ascii=False)
    process = subprocess.Popen([_binary(), "--mode", mode], env=env)
    try:
        code = process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        code = 1
    raise typer.Exit(code if code >= 0 else 1)


def repl():
    """Open the OpenTUI conversation interface."""
    launch("repl")


def tui(
    target: Optional[str] = typer.Option(None, "--target", "-t"),
    mode: str = typer.Option("standard", "--mode", "-m"),
    only_port: Optional[int] = typer.Option(None, "--only-port", min=1, max=65535),
    only_host: Optional[str] = typer.Option(None, "--only-host"),
    only_path: Optional[str] = typer.Option(None, "--only-path"),
    blocked_host: Optional[str] = typer.Option(None, "--blocked-host"),
    blocked_path: Optional[str] = typer.Option(None, "--blocked-path"),
    allow_actions: Optional[str] = typer.Option(None, "--allow-actions"),
    block_actions: Optional[str] = typer.Option(None, "--block-actions"),
    resume: bool = typer.Option(True, "--resume/--no-resume"),
    dry_run: bool = typer.Option(False, "--dry-run"),
):
    """Open the OpenTUI workbench with an optional task draft."""
    commands = {"quick": "recon", "standard": "run", "deep": "run", "continuous": "persistent"}
    if mode not in commands:
        raise CommandError("invalid_mode", "--mode must be quick, standard, deep or continuous")
    preset = ACTION_PRESETS.get(mode.capitalize(), ())
    bootstrap = {
        "target": (target or "").strip(),
        "mode": mode,
        "command": commands[mode],
        "only_host": only_host,
        "only_port": only_port,
        "only_path": only_path,
        "blocked_host": blocked_host,
        "blocked_path": blocked_path,
        "allow_actions": allow_actions.split(",") if allow_actions else list(preset),
        "block_actions": block_actions.split(",") if block_actions else [],
        "resume": resume,
    }
    # Validate constraints without starting a runtime or requiring a target for an empty form.
    task = TaskCreateRequest(
        command=commands[mode],
        target=target or "draft.invalid",
        resume=resume,
        options={
            key: value
            for key, value in bootstrap.items()
            if key not in {"target", "mode", "command", "resume"}
        },
    )
    if dry_run:
        perform(
            lambda: {
                "bootstrap": bootstrap,
                "task": task.model_dump(mode="json") if target else None,
            }
        )
    launch("tui", bootstrap=bootstrap)
