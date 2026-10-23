import { useTerminalDimensions } from "@opentui/react";
import stringWidth from "string-width";
import type { SessionController } from "../session/controller";
import type { WorkbenchState } from "../workbench/layout";
import { geometry, TUI_HEADER_ROWS, tuiChromeHeight } from "../workbench/layout";
import { Workbench } from "../workbench/Workbench";
import { Composer } from "../components/Composer";
import { OverlayLayer } from "../components/OverlayLayer";
import { PermissionStatus } from "../components/PermissionStatus";
import { palettes } from "../theme/palette";
import { elapsed, equalizer, spinner, useClock } from "../theme/activity";
import { visibleText } from "../components/content";
import { useSession } from "./useSession";
import { useSessionKeys } from "./useSessionKeys";
import { useI18n } from "../i18n";

export function TuiShell({ controller, workbench }: { controller: SessionController; workbench: WorkbenchState }) {
  useSession(controller); useSessionKeys(controller, "tui", workbench);
  const t = useI18n();
  const { width, height } = useTerminalDimensions();
  const p = palettes[controller.theme];
  const busy = controller.state?.task.active ?? false;
  const now = useClock(busy || controller.approvals.length > 0);
  const available = Math.max(1, height - tuiChromeHeight(controller.composerRows, controller.completionRows));
  const tooSmall = geometry(width, available, workbench.layout).tooSmall;
  const activity = busy && controller.activity === "task"
    ? `${spinner(now)} ${controller.approvals.length ? t("awaiting approval") : t("task running")} ${equalizer(now)} ${elapsed(controller.startedAt, now)}` : "";
  const provider = controller.runtime ? t("provider: {provider}", { provider: controller.runtime.config_ready ? controller.runtime.provider : t("not configured") }) : "";
  const showProvider = !!provider && width >= 17 + stringWidth(activity) + stringWidth(provider);
  const focusHint = controller.inputFocused ? t("Enter submit · Shift+Enter newline · Tab complete · Shift+Tab permission")
    : workbench.layout.focus === "subagents" ? t("↑↓ select agent/directory · Enter view/toggle · Ctrl+←/→ pane")
      : workbench.layout.focus === "findings" ? t("↑↓ select finding · Enter evidence · Ctrl+←/→ pane")
        : workbench.layout.focus === "capabilities" ? t("Enter expand/collapse MCP · ↑↓/PgUp/PgDn scroll · Ctrl+←/→ pane")
        : t("Alt+↑/↓ select · Enter expand · ↑↓/PgUp/PgDn scroll");
  const hint = workbench.gesture ? t(workbench.gesture.kind === "move" ? "Move pane · Release to dock · Esc cancel" : "Resize panes · Release to save · Esc cancel")
    : controller.permissionPending ? t("Changing permission…")
      : controller.notice || (busy ? `${t("Ctrl+C cancel")} · ${focusHint}` : focusHint);
  return <box width="100%" height="100%" flexDirection="column" backgroundColor={p.background}>
    <box id="terminal-header" border={["top", "left", "right"]} borderColor={p.border} backgroundColor={p.background} height={TUI_HEADER_ROWS} flexShrink={0} flexDirection="row">
      <text bg={p.action} fg={p.background}><b> VulnClaw </b></text>
      <box flexGrow={1} alignItems="center"><text fg={p.signal}>{visibleText(activity)}</text></box>
      {showProvider && <text fg={p.soft}>{visibleText(`│ ${provider} │ `)}</text>}
    </box>
    <Workbench controller={controller} model={workbench} width={width} height={available} now={now} />
    {!tooSmall && <>
      <Composer controller={controller} active={!controller.overlay && !controller.approvals.length} framed />
      <PermissionStatus controller={controller} />
    </>}
    <text id="hotbar" height={1} flexShrink={0} fg={p.muted} bg={p.background}>{visibleText(hint)}</text>
    <OverlayLayer controller={controller} width={width} />
  </box>;
}
