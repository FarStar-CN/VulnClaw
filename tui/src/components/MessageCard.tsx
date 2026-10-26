import { memo } from "react";
import type { Card } from "../session/controller";
import type { MessageSnapshot } from "../transport/protocol.generated";
import type { ThemeId } from "../theme/palette";
import { usePalette } from "../theme/usePalette";
import { disclosureIcon, visibleText } from "./content";
import { MarkdownBody } from "./MarkdownBody";
import { useI18n, statusLabel } from "../i18n";
import { useExpansion } from "./useExpansion";

export const MessageCard = memo(function MessageCard(props: {
  id: string; role: MessageSnapshot["role"]; text: string;
  format: MessageSnapshot["format"]; status: MessageSnapshot["status"];
  theme: ThemeId; width: number; showRole?: boolean; expansion?: Map<string, boolean>;
}) {
  const t = useI18n();
  const p = usePalette(props.theme);
  const [expanded, toggle] = useExpansion(`reasoning-${props.id}`, props.expansion);
  const thinking = props.role === "reasoning";
  const label = props.role === "user" ? t("You") : props.role === "tool" ? t("Tool") : "VulnClaw";
  return <box id={`entry-${props.id}`} flexDirection="column" flexShrink={0} marginTop={props.showRole === false ? 0 : 1}>
    {props.showRole !== false && <text alignSelf="flex-start" height={1} fg={props.role === "user" ? p.text : p.accent}><b>{label}</b></text>}
    {thinking && <box flexDirection="row" height={1}>
      <text flexShrink={0} height={1} fg={p.muted} onMouseDown={toggle}>
        <b>{disclosureIcon(expanded)}</b>{t("Thinking")}
      </text>
      <text fg={p.muted}>{` · ${t("{count} lines", { count: props.text.split("\n").length })}`}</text>
    </box>}
    {(!thinking || expanded) && (props.format === "markdown" ? <MarkdownBody id={`md-${props.id}`} text={props.text}
      streaming={props.status === "streaming"} width={props.width} theme={props.theme} />
      : <text wrapMode="word" fg={p.text}>{visibleText(props.text)}</text>)}
    {["cancelled", "failed"].includes(props.status) && <text fg={props.status === "failed" ? p.error : p.muted}>{statusLabel(props.status)}</text>}
  </box>;
});

export const ToolCard = memo(function ToolCard({ card, theme, width, expansion }: {
  card: Card; theme: ThemeId; width: number; expansion?: Map<string, boolean>;
}) {
  const t = useI18n();
  const p = usePalette(theme);
  const [expanded, toggle] = useExpansion(`tool-${card.id}`, expansion);
  const result = card.kind === "result" || card.kind === "error";
  const title = t(card.title);
  const label = title;
  const preview = expanded ? "" : ` · ${card.text.split("\n")[0]}`;
  return <box id={`entry-${card.id}`} flexDirection="column" flexShrink={0} marginTop={result ? 1 : 0}>
    {result ? <text fg={card.kind === "error" ? p.error : p.accent}>{visibleText(title)}</text>
      : <box flexDirection="row" height={1}>
        <text flexShrink={0} maxWidth={width} height={1} fg={p.muted}
          onMouseDown={toggle}><b>{disclosureIcon(expanded)}</b>{visibleText(label)}</text>
        <text flexGrow={1} height={1} fg={p.muted}>{visibleText(preview)}</text>
      </box>}
    {(result || expanded) && (card.format === "markdown" ? <MarkdownBody id={`tool-md-${card.id}`} text={card.text} width={width} theme={theme} />
      : <text wrapMode="word" fg={card.kind === "error" ? p.error : p.text}>{visibleText(card.text)}</text>)}
  </box>;
});
