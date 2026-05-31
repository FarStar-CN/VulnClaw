"""Rich-powered TUI helpers for the VulnClaw CLI."""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable, Literal

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from vulnclaw.config.settings import apply_provider_preset, list_providers, load_config, save_config
from vulnclaw.target_state.store import get_target_state_preview, list_target_snapshots

CheckMode = Literal["quick", "standard", "deep", "continuous"]
TaskCommand = Literal["recon", "run", "scan", "persistent"]


@dataclass(frozen=True)
class TuiMode:
    key: CheckMode
    label: str
    command: TaskCommand
    description: str
    allow_actions: tuple[str, ...]
    block_actions: tuple[str, ...] = ()
    needs_extra_confirm: bool = False


@dataclass
class TuiState:
    target: str = ""
    mode: CheckMode = "standard"
    only_host: str = ""
    only_port: str = ""
    only_path: str = ""
    blocked_host: str = ""
    blocked_path: str = ""
    allow_actions: list[str] = field(default_factory=list)
    block_actions: list[str] = field(default_factory=list)
    resume: bool = True


@dataclass(frozen=True)
class TuiTargetOverview:
    """Small, safe-to-render summary of the selected target history."""

    target: str
    has_history: bool
    snapshot_count: int = 0
    phase: str = "unknown"
    findings_count: int = 0
    verified_count: int = 0
    pending_count: int = 0
    constraints_summary: str = "未记录"
    violations_count: int = 0
    last_command: str = ""
    error: str = ""


@dataclass(frozen=True)
class TuiRuntimeDiagnostic:
    """Runtime readiness summary shown inside the TUI."""

    python_version: str
    node_version: str = "missing"
    npx_status: str = "missing"
    uvx_status: str = "missing"
    nmap_status: str = "optional/missing"
    provider: str = "unknown"
    model: str = "unknown"
    api_key_configured: bool = False
    mcp_total_services: int = 0
    mcp_running_services: int = 0
    mcp_local_services: int = 0
    mcp_placeholder_services: int = 0
    mcp_tool_count: int = 0
    mcp_error: str = ""


@dataclass(frozen=True)
class TuiTaskDraft:
    command: TaskCommand
    target: str
    only_host: str | None = None
    only_port: int | None = None
    only_path: str | None = None
    blocked_host: str | None = None
    blocked_path: str | None = None
    allow_actions: tuple[str, ...] = ()
    block_actions: tuple[str, ...] = ()
    resume: bool = True

    @property
    def command_line(self) -> str:
        """Return a copyable command line for the current draft."""
        return " ".join(build_command_preview_args(self))


TaskLauncher = Callable[[TuiTaskDraft], None]


MODES: dict[CheckMode, TuiMode] = {
    "quick": TuiMode(
        key="quick",
        label="快速摸底",
        command="recon",
        description="只做信息收集和基础风险识别，适合第一次了解目标。",
        allow_actions=("recon",),
        block_actions=("exploit", "persistent", "post_exploitation"),
    ),
    "standard": TuiMode(
        key="standard",
        label="标准检查",
        command="run",
        description="信息收集 + 风险发现，默认推荐，不主动做高风险验证。",
        allow_actions=("recon", "scan"),
        block_actions=("post_exploitation",),
    ),
    "deep": TuiMode(
        key="deep",
        label="深度验证",
        command="scan",
        description="更深入地验证风险，需要再次确认授权范围。",
        allow_actions=("recon", "scan", "exploit"),
        needs_extra_confirm=True,
    ),
    "continuous": TuiMode(
        key="continuous",
        label="持续检查",
        command="persistent",
        description="多轮持续运行，适合靶场或长期观察目标。",
        allow_actions=("recon", "scan"),
        block_actions=("post_exploitation",),
        needs_extra_confirm=True,
    ),
}

MENU_ITEMS = {
    "1": "设置授权目标",
    "2": "选择检查模式",
    "3": "设置测试范围",
    "4": "开始授权安全检查",
    "5": "查看历史状态摘要",
    "6": "生成目标报告",
    "7": "环境诊断入口",
    "8": "模型/API 配置",
    "q": "退出",
}


def render_tui_home(state: TuiState | None = None, *, width: int = 110) -> str:
    """Render the TUI home surface into plain text for tests and dry-runs."""
    console = Console(
        file=io.StringIO(),
        record=True,
        width=width,
        force_terminal=False,
        color_system=None,
    )
    config = load_config()
    console.print(build_dashboard(config, state or TuiState()))
    return console.export_text()


def build_state_from_options(
    *,
    target: str = "",
    mode: CheckMode = "standard",
    only_host: str = "",
    only_port: str | int | None = "",
    only_path: str = "",
    blocked_host: str = "",
    blocked_path: str = "",
    allow_actions: str | tuple[str, ...] | list[str] | None = None,
    block_actions: str | tuple[str, ...] | list[str] | None = None,
    resume: bool = True,
) -> TuiState:
    """Build a TUI state object from CLI flags or tests."""
    return TuiState(
        target=target.strip(),
        mode=mode,
        only_host=only_host.strip(),
        only_port=str(only_port or "").strip(),
        only_path=only_path.strip(),
        blocked_host=blocked_host.strip(),
        blocked_path=blocked_path.strip(),
        allow_actions=_parse_action_csv(allow_actions),
        block_actions=_parse_action_csv(block_actions),
        resume=resume,
    )


def build_dashboard(config, state: TuiState) -> Group:
    """Build the first-screen VulnClaw TUI dashboard."""
    mode = MODES[state.mode]
    provider = getattr(config.llm, "provider", "unknown")
    model = getattr(config.llm, "model", "unknown")
    api_ready = bool(getattr(config.llm, "api_key", ""))
    overview = build_target_overview(state.target)

    title = Text("VulnClaw TUI 工作台", style="bold cyan")
    subtitle = Text(
        "面向授权安全测试的终端图形化入口：先确认目标和边界，再启动检查。",
        style="dim",
    )
    header = Panel(
        Align.left(Group(title, subtitle)),
        border_style="cyan",
        box=box.ROUNDED,
        padding=(1, 2),
    )

    status = Table.grid(expand=True)
    status.add_column(ratio=1)
    status.add_column(ratio=1)
    status.add_column(ratio=1)
    status.add_row(
        _metric_panel("授权目标", state.target or "未设置", "yellow" if not state.target else "green"),
        _metric_panel("检查模式", f"{mode.label} / {mode.command}", "cyan"),
        _metric_panel("AI 模型", f"{provider} · {model}", "green" if api_ready else "yellow"),
    )

    scope_table = Table(box=box.SIMPLE_HEAVY, expand=True, show_header=True)
    scope_table.add_column("测试范围", style="bold")
    scope_table.add_column("当前值")
    scope_table.add_row("仅测试主机", state.only_host or "由目标自动推断 / 未限制")
    scope_table.add_row("仅测试端口", state.only_port or "未限制")
    scope_table.add_row("仅测试路径", state.only_path or "未限制")
    scope_table.add_row("排除主机", state.blocked_host or "未设置")
    scope_table.add_row("排除路径", state.blocked_path or "未设置")
    scope_table.add_row("允许动作", ", ".join(_effective_allow_actions(state)) or "未设置")
    scope_table.add_row("禁止动作", ", ".join(_effective_block_actions(state)) or "未设置")

    overview_table = Table(box=box.SIMPLE_HEAVY, expand=True, show_header=True)
    overview_table.add_column("工作台概览", style="bold")
    overview_table.add_column("当前状态")
    overview_table.add_row("模型密钥", "已配置" if api_ready else "未配置")
    overview_table.add_row("历史沿用", "开启" if state.resume else "关闭")
    overview_table.add_row("目标历史", _format_target_history_line(overview))
    overview_table.add_row("风险概览", _format_findings_line(overview))
    overview_table.add_row("持久约束", overview.constraints_summary)
    overview_table.add_row("约束拦截", f"{overview.violations_count} 次")
    if overview.last_command:
        overview_table.add_row("上次命令", overview.last_command)
    if overview.error:
        overview_table.add_row("历史读取", overview.error)

    menu = Table(box=box.MINIMAL_HEAVY_HEAD, expand=True, show_header=False)
    menu.add_column("Key", style="bold cyan", width=6)
    menu.add_column("Action")
    for key, label in MENU_ITEMS.items():
        menu.add_row(key, label)

    command_preview = _draft_from_state(state).command_line
    footer = Panel(
        f"[bold]命令预览[/]\n{command_preview}\n\n"
        "[dim]原有 CLI / REPL 仍是默认入口；TUI 仅在显式运行 vulnclaw tui 时启用。[/]",
        title="启动前确认",
        border_style="green" if state.target else "yellow",
        box=box.ROUNDED,
    )

    return Group(
        header,
        status,
        Panel(overview_table, title="运行概览", border_style="blue", box=box.ROUNDED),
        Panel(scope_table, title="安全边界", border_style="green", box=box.ROUNDED),
        Panel(menu, title="操作菜单", border_style="cyan", box=box.ROUNDED),
        footer,
    )


def run_tui(
    *,
    launcher: TaskLauncher | None = None,
    once: bool = False,
    initial_state: TuiState | None = None,
) -> None:
    """Run the interactive terminal UI loop."""
    state = initial_state or TuiState()
    config = load_config()
    active_launcher = launcher or _default_launcher
    screen = Console()

    while True:
        screen.clear()
        screen.print(build_dashboard(config, state))

        if once:
            return

        choice = Prompt.ask(
            "选择操作",
            choices=list(MENU_ITEMS.keys()),
            default="1" if not state.target else "4",
        )
        if choice == "q":
            screen.print("[dim]已退出 VulnClaw TUI。[/]")
            return
        if choice == "1":
            _prompt_target(state)
        elif choice == "2":
            _prompt_mode(state)
        elif choice == "3":
            _prompt_scope(state)
        elif choice == "4":
            _confirm_and_launch(state, active_launcher)
        elif choice == "5":
            _show_target_history(screen, state)
        elif choice == "6":
            _generate_target_report(screen, state)
        elif choice == "7":
            screen.print(build_runtime_diagnostic_panel(config))
            Prompt.ask("按 Enter 返回", default="")
        elif choice == "8":
            config = _prompt_llm_config(screen, config)


def render_task_summary(draft: TuiTaskDraft, *, width: int = 100) -> str:
    """Render a launch summary for dry-run output and tests."""
    console = Console(
        file=io.StringIO(),
        record=True,
        width=width,
        force_terminal=False,
        color_system=None,
    )
    console.print(_build_task_summary_panel(draft))
    return console.export_text()


def build_task_draft(state: TuiState) -> TuiTaskDraft:
    """Public wrapper for converting TUI state into an executable task draft."""
    return _draft_from_state(state)


def build_target_overview(target: str) -> TuiTargetOverview:
    """Build a safe target-history overview for the TUI dashboard."""
    normalized = target.strip()
    if not normalized:
        return TuiTargetOverview(target="", has_history=False)

    try:
        preview = get_target_state_preview(normalized)
        snapshots = list_target_snapshots(normalized)
    except Exception as exc:
        return TuiTargetOverview(
            target=normalized,
            has_history=False,
            error=f"读取失败: {exc}",
        )

    if preview is None:
        return TuiTargetOverview(target=normalized, has_history=False)

    violations = preview.get("constraint_violations", [])
    if not isinstance(violations, list):
        violations = []

    return TuiTargetOverview(
        target=str(preview.get("target") or normalized),
        has_history=True,
        snapshot_count=len(snapshots),
        phase=str(preview.get("phase") or "unknown"),
        findings_count=_safe_int(preview.get("findings_count")),
        verified_count=_safe_int(preview.get("verified_count")),
        pending_count=_safe_int(preview.get("pending_count")),
        constraints_summary=_format_constraints_summary(preview.get("constraints")),
        violations_count=len(violations),
        last_command=str(preview.get("last_command") or ""),
    )


def build_runtime_diagnostic(config) -> TuiRuntimeDiagnostic:
    """Collect runtime readiness without leaving the TUI."""
    provider = str(getattr(config.llm, "provider", "unknown"))
    model = str(getattr(config.llm, "model", "unknown"))
    api_key_configured = bool(getattr(config.llm, "api_key", ""))

    node_version = _command_version("node", "--version") or "missing"
    npx_status = "installed" if shutil.which("npx") else "missing"
    uvx_status = "installed" if shutil.which("uvx") else "missing"
    nmap_status = "installed" if shutil.which("nmap") else "optional/missing"

    try:
        from vulnclaw.web.services.mcp_service import get_mcp_diagnostics

        mcp_diag = get_mcp_diagnostics()
        return TuiRuntimeDiagnostic(
            python_version=sys.version.split()[0],
            node_version=node_version,
            npx_status=npx_status,
            uvx_status=uvx_status,
            nmap_status=nmap_status,
            provider=provider,
            model=model,
            api_key_configured=api_key_configured,
            mcp_total_services=mcp_diag.total_services,
            mcp_running_services=mcp_diag.running_services,
            mcp_local_services=mcp_diag.local_services,
            mcp_placeholder_services=mcp_diag.placeholder_services,
            mcp_tool_count=mcp_diag.tool_count,
        )
    except Exception as exc:
        return TuiRuntimeDiagnostic(
            python_version=sys.version.split()[0],
            node_version=node_version,
            npx_status=npx_status,
            uvx_status=uvx_status,
            nmap_status=nmap_status,
            provider=provider,
            model=model,
            api_key_configured=api_key_configured,
            mcp_error=f"MCP 诊断失败: {exc}",
        )


def build_runtime_diagnostic_panel(config) -> Panel:
    """Render the runtime diagnostic panel used by menu item 7."""
    diagnostic = build_runtime_diagnostic(config)
    table = Table(box=box.SIMPLE_HEAVY, expand=True, show_header=True)
    table.add_column("检查项", style="bold")
    table.add_column("状态")
    table.add_row("Python", diagnostic.python_version)
    table.add_row("Node.js", diagnostic.node_version)
    table.add_row("npx", diagnostic.npx_status)
    table.add_row("uvx", diagnostic.uvx_status)
    table.add_row("nmap", diagnostic.nmap_status)
    table.add_row("LLM Provider", diagnostic.provider)
    table.add_row("LLM Model", diagnostic.model)
    table.add_row("API Key", "已配置" if diagnostic.api_key_configured else "未配置")
    table.add_row(
        "MCP Services",
        (
            f"{diagnostic.mcp_total_services} registered / "
            f"{diagnostic.mcp_running_services} running / "
            f"{diagnostic.mcp_local_services} local / "
            f"{diagnostic.mcp_placeholder_services} placeholder"
        ),
    )
    table.add_row("MCP Tools", str(diagnostic.mcp_tool_count))
    if diagnostic.mcp_error:
        table.add_row("MCP Error", diagnostic.mcp_error)

    footer = (
        "\n[dim]这是一份 TUI 内联诊断摘要；需要完整细节时仍可运行 "
        "[bold]vulnclaw doctor[/]。[/]"
    )
    return Panel(Group(table, Text.from_markup(footer)), title="环境诊断", border_style="cyan")


def _command_version(command: str, *args: str) -> str:
    path = shutil.which(command)
    if not path:
        return ""
    try:
        result = subprocess.run(
            [path, *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return "check failed"
    return (result.stdout or result.stderr).strip() or "installed"


def _metric_panel(label: str, value: str, style: str) -> Panel:
    return Panel(
        f"[dim]{label}[/]\n[bold {style}]{value}[/]",
        box=box.ROUNDED,
        border_style=style,
    )


def _safe_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _format_target_history_line(overview: TuiTargetOverview) -> str:
    if not overview.target:
        return "未选择目标"
    if overview.error:
        return "读取失败"
    if not overview.has_history:
        return "暂无历史"
    return f"{overview.snapshot_count} 个快照 / 阶段 {overview.phase}"


def _format_findings_line(overview: TuiTargetOverview) -> str:
    if not overview.has_history:
        return "暂无风险记录"
    return (
        f"{overview.findings_count} 个风险 "
        f"(已验证 {overview.verified_count} / 待处理 {overview.pending_count})"
    )


def _format_constraints_summary(raw: object) -> str:
    if not isinstance(raw, dict) or not raw:
        return "未记录"

    parts: list[str] = []
    mapping = [
        ("allowed_hosts", "限定主机"),
        ("allowed_ports", "限定端口"),
        ("allowed_paths", "限定路径"),
        ("blocked_hosts", "排除主机"),
        ("blocked_paths", "排除路径"),
        ("allowed_actions", "允许动作"),
        ("blocked_actions", "禁止动作"),
    ]
    for key, label in mapping:
        value = raw.get(key)
        if isinstance(value, list) and value:
            parts.append(f"{label}: {', '.join(str(item) for item in value)}")
        elif value:
            parts.append(f"{label}: {value}")

    if raw.get("strict_mode"):
        parts.append("严格模式")

    return "；".join(parts) if parts else "未记录"


def _effective_allow_actions(state: TuiState) -> tuple[str, ...]:
    return tuple(state.allow_actions) or MODES[state.mode].allow_actions


def _effective_block_actions(state: TuiState) -> tuple[str, ...]:
    return tuple(state.block_actions) or MODES[state.mode].block_actions


def _parse_action_csv(value: str | tuple[str, ...] | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def _parse_optional_port(value: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    try:
        port = int(value)
    except ValueError as exc:
        raise ValueError("端口必须是 1-65535 之间的数字") from exc
    if port < 1 or port > 65535:
        raise ValueError("端口必须是 1-65535 之间的数字")
    return port


def _draft_from_state(state: TuiState) -> TuiTaskDraft:
    mode = MODES[state.mode]
    return TuiTaskDraft(
        command=mode.command,
        target=state.target.strip() or "<target>",
        only_host=state.only_host.strip() or None,
        only_port=_parse_optional_port(state.only_port),
        only_path=state.only_path.strip() or None,
        blocked_host=state.blocked_host.strip() or None,
        blocked_path=state.blocked_path.strip() or None,
        allow_actions=_effective_allow_actions(state),
        block_actions=_effective_block_actions(state),
        resume=state.resume,
    )


def _build_command_preview_args(draft: TuiTaskDraft) -> list[str]:
    return build_command_preview_args(draft)


def build_command_preview_args(draft: TuiTaskDraft) -> list[str]:
    """Build a copyable CLI command from a TUI task draft."""
    args = ["vulnclaw", draft.command, draft.target]
    if not draft.resume:
        args.append("--no-resume")
    if draft.only_port is not None:
        args.extend(["--only-port", str(draft.only_port)])
    if draft.only_host:
        args.extend(["--only-host", draft.only_host])
    if draft.only_path:
        args.extend(["--only-path", draft.only_path])
    if draft.blocked_host:
        args.extend(["--blocked-host", draft.blocked_host])
    if draft.blocked_path:
        args.extend(["--blocked-path", draft.blocked_path])
    if draft.allow_actions:
        args.extend(["--allow-actions", ",".join(draft.allow_actions)])
    if draft.block_actions:
        args.extend(["--block-actions", ",".join(draft.block_actions)])
    return args


def _prompt_target(state: TuiState) -> None:
    state.target = Prompt.ask("输入已授权目标 URL / 域名 / IP", default=state.target).strip()


def _prompt_mode(state: TuiState) -> None:
    choices = list(MODES.keys())
    table = Table(title="检查模式", box=box.SIMPLE)
    table.add_column("Key", style="bold cyan")
    table.add_column("名称")
    table.add_column("说明")
    for key in choices:
        mode = MODES[key]
        table.add_row(key, mode.label, mode.description)
    Console().print(table)
    state.mode = Prompt.ask("选择模式", choices=choices, default=state.mode)  # type: ignore[assignment]


def _prompt_llm_config(screen: Console, config):
    provider_table = Table(title="可用模型提供商", box=box.SIMPLE)
    provider_table.add_column("Provider", style="bold cyan")
    provider_table.add_column("Default Model")
    provider_table.add_column("Base URL")
    for item in list_providers():
        marker = " *" if item["provider"] == config.llm.provider else ""
        provider_table.add_row(
            f"{item['provider']}{marker}",
            item.get("default_model", ""),
            item.get("base_url", ""),
        )
    screen.print(provider_table)

    provider = Prompt.ask(
        "选择 Provider（留空保持当前）",
        default=config.llm.provider,
    ).strip()
    if provider and provider != config.llm.provider:
        config = apply_provider_preset(config, provider)

    base_url = Prompt.ask("Base URL", default=config.llm.base_url).strip()
    model = Prompt.ask("Model", default=config.llm.model).strip()
    current_key = "已配置，留空保持不变" if config.llm.api_key else "未配置"
    api_key = Prompt.ask("API Key（留空保持不变）", default="").strip()

    if base_url:
        config.llm.base_url = base_url
    if model:
        config.llm.model = model
    if api_key:
        config.llm.api_key = api_key
    save_config(config)

    screen.print(
        Panel(
            f"Provider: [bold]{config.llm.provider}[/]\n"
            f"Base URL: [dim]{config.llm.base_url}[/]\n"
            f"Model: [dim]{config.llm.model}[/]\n"
            f"API Key: {'已更新' if api_key else current_key}",
            title="模型/API 配置已保存",
            border_style="green",
        )
    )
    Prompt.ask("按 Enter 返回", default="")
    return config


def _prompt_scope(state: TuiState) -> None:
    state.only_host = Prompt.ask("仅测试主机", default=state.only_host).strip()
    while True:
        state.only_port = Prompt.ask("仅测试端口", default=state.only_port).strip()
        try:
            _parse_optional_port(state.only_port)
            break
        except ValueError as exc:
            Console().print(f"[red]{exc}[/]")
    state.only_path = Prompt.ask("仅测试路径", default=state.only_path).strip()
    state.blocked_host = Prompt.ask("排除主机", default=state.blocked_host).strip()
    state.blocked_path = Prompt.ask("排除路径", default=state.blocked_path).strip()
    state.allow_actions = _parse_action_csv(
        Prompt.ask(
            "允许动作（逗号分隔，留空使用模式默认值）",
            default=",".join(state.allow_actions),
        )
    )
    state.block_actions = _parse_action_csv(
        Prompt.ask(
            "禁止动作（逗号分隔，留空使用模式默认值）",
            default=",".join(state.block_actions),
        )
    )
    state.resume = Confirm.ask("沿用目标历史上下文", default=state.resume)


def _confirm_and_launch(state: TuiState, launcher: TaskLauncher) -> None:
    if not state.target.strip():
        Console().print("[yellow]请先设置授权目标。[/]")
        Prompt.ask("按 Enter 返回", default="")
        return

    mode = MODES[state.mode]
    if mode.needs_extra_confirm:
        ok = Confirm.ask(
            f"{mode.label} 可能执行更深入或多轮验证。确认目标和范围均已授权？",
            default=False,
        )
        if not ok:
            return

    draft = _draft_from_state(state)
    Console().print(_build_task_summary_panel(draft, title="即将启动"))
    if Confirm.ask("开始授权安全检查", default=False):
        launcher(draft)
        Prompt.ask("任务已返回，按 Enter 回到 TUI", default="")


def _build_task_summary_panel(draft: TuiTaskDraft, *, title: str = "启动摘要") -> Panel:
    lines = [
        f"目标: [bold]{draft.target}[/]",
        f"命令: [bold]{draft.command}[/]",
        f"沿用历史: {'是' if draft.resume else '否'}",
        f"仅测试主机: {draft.only_host or '未限制'}",
        f"仅测试端口: {draft.only_port if draft.only_port is not None else '未限制'}",
        f"仅测试路径: {draft.only_path or '未限制'}",
        f"排除主机: {draft.blocked_host or '未设置'}",
        f"排除路径: {draft.blocked_path or '未设置'}",
        f"允许动作: {', '.join(draft.allow_actions) or '未设置'}",
        f"禁止动作: {', '.join(draft.block_actions) or '未设置'}",
        "",
        "[bold]可复制命令[/]",
        draft.command_line,
    ]
    return Panel("\n".join(lines), title=title, border_style="yellow", box=box.ROUNDED)


def _show_target_history(screen: Console, state: TuiState) -> None:
    if not state.target.strip():
        screen.print("[yellow]请先设置授权目标。[/]")
        Prompt.ask("按 Enter 返回", default="")
        return

    preview = get_target_state_preview(state.target)
    snapshots = list_target_snapshots(state.target)
    if preview is None:
        screen.print(Panel("还没有该目标的历史状态。", title="历史状态", border_style="yellow"))
    else:
        screen.print(
            Panel(
                f"目标: [bold]{preview.get('target', state.target)}[/]\n"
                f"阶段: [bold]{preview.get('phase', 'unknown')}[/]\n"
                f"风险数: [bold]{preview.get('findings_count', 0)}[/]\n"
                f"快照数: [bold]{len(snapshots)}[/]",
                title="历史状态",
                border_style="cyan",
            )
        )
    Prompt.ask("按 Enter 返回", default="")


def _generate_target_report(screen: Console, state: TuiState) -> None:
    if not state.target.strip():
        screen.print("[yellow]请先设置授权目标。[/]")
        Prompt.ask("按 Enter 返回", default="")
        return

    from vulnclaw.cli.main import _generate_report_for_target

    report_path = _generate_report_for_target(state.target)
    screen.print(Panel(report_path, title="报告已生成", border_style="green"))
    Prompt.ask("按 Enter 返回", default="")


def _default_launcher(draft: TuiTaskDraft) -> None:
    from vulnclaw.cli import main as cli_main

    allow_actions = ",".join(draft.allow_actions) if draft.allow_actions else None
    block_actions = ",".join(draft.block_actions) if draft.block_actions else None

    common = {
        "target": draft.target,
        "only_port": draft.only_port,
        "only_host": draft.only_host,
        "only_path": draft.only_path,
        "blocked_host": draft.blocked_host,
        "blocked_path": draft.blocked_path,
        "allow_actions": allow_actions,
        "block_actions": block_actions,
        "resume": draft.resume,
        "snapshot": None,
    }

    if draft.command == "recon":
        cli_main.recon(**common)
    elif draft.command == "scan":
        cli_main.scan(ports=None, **common)
    elif draft.command == "persistent":
        cli_main.persistent(rounds=0, cycles=0, no_report=False, **common)
    else:
        cli_main.run(scope="full", output=None, **common)
