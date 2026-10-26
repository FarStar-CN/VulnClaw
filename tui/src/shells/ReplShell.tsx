import { useLayoutEffect, useMemo } from "react";
import { RGBA } from "@opentui/core";
import { useRenderer, useTerminalDimensions } from "@opentui/react";
import type { SessionController } from "../session/controller";
import { palettes } from "../theme/palette";
import { version } from "../../package.json";
import { PermissionStatus } from "../components/PermissionStatus";
import { Composer } from "../components/Composer";
import { Transcript, type ReadingPosition } from "../components/Transcript";
import { OverlayLayer } from "../components/OverlayLayer";
import { visibleText } from "../components/content";
import { useSession } from "./useSession";
import { useSessionKeys } from "./useSessionKeys";
import { useI18n } from "../i18n";

const wordmark = [
  "__     __          _            ____   _                    ",
  "\\ \\   / /  _   _  | |  _ __    / ___| | |   __ _  __      __",
  " \\ \\ / /  | | | | | | | '_ \\  | |     | |  / _` | \\ \\ /\\ / /",
  "  \\ V /   | |_| | | | | | | | | |___  | | | (_| |  \\ V  V / ",
  "   \\_/     \\__,_| |_| |_| |_|  \\____| |_|  \\__,_|   \\_/\\_/  ",
];
const wordmarkWidth = Math.max(...wordmark.map((line) => line.length));

function ReplWelcome({ controller, width, compact, minimal }: {
  controller: SessionController; width: number; compact: boolean; minimal: boolean;
}) {
  const t = useI18n();
  const p = palettes[controller.theme];
  const model = controller.runtime?.config_ready
    ? `${controller.runtime.model} · ${controller.runtime.provider}`
    : t("provider: {provider}", { provider: t("not configured") });
  const rule = "─".repeat(Math.max(1, Math.min(compact ? 24 : 44,
    width - (compact ? 2 : wordmarkWidth + 3))));
  return <box id="repl-welcome" width="100%" height={compact ? (minimal ? 1 : 3) : 7} flexShrink={0}
    paddingLeft={1} paddingTop={compact ? 0 : 1} flexDirection={compact ? "column" : "row"}>
    {compact ? <>
      {!minimal && <text id="repl-welcome-top-rule" height={1} fg={p.action}>{rule}</text>}
      <box height={1} flexDirection="row">
        <text fg={p.action}><b>VulnClaw</b></text>
        <text fg={p.muted}>{` v${version}`}</text>
      </box>
      {!minimal && <text id="repl-welcome-bottom-rule" height={1} fg={p.action}>{rule}</text>}
    </> : <>
      <box id="repl-welcome-wordmark" width={wordmarkWidth} height={wordmark.length} flexShrink={0} flexDirection="column">
        {wordmark.map((line) => <text key={line} fg={p.action} wrapMode="none">{line}</text>)}
      </box>
      <box flexGrow={1} minWidth={0} height={5} paddingLeft={2} flexDirection="column">
        <text id="repl-welcome-top-rule" height={1} fg={p.action}>{rule}</text>
        <box height={1} flexDirection="row">
          <text fg={p.text}><b>VulnClaw</b></text>
          <text fg={p.muted}>{` v${version}`}</text>
        </box>
        <text id="repl-welcome-model" height={1} truncate fg={p.soft}>{visibleText(model)}</text>
        <text id="repl-welcome-directory" height={1} truncate fg={p.muted}>{visibleText(process.cwd())}</text>
        <text id="repl-welcome-bottom-rule" height={1} fg={p.action}>{rule}</text>
      </box>
    </>}
  </box>;
}

export function ReplShell({ controller }: { controller: SessionController }) {
  useSession(controller);
  useSessionKeys(controller, "repl");
  const renderer = useRenderer();
  const { width, height } = useTerminalDimensions();
  const positions = useMemo(() => new Map<string, ReadingPosition>(), []);
  const p = palettes[controller.theme];
  const showWelcome = controller.source === "main" && controller.messages.size === 0 && !controller.activity;
  useLayoutEffect(() => { renderer.setBackgroundColor(RGBA.defaultBackground(p.background)); }, [renderer, p.background]);
  return <box width="100%" height="100%" flexDirection="column" backgroundColor={RGBA.defaultBackground(p.background)}>
    {showWelcome && <ReplWelcome controller={controller} width={width}
      compact={width < wordmarkWidth + 30 || height < 20} minimal={height < 15} />}
    <Transcript controller={controller} width={width - 2} positions={positions} focused={!controller.inputFocused}
      showEmptyHint={!showWelcome} />
    <Composer controller={controller} active={!controller.overlay && !controller.approvals.length} />
    <PermissionStatus controller={controller} />
    <text id="repl-notice" height={1} flexShrink={0} truncate fg={p.muted}>{visibleText(controller.notice || controller.startupMessage)}</text>
    <OverlayLayer controller={controller} width={width} />
  </box>;
}
