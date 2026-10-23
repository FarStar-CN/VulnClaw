import { useEffect, useMemo } from "react";
import { useRenderer, useTerminalDimensions } from "@opentui/react";
import type { SessionController } from "../session/controller";
import { palettes } from "../theme/palette";
import { PermissionStatus } from "../components/PermissionStatus";
import { Composer } from "../components/Composer";
import { Transcript, type ReadingPosition } from "../components/Transcript";
import { OverlayLayer } from "../components/OverlayLayer";
import { HistoryQueue } from "../scrollback/history";
import { visibleText } from "../components/content";
import { useSession } from "./useSession";
import { useSessionKeys } from "./useSessionKeys";
import { useI18n } from "../i18n";

export function ReplShell({ controller }: { controller: SessionController }) {
  useSession(controller);
  const t = useI18n();
  useSessionKeys(controller, "repl");
  const renderer = useRenderer();
  const { width } = useTerminalDimensions();
  const queue = useMemo(() => new HistoryQueue(renderer, controller), [renderer, controller]);
  const positions = useMemo(() => new Map<string, ReadingPosition>(), []);
  const p = palettes[controller.theme];
  useEffect(() => { void queue.drain(); }, [controller.version, queue]);
  useEffect(() => {
    controller.onHistoryRetry = () => { void queue.retry(); };
    return () => { controller.onHistoryRetry = null; queue.dispose(); };
  }, [queue, controller]);
  useEffect(() => {
    const compact = Math.max(8, Math.min(14, Math.floor(renderer.terminalHeight / 2)));
    renderer.footerHeight = controller.overlay || controller.approvals.length
      ? Math.max(compact, renderer.terminalHeight - 2) : compact;
  }, [renderer, controller.overlay, controller.approvals.length]);
  const pending = controller.visibleEntries().filter((entry) => !queue.committed.has(entry.id));
  return <box width="100%" height="100%" flexDirection="column" backgroundColor={p.background}>
    <Transcript controller={controller} width={width - 2} entries={pending} positions={positions} focused={!controller.inputFocused} />
    <Composer controller={controller} active={!controller.overlay && !controller.approvals.length} />
    <PermissionStatus controller={controller} />
    <text fg={p.muted}>{visibleText(queue.error ? `${queue.error} · ${t("/history-retry resumes pending output")}` : controller.notice)}</text>
    <OverlayLayer controller={controller} width={width} />
  </box>;
}
