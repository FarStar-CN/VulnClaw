import { useState } from "react";
import { useKeyboard, useRenderer } from "@opentui/react";
import type { ScrollBoxRenderable } from "@opentui/core";
import type { SessionController } from "../session/controller";
import { palettes } from "../theme/palette";
import { TaskForm } from "./TaskForm";
import { ConfigForm } from "./ConfigForm";
import { WizardForm } from "./WizardForm";
import { FormPanel, type FormRow } from "./FormPanel";
import { Reader } from "./Reader";
import { visibleText } from "./content";

function Inspector({ controller, kind, active, width }: {
  controller: SessionController; kind: "messages" | "agents" | "findings" | "chain"; active: boolean; width: number;
}) {
  const [finding, setFinding] = useState<string | null>(null);
  const selected = controller.findings.find((item) => item.id === finding);
  if (selected) return <Reader controller={controller} title={selected.title} width={width} active={active}
    format="markdown" text={`# ${selected.title}\n\nSeverity: ${selected.severity}\nVerification: ${selected.verification_status ?? selected.evidence_level ?? "unknown"}\n\n${selected.description ?? ""}\n\n## Impact\n${selected.impact ?? ""}\n\n## Evidence\n${selected.evidence ?? ""}\n\n${JSON.stringify(selected.evidence_refs ?? [], null, 2)}\n\n## Remediation\n${selected.remediation ?? ""}`} />;
  if (kind === "chain") return <Reader controller={controller} title="Attack chain" active={active} width={width}
    format="text" text={controller.findings.map((item) => `${item.id} · ${item.title}\n  depends on: ${item.chain_depends_on?.join(", ") || "none"}`).join("\n\n") || "No attack chain available."} />;
  const rows: FormRow[] = kind === "messages" ? [...controller.messages.values()].filter((message) => message.source === controller.source).map((message) => ({
    key: message.message_id, label: `${message.source} · ${message.role} · ${message.text.split("\n")[0]}`,
    action: () => controller.readMessage(message.message_id),
  })) : kind === "agents" ? [{ key: "main", label: "Main agent", action: () => { controller.source = "main"; controller.dismiss(); } },
    ...[...controller.agents.values()].map((agent) => ({ key: agent.agent_id, label: `${agent.name} · ${agent.status}`,
      action: () => { controller.source = agent.agent_id; controller.dismiss(); } }))]
    : controller.findings.map((finding) => ({ key: finding.id, label: `${finding.severity} · ${finding.title}`,
      action: () => setFinding(finding.id) }));
  rows.push({ key: "close", label: "Close", action: () => controller.dismiss() });
  return <FormPanel title={kind} rows={rows} theme={controller.theme} active={active} onChange={() => {}}
    onSave={() => controller.dismiss()} onCancel={() => controller.dismiss()} />;
}

export function OverlayLayer({ controller, width }: { controller: SessionController; width: number }) {
  const renderer = useRenderer();
  const approval = controller.approvals[0];
  const overlay = controller.overlay;
  const p = palettes[controller.theme];
  useKeyboard((key) => {
    if (!approval || key.defaultPrevented) return;
    if (!key.ctrl && key.name === "y") controller.resolveApproval(true);
    else if (key.name === "escape" || !key.ctrl && key.name === "n") controller.resolveApproval(false);
    else if (["up", "down", "pageup", "pagedown"].includes(key.name)) {
      const scroll = renderer.root.findDescendantById("approval-scroll") as ScrollBoxRenderable | undefined;
      const direction = key.name === "up" || key.name === "pageup" ? -1 : 1;
      scroll?.scrollBy(direction * (key.name.startsWith("page") ? Math.max(1, scroll.viewport.height - 1) : 1));
    }
    key.preventDefault();
    key.stopPropagation();
  });
  if (!approval && !overlay) return null;
  return <box id="overlay-pane" position="absolute" top={0} left={0} width="100%" height="100%" zIndex={20}
    backgroundColor={p.background} flexDirection="column" onMouseDown={(event) => event.stopPropagation()}>
    <box flexGrow={1} flexDirection="column" visible={!approval} minHeight={0}>
      {overlay?.kind === "task" && <TaskForm controller={controller} initial={overlay.preview} original={overlay.original} active={!approval} />}
      {overlay?.kind === "config" && <ConfigForm controller={controller} document={overlay.document} active={!approval} />}
      {overlay?.kind === "wizard" && <WizardForm controller={controller} document={overlay.document} active={!approval} />}
      {overlay?.kind === "reader" && (() => {
        const message = controller.messages.get(overlay.messageId);
        return message ? <Reader controller={controller} title={`${message.source} · ${message.role} · ${message.status}`}
          text={message.text} format={message.format === "markdown" ? "markdown" : "text"} messageId={message.message_id}
          active={!approval} width={width} /> : <text>Message unavailable</text>;
      })()}
      {overlay?.kind === "data" && <Reader controller={controller} title={overlay.title}
        text={typeof overlay.data === "string" ? overlay.data : JSON.stringify(overlay.data, null, 2)}
        format={overlay.format} active={!approval} width={width} />}
      {overlay && ["messages", "agents", "findings", "chain"].includes(overlay.kind) && <Inspector controller={controller}
        kind={overlay.kind as "messages" | "agents" | "findings" | "chain"} active={!approval} width={width} />}
    </box>
    {approval && <box id="approval-pane" title="Execution approval" border borderColor={p.accent} flexDirection="column" flexGrow={1}>
      <scrollbox id="approval-scroll" flexGrow={1} focused scrollX={false}>
        <text wrapMode="word">{visibleText(`${approval.kind ?? "action"}: ${approval.question}\n\nWorking directory: ${approval.cwd ?? ""}\n\n${approval.detail ?? ""}\n\nRisk: ${approval.risk ?? ""}\nExpires: ${approval.expires_at ?? ""}`)}</text>
      </scrollbox>
      <text fg={p.accent}>Y approve · N/Esc deny · ↑↓/PgUp/PgDn details</text>
    </box>}
    <text fg={p.muted}>{visibleText(controller.notice)}</text>
  </box>;
}
