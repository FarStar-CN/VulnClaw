import { useTerminalDimensions } from "@opentui/react";
import type { SessionController } from "../session/controller";
import type { WorkbenchState } from "../workbench/layout";
import { Workbench } from "../workbench/Workbench";
import { Composer } from "../components/Composer";
import { OverlayLayer } from "../components/OverlayLayer";
import { palettes } from "../theme/palette";
import { visibleText } from "../components/content";
import { useSession } from "./useSession";
import { useSessionKeys } from "./useSessionKeys";
import { completions } from "../session/completions";

export function TuiShell({ controller, workbench }: { controller: SessionController; workbench: WorkbenchState }) {
  useSession(controller); useSessionKeys(controller, "tui", workbench);
  const { width, height } = useTerminalDimensions();
  const p = palettes[controller.theme];
  const completionRows = controller.overlay || controller.approvals.length ? 0 : Math.min(5, completions(controller).length);
  const composerHeight = Math.min(5, Math.max(2, controller.draft.split("\n").length)) + 2 + completionRows;
  const available = Math.max(1, height - 3 - composerHeight - 2);
  return <box width="100%" height="100%" flexDirection="column" backgroundColor={p.background}>
    <box border borderColor={p.border} height={3} flexShrink={0} flexDirection="row" gap={2}>
      <text fg={p.accent}>VulnClaw</text>
      <text onMouseDown={() => controller.action("config.document")}>[Config]</text>
      <text onMouseDown={() => controller.action("setup.status")}>[Setup]</text>
      <text onMouseDown={() => controller.action("report.read")}>[Report]</text>
      <text onMouseDown={() => { controller.overlay = { kind: "messages" }; controller.changed(); }}>[Messages]</text>
    </box>
    <Workbench controller={controller} model={workbench} width={width} height={available} />
    <Composer controller={controller} active={!controller.overlay && !controller.approvals.length} />
    <text fg={p.accent}>{visibleText(`${controller.state?.execution_mode ?? "agent"} · ${controller.permission} · ${controller.state?.target || "No target"} · ${controller.state?.task.active ? "running" : "ready"}`)}</text>
    <text fg={p.muted}>{visibleText(controller.notice || "Tab mode · Shift+Tab permissions · Ctrl+←/→ focus · Ctrl+C cancel/exit · F5 chain")}</text>
    <OverlayLayer controller={controller} width={width} />
  </box>;
}
