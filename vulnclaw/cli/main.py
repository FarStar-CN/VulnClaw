"""Public Python entry point: machine commands and explicit terminal launchers."""

from __future__ import annotations

import os
import sys
from typing import Optional

import typer

from vulnclaw import __version__
from vulnclaw.cli import management, reports, tasks, terminal
from vulnclaw.cli.code import code_app
from vulnclaw.cli.discovery import describe, group_document
from vulnclaw.cli.manual import render_manual
from vulnclaw.cli.output import CommandError, HeadlessGroup, current_output, perform, run_async
from vulnclaw.cli.serving import web


def _configure_windows_console() -> None:
    """Force UTF-8 console I/O on Windows for reliable Unicode output."""
    if sys.platform != "win32":
        return

    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleCP(65001)
        kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass

    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


_configure_windows_console()

app = typer.Typer(
    cls=HeadlessGroup,
    help="VulnClaw: headless security workflows; explicit tui/repl for interactive use",
    no_args_is_help=False,
)
for name in ("run", "recon", "scan", "exploit", "persistent", "codescan"):
    app.command(name)(tasks.run)
for name, command in {
    "solve": tasks.solve,
    "network-scan": tasks.network_scan,
    "ask": tasks.ask,
    "tui": terminal.tui,
    "repl": terminal.repl,
    "web": web,
    "verify": reports.verify,
    "report": reports.report,
    **{
        name: getattr(management, name)
        for name in ("init", "login", "logout", "doctor", "learn", "feedback")
    },
}.items():
    app.command(name)(command)
for name in ("config", "kb", "experience", "plugins", "target_state"):
    app.add_typer(getattr(management, f"{name}_app"), name=name.replace("_", "-"))
app.add_typer(code_app, name="code")
app.command("describe")(describe)


def manual(
    topic: Optional[str] = typer.Argument(None),
    output_format: str = typer.Option("text", "--format", "-f"),
):
    """Read the packaged command manual (text, markdown or man)."""
    sys.stdout.write(render_manual(output_format, topic))


app.command("manual")(manual)
app.command("man")(manual)


def _resume_saved_run(name: str, runs_dir: str | None, repair: bool):
    from vulnclaw.application import task_result
    from vulnclaw.cli.execution import HeadlessExecutor
    from vulnclaw.config.settings import load_config
    from vulnclaw.config.token_provider import has_llm_credentials
    from vulnclaw.input_service import InputRequest
    from vulnclaw.run_context import load_run_context
    from vulnclaw.task_service import TaskCreateRequest, TaskExecution, prepare_task

    config = load_config()
    context = load_run_context(name, runs_dir=runs_dir, config=config, repair=repair)
    saved = context.target_manifest()
    if not saved.get("input"):
        raise CommandError("invalid_saved_run", "The saved run has no resumable target.")
    if not has_llm_credentials(config.llm):
        raise CommandError(
            "requires_input", "Configure LLM credentials before resuming.", status="requires_input",
            path=["llm", "credentials"],
        )
    target = str(saved["input"])
    request = TaskCreateRequest(
        command="run", target=target, resume_run_name=name, runs_dir=runs_dir, repair=repair
    )

    async def direct(application, sink):
        action = None

        async def runner(agent):
            nonlocal action
            action = await agent.chat(
                f"Continue the saved VulnClaw run {name} for {target}. Resume from the loaded checkpoint and continue the authorized task.",
                target=target,
                stream_sink=sink,
            )

        run = await application.run_orchestrated(
            command=str(context.manifest.get("command") or "run"),
            target=target,
            resume=True,
            resume_run_name=name,
            runs_dir=runs_dir,
            repair=repair,
            runner=runner,
        )
        result = task_result(application.runtime, prepare_task(request), TaskExecution(run, action))
        result["command"] = str(context.manifest.get("command") or "run")
        return result

    return run_async(
        HeadlessExecutor(current_output()).execute(InputRequest(task=request), direct=direct)
    )


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", is_eager=True),
    show_manual: bool = typer.Option(False, "--man", "--manual", is_eager=True),
    resume_run_name: Optional[str] = typer.Option(None, "--resume"),
    runs_dir: Optional[str] = typer.Option(None, "--runs-dir"),
    repair: bool = typer.Option(False, "--repair"),
    output_format: management.MachineFormat = typer.Option("json", "--format"),
):
    if version:
        typer.echo(__version__)
        raise typer.Exit()
    if show_manual:
        sys.stdout.write(render_manual("text", None))
        raise typer.Exit()
    if resume_run_name:
        if ctx.invoked_subcommand is not None:
            raise CommandError(
                "invalid_arguments", "Root --resume cannot be combined with a subcommand."
            )
        perform(lambda: _resume_saved_run(resume_run_name, runs_dir, repair))
    if ctx.invoked_subcommand is None:
        perform(lambda: group_document(ctx.command, []))


if __name__ == "__main__":
    app()
