"""Explicit management commands with machine output and no terminal prompts."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Literal, Optional

import typer

from vulnclaw import experience_service
from vulnclaw.cli.output import CommandError, perform, run_async
from vulnclaw.cli.task_input import read_input_json
from vulnclaw.config.settings import load_config, save_config
from vulnclaw.config_service import masked_config, update_config

MachineFormat = Literal["json", "jsonl"]


def command_group(help: str) -> typer.Typer:
    group = typer.Typer(help=help, no_args_is_help=False)

    @group.callback(invoke_without_command=True)
    def root(ctx: typer.Context):
        if ctx.invoked_subcommand is None:
            raise CommandError(
                "requires_input",
                "Choose an operation; use --help for commands.",
                status="requires_input",
            )

    return group


config_app = command_group("Manage configuration")
kb_app = command_group("Manage the security knowledge base")
experience_app = command_group("Review cross-session experience lessons")
plugins_app = command_group("Inspect and run detection plugins")
target_state_app = command_group("Manage saved target history")


def _config_value(key: str):
    value = masked_config(load_config().model_dump(mode="json"))
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            raise CommandError("invalid_config_key", f"Unknown configuration key: {key}")
        value = value[part]
    return value


@config_app.command("get")
def config_get(key: str, output_format: MachineFormat = typer.Option("json", "--format")):
    perform(lambda: {"key": key, "value": _config_value(key)})


@config_app.command("list")
def config_list(output_format: MachineFormat = typer.Option("json", "--format")):
    perform(lambda: {"config": masked_config(load_config().model_dump(mode="json"))})


@config_app.command("set")
def config_set(
    key: str, value: str, output_format: MachineFormat = typer.Option("json", "--format")
):
    def operation():
        config = load_config()
        existing = config.model_dump(mode="json")
        for part in key.split("."):
            existing = existing[part]
        if isinstance(existing, list):
            parsed = (
                json.loads(value)
                if value.lstrip().startswith("[")
                else [item.strip() for item in value.replace("\n", ",").split(",") if item.strip()]
            )
        elif isinstance(existing, str):
            parsed = value
        else:
            try:
                parsed = json.loads(value)
            except ValueError:
                parsed = value
        updated = update_config({key: parsed}, config=config)
        return {"key": key, "config": masked_config(updated.model_dump(mode="json"))}

    perform(operation)


@config_app.command("provider")
def config_provider(
    name: Optional[str] = typer.Argument(None),
    list_all: bool = typer.Option(False, "--list", "-l"),
    output_format: MachineFormat = typer.Option("json", "--format"),
):
    def operation():
        from vulnclaw.config.settings import apply_provider_preset, list_providers

        config = load_config()
        if name is not None and not list_all:
            config = apply_provider_preset(config, name.lower())
            save_config(config)
        return {
            "current": config.llm.provider,
            "providers": list_providers(),
            "llm": masked_config(config.llm.model_dump(mode="json")),
        }

    perform(operation)


def init(output_format: MachineFormat = typer.Option("json", "--format")):
    def operation():
        from vulnclaw.config.settings import CONFIG_FILE, ensure_dirs

        ensure_dirs()
        save_config(load_config())
        return {"config_path": str(CONFIG_FILE), "initialized": True}

    perform(operation)


def login(
    token_file: Optional[str] = typer.Option(
        None, "--token-file", help="OAuth token bundle JSON, or - for stdin"
    ),
    proxy_url: Optional[str] = typer.Option(None, "--proxy-url"),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Accepted for migration; headless never opens a browser"
    ),
    set_default: bool = typer.Option(True, "--set-default/--no-set-default"),
    output_format: MachineFormat = typer.Option("json", "--format"),
):
    def operation():
        from vulnclaw.config.token_provider import (
            CHATGPT_CLIENT_ID,
            CHATGPT_TOKEN_URL,
            save_oauth_tokens,
        )

        if not token_file:
            raise CommandError(
                "requires_input",
                "Provide --token-file containing an OAuth token bundle; headless login does not start browser consent.",
                status="requires_input",
            )
        bundle = read_input_json(token_file)
        if not any(
            isinstance(bundle.get(key), str) and bundle[key].strip()
            for key in ("access_token", "refresh_token")
        ):
            raise CommandError(
                "invalid_token_bundle", "A non-empty access_token or refresh_token is required."
            )
        changes = {
            "llm.oauth_token_url": CHATGPT_TOKEN_URL,
            "llm.oauth_client_id": CHATGPT_CLIENT_ID,
            "llm.chatgpt_auto_proxy": not bool(proxy_url),
        }
        if set_default:
            changes["llm.auth_mode"] = "oauth"
        config = load_config()
        if (config.llm.model or "").lower() in {"", "gpt-4o", "gpt-5", "gpt-5-codex"}:
            changes["llm.model"] = "gpt-5.5"
        if proxy_url:
            changes["llm.base_url"] = proxy_url.rstrip("/")
        update_config(changes, config=config)
        save_oauth_tokens(bundle)
        return {
            "authenticated": True,
            "auth_mode": "oauth" if set_default else config.llm.auth_mode,
        }

    perform(operation)


def logout(output_format: MachineFormat = typer.Option("json", "--format")):
    from vulnclaw.config.token_provider import logout_oauth

    perform(lambda: {"removed": logout_oauth()})


def doctor(
    probe_llm: bool = typer.Option(False, "--probe-llm"),
    output_format: MachineFormat = typer.Option("json", "--format"),
):
    def operation():
        from vulnclaw.config.token_provider import has_llm_credentials
        from vulnclaw.mcp.diagnostics import get_mcp_diagnostics

        config = load_config()
        result = {
            "python": sys.version.split()[0],
            "executables": {name: shutil.which(name) for name in ("node", "npx", "uvx", "nmap")},
            "llm": {
                "provider": config.llm.provider,
                "model": config.llm.model,
                "base_url": config.llm.base_url,
                "auth_mode": config.llm.auth_mode,
                "credentials_configured": has_llm_credentials(config.llm),
            },
            "mcp": get_mcp_diagnostics(),
        }
        if probe_llm:
            if not has_llm_credentials(config.llm):
                raise CommandError(
                    "requires_input",
                    "Configure credentials before probing the LLM.",
                    status="requires_input",
                )
            from vulnclaw.config.llm_probe import probe_llm_endpoint

            result["probe"] = probe_llm_endpoint(config.llm)
        return result

    perform(operation)


@kb_app.command("update")
def kb_update(output_format: MachineFormat = typer.Option("json", "--format")):
    def operation():
        from vulnclaw.kb.store import KnowledgeStore
        from vulnclaw.kb.updater import seed_knowledge_base

        store = KnowledgeStore()
        before = store.get_stats()
        seed_knowledge_base(store)
        after = store.get_stats()
        return {"added": sum(after.values()) - sum(before.values()), "categories": after}

    perform(operation)


@kb_app.command("status")
def kb_status(output_format: MachineFormat = typer.Option("json", "--format")):
    def operation():
        from vulnclaw.kb.retriever import KnowledgeRetriever
        from vulnclaw.kb.store import KnowledgeStore

        store = KnowledgeStore()
        retriever = KnowledgeRetriever(store=store)
        return {
            "status": retriever.get_status().value,
            "detail": retriever.get_status_detail(),
            "categories": store.get_stats(),
        }

    perform(operation)


def _experience(result):
    if not result.ok:
        raise CommandError("operation_failed", result.message)
    return {"message": result.message, **result.data}


@experience_app.command("list")
@experience_app.command("review")
def experience_list(output_format: MachineFormat = typer.Option("json", "--format")):
    perform(lambda: _experience(experience_service.list_lessons()))


@experience_app.command("show")
def experience_show(
    lesson_id: str, output_format: MachineFormat = typer.Option("json", "--format")
):
    perform(lambda: _experience(experience_service.get_lesson(lesson_id)))


@experience_app.command("approve")
def experience_approve(
    lesson_id: str, output_format: MachineFormat = typer.Option("json", "--format")
):
    perform(lambda: _experience(experience_service.set_lesson_status(lesson_id, "approved")))


@experience_app.command("reject")
def experience_reject(
    lesson_id: str, output_format: MachineFormat = typer.Option("json", "--format")
):
    perform(lambda: _experience(experience_service.set_lesson_status(lesson_id, "rejected")))


@experience_app.command("edit")
def experience_edit(
    lesson_id: str,
    context: Optional[str] = typer.Option(None, "--context"),
    lesson_text: Optional[str] = typer.Option(None, "--lesson"),
    output_format: MachineFormat = typer.Option("json", "--format"),
):
    perform(
        lambda: _experience(
            experience_service.edit_lesson(lesson_id, context=context, lesson=lesson_text)
        )
    )


def learn(
    run_name: str,
    runs_dir: Optional[str] = typer.Option(None, "--runs-dir"),
    output_format: MachineFormat = typer.Option("json", "--format"),
):
    perform(
        lambda: _experience(
            experience_service.distill_run(run_name, config=load_config(), runs_dir=runs_dir)
        )
    )


def feedback(
    run: str,
    rating: int = typer.Option(..., "--rating", "-r", min=1, max=5),
    notes: str = typer.Option(..., "--notes", "-n"),
    runs_dir: Optional[str] = typer.Option(None, "--runs-dir"),
    output_format: MachineFormat = typer.Option("json", "--format"),
):
    perform(
        lambda: _experience(
            experience_service.save_run_feedback(
                run, rating=rating, notes=notes, config=load_config(), runs_dir=runs_dir
            )
        )
    )


@plugins_app.command("list")
def plugins_list(
    stage: Optional[str] = typer.Option(None, "--stage"),
    tag: Optional[str] = typer.Option(None, "--tag"),
    output_format: MachineFormat = typer.Option("json", "--format"),
):
    def operation():
        from vulnclaw.plugins import registry

        classes = registry.by_stage(stage) if stage else registry.list()
        ids = {plugin.plugin_id for plugin in registry.by_tag(tag)} if tag else None
        return {
            "plugins": [
                plugin.metadata()
                for plugin in sorted(classes, key=lambda p: p.plugin_id)
                if ids is None or plugin.plugin_id in ids
            ]
        }

    perform(operation)


@plugins_app.command("info")
def plugins_info(plugin_id: str, output_format: MachineFormat = typer.Option("json", "--format")):
    def operation():
        from vulnclaw.plugins import registry

        plugin = registry.get(plugin_id)
        if plugin is None:
            raise CommandError("plugin_not_found", f"Plugin not found: {plugin_id}")
        return plugin.metadata()

    perform(operation)


@plugins_app.command("run")
def plugins_run(
    plugin_id: str,
    target: str = typer.Option("", "--target"),
    stage: str = typer.Option("discovery", "--stage"),
    option: Optional[list[str]] = typer.Option(None, "--option", "-o"),
    input_file: Optional[str] = typer.Option(None, "--input"),
    allow_destructive: bool = typer.Option(False, "--allow-destructive"),
    session_file: Optional[str] = typer.Option(None, "--session"),
    as_json: bool = typer.Option(
        False, "--json", help="Accepted for migration; all results are machine-readable"
    ),
    output_format: MachineFormat = typer.Option("json", "--format"),
):
    def operation():
        from vulnclaw.plugins import PluginContext, PluginStage, create_builtin_runtime

        options = {}
        for pair in option or []:
            key, separator, value = pair.partition("=")
            if not separator or not key.strip():
                raise CommandError("invalid_option", "Plugin options must be key=value.")
            try:
                options[key.strip()] = json.loads(value)
            except ValueError:
                options[key.strip()] = value
        if input_file:
            payload = json.loads(Path(input_file).read_text(encoding="utf-8"))
            options.update(payload if isinstance(payload, dict) else {"input": payload})
        context = PluginContext(
            target=target,
            stage=PluginStage(stage),
            options=options,
            allow_destructive=allow_destructive,
        )
        result = run_async(create_builtin_runtime(load_config()).execute(plugin_id, context))
        if session_file:
            from vulnclaw.agent.context import SessionState
            from vulnclaw.plugins.integration import record_plugin_result

            path = Path(session_file)
            session = SessionState.load(path) if path.exists() else SessionState()
            record_plugin_result(session, result, context)
            session.save(path)
        if result.error and not result.skipped:
            raise CommandError(result.error_type or "plugin_failed", result.error)
        return result

    perform(operation)


def _require_state(value, target):
    if not value:
        raise CommandError("state_not_found", f"Saved state not found: {target}")
    return value


@target_state_app.command("list")
def target_state_list(target: str, output_format: MachineFormat = typer.Option("json", "--format")):
    from vulnclaw.target_state.store import list_target_snapshots

    perform(lambda: {"snapshots": _require_state(list_target_snapshots(target), target)})


@target_state_app.command("preview")
def target_state_preview(
    target: str,
    snapshot_id: Optional[str] = typer.Option(None, "--snapshot"),
    output_format: MachineFormat = typer.Option("json", "--format"),
):
    from vulnclaw.target_state.store import get_target_state_preview

    perform(
        lambda: _require_state(get_target_state_preview(target, snapshot_id=snapshot_id), target)
    )


@target_state_app.command("diff")
def target_state_diff(
    target: str,
    from_snapshot_id: str,
    to_snapshot_id: Optional[str] = typer.Option(None, "--to"),
    output_format: MachineFormat = typer.Option("json", "--format"),
):
    from vulnclaw.target_state.store import diff_target_state_snapshots

    perform(
        lambda: _require_state(
            diff_target_state_snapshots(target, from_snapshot_id, to_snapshot_id=to_snapshot_id),
            target,
        )
    )


@target_state_app.command("rollback")
def target_state_rollback(
    target: str, snapshot_id: str, output_format: MachineFormat = typer.Option("json", "--format")
):
    from vulnclaw.target_state.store import rollback_target_state

    perform(
        lambda: {
            "path": str(_require_state(rollback_target_state(target, snapshot_id), target)),
            "snapshot_id": snapshot_id,
        }
    )


@target_state_app.command("clear")
def target_state_clear(
    target: str, output_format: MachineFormat = typer.Option("json", "--format")
):
    from vulnclaw.target_state.store import clear_target_state

    perform(lambda: {"cleared": bool(_require_state(clear_target_state(target), target))})
