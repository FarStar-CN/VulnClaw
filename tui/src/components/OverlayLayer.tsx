import { useKeyboard, useRenderer, useTerminalDimensions } from "@opentui/react";
import type { ScrollBoxRenderable } from "@opentui/core";
import { scopeSummary, type SessionController } from "../session/controller";
import { palettes } from "../theme/palette";
import { useClock } from "../theme/activity";
import { TaskForm } from "./TaskForm";
import { ScopeForm } from "./ScopeForm";
import { ConfigForm } from "./ConfigForm";
import { LlmSettings } from "./LlmSettings";
import { DataDialog } from "./DataDialog";
import { ExperiencePanel } from "./ExperiencePanel";
import { visibleText } from "./content";
import { useI18n } from "../i18n";

export function OverlayLayer({ controller, width }: { controller: SessionController; width: number }) {
  const t = useI18n();
  const renderer = useRenderer();
  const { height } = useTerminalDimensions();
  const approval = controller.approvals[0];
  const overlay = controller.overlay;
  const now = useClock(!!approval);
  const p = palettes[controller.theme];
  useKeyboard((key) => {
    if (!approval || key.defaultPrevented) return;
    if (!key.ctrl && !key.meta && key.name.toLowerCase() === "y") controller.resolveApproval(true);
    else if (key.name === "escape" || !key.ctrl && !key.meta && key.name.toLowerCase() === "n") controller.resolveApproval(false);
    else if (["up", "down", "pageup", "pagedown"].includes(key.name)) {
      const scroll = renderer.root.findDescendantById("approval-scroll") as ScrollBoxRenderable | undefined;
      const direction = key.name === "up" || key.name === "pageup" ? -1 : 1;
      scroll?.scrollBy(direction * (key.name.startsWith("page") ? Math.max(1, scroll.viewport.height - 1) : 1));
    }
    key.preventDefault(); key.stopPropagation();
  });
  if (!approval && !overlay) return null;
  const modalWidth = Math.min(width, approval ? 78 : overlay?.kind === "experience" ? 120 : ["task", "scope"].includes(overlay?.kind ?? "") ? 88 : overlay?.kind === "llm" ? 92 : overlay?.kind === "config" ? 104 : width);
  const modalHeight = Math.max(4, Math.min(height, approval ? 18 : overlay?.kind === "experience" ? Math.max(4, height - 2) : overlay?.kind === "llm" ? 22 : ["task", "scope"].includes(overlay?.kind ?? "") ? 32 : overlay?.kind === "config" ? 34 : height));
  const deadline = approval?.request_hash ? controller.approvalDeadlines.get(approval.request_hash) : undefined;
  const remaining = deadline !== undefined && Number.isFinite(deadline) ? Math.max(0, Math.ceil((deadline - now) / 1000)) : null;
  return <box id="overlay-pane" position="absolute" top={0} left={0} width="100%" height="100%" zIndex={20}
    onMouseDown={(event) => event.stopPropagation()}>
    <box position="absolute" left={Math.floor((width - modalWidth) / 2)} top={Math.max(0, Math.floor((height - modalHeight) / 2))}
      width={modalWidth} height={modalHeight} flexDirection="column" visible={!approval}>
      {overlay?.kind === "task" && <TaskForm controller={controller} initial={overlay.preview} original={overlay.original} active={!approval} />}
      {overlay?.kind === "scope" && <ScopeForm initial={overlay.preview} loading={overlay.loading} active={!approval}
        readOnly={!!controller.state?.task.active} theme={controller.theme} summary={scopeSummary}
        onPreview={(scope) => controller.control("session.scope.preview", { scope })}
        onSave={(scope, reset) => controller.control(reset ? "session.scope.reset" : "session.scope.update", reset ? {} : { scope })}
        onSaved={(result) => controller.scopeSaved(result as Record<string, unknown>)} onCancel={() => controller.dismiss()} />}
      {overlay?.kind === "experience" && <ExperiencePanel initial={overlay.data} loading={overlay.loading} error={overlay.error}
        active={!approval} disabled={!!controller.state?.task.active} theme={controller.theme} width={modalWidth}
        onList={(status) => controller.control("experience.list", { status })}
        onReview={(id, status) => controller.control(status === "approved" ? "experience.approve" : "experience.reject", { lesson_id: id })}
        onEdit={(id, changes) => controller.control("experience.edit", { lesson_id: id, ...changes })}
        onClose={() => controller.dismiss()} onNotify={(message) => controller.notify(message)} />}
      {overlay?.kind === "llm" && <LlmSettings controller={controller} document={overlay.document} active={!approval} />}
      {overlay?.kind === "config" && <ConfigForm controller={controller} document={overlay.document} active={!approval} />}
      {overlay?.kind === "data" && <DataDialog title={overlay.title}
        text={typeof overlay.data === "string" ? overlay.data : JSON.stringify(overlay.data, null, 2)}
        active={!approval} theme={controller.theme}
        onClose={() => controller.dismiss()} onNotify={(message) => controller.notify(message)} />}
    </box>
    {approval && <box id="approval-pane" position="absolute" left={Math.floor((width - modalWidth) / 2)} top={Math.max(0, Math.floor((height - modalHeight) / 2))}
      width={modalWidth} height={modalHeight} title={t("Execution approval")} border borderColor={p.error} backgroundColor={p.panel} flexDirection="column" paddingX={1}>
      <scrollbox id="approval-scroll" flexGrow={1} minHeight={0} focused scrollX={false}>
        <text fg={p.action}><b>{visibleText(`${approval.kind ?? t("action")} · ${t("Execution approval")}`)}</b></text>
        <text wrapMode="word" fg={p.soft}>{visibleText(`${t("Working directory")}: ${approval.cwd || t("not specified")}\n\n${t("Command / code")}\n${approval.question}\n\n${t("Reason")}\n${approval.detail ?? ""}`)}</text>
      </scrollbox>
      <box flexDirection="column" flexShrink={0}>
        <text fg={p.signal} wrapMode="word" flexShrink={0}>{visibleText(`${t("Risk")}: ${approval.risk || t("Executes with current user privileges")}`)}</text>
        <text fg={p.muted} height={1} flexShrink={0}>{remaining === null ? t("Waiting for backend decision") : t("{seconds}s remaining · timeout denies", { seconds: remaining })}</text>
        <box flexDirection="row" gap={2} height={1} flexShrink={0}>
          <text fg={p.accent} onMouseDown={() => controller.resolveApproval(true)}>{t("[Y] Approve")}</text>
          <text fg={p.error} onMouseDown={() => controller.resolveApproval(false)}>{t("[N/Esc] Deny")}</text>
        </box>
        <text fg={p.hint} height={1} flexShrink={0}>{t("↑↓/PgUp/PgDn details")}</text>
      </box>
    </box>}
  </box>;
}
