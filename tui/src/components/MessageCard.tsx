import { memo, useState } from "react";
import type { Card } from "../session/controller";
import type { MessageSnapshot } from "../transport/protocol.generated";
import type { ThemeId } from "../theme/palette";
import { palettes } from "../theme/palette";
import { visibleText } from "./content";
import { MarkdownBody } from "./MarkdownBody";

export const MessageCard = memo(function MessageCard(props: {
  id: string; role: MessageSnapshot["role"]; source: string; text: string;
  format: MessageSnapshot["format"]; status: MessageSnapshot["status"];
  theme: ThemeId; width: number; onRead?: (id: string) => void; showRole?: boolean;
}) {
  const p = palettes[props.theme];
  const [expanded, setExpanded] = useState(false);
  const thinking = props.role === "reasoning";
  const label = props.role === "user" ? "You" : props.role === "tool" ? "Tool" : "VulnClaw";
  return <box id={`entry-${props.id}`} flexDirection="column" flexShrink={0} marginTop={props.showRole === false ? 0 : 1}>
    {props.showRole !== false && <text alignSelf="flex-start" height={1} fg={props.role === "user" ? p.text : p.accent}
      onMouseDown={() => props.onRead?.(props.id)}><b>{label}</b></text>}
    {thinking && <box flexDirection="row" height={1}>
      <text flexShrink={0} height={1} fg={p.muted} onMouseDown={() => setExpanded((value) => !value)}>
        {`${expanded ? "▼" : "▶"} Thinking`}
      </text>
      <text fg={p.muted}>{` · ${props.text.split("\n").length} lines`}</text>
    </box>}
    {(!thinking || expanded) && (props.format === "markdown" ? <MarkdownBody id={`md-${props.id}`} text={props.text}
      streaming={props.status === "streaming"} width={props.width} theme={props.theme} />
      : <text wrapMode="word" fg={p.text}>{visibleText(props.text)}</text>)}
    {["cancelled", "failed"].includes(props.status) && <text fg={props.status === "failed" ? p.error : p.muted}>{props.status}</text>}
  </box>;
});

export const ToolCard = memo(function ToolCard({ card, theme, width }: { card: Card; theme: ThemeId; width: number }) {
  const p = palettes[theme];
  const [expanded, setExpanded] = useState(false);
  const result = card.kind === "result" || card.kind === "error";
  const label = card.kind === "tool_call" ? `→ ${card.title}` : card.kind === "tool_result" ? "↳ Result" : card.title;
  const toggle = `${expanded ? "▼" : "▶"} ${label}`;
  const preview = card.kind === "tool_call" ? `  ${card.text.replace(/\s+/g, " ")}`
    : card.kind === "tool_result" ? ` · ${card.text.split("\n").length} lines` : ` · ${card.text.split("\n")[0]}`;
  return <box id={`entry-${card.id}`} flexDirection="column" flexShrink={0} marginTop={result ? 1 : 0}>
    {result ? <text fg={card.kind === "error" ? p.error : p.accent}>{visibleText(card.title)}</text>
      : <box flexDirection="row" height={1}>
        <text flexShrink={0} maxWidth={width} height={1} fg={p.muted}
          onMouseDown={() => setExpanded((value) => !value)}>{visibleText(toggle)}</text>
        <text flexGrow={1} height={1} fg={p.muted}>{visibleText(preview)}</text>
      </box>}
    {(result || expanded) && (card.format === "markdown" ? <MarkdownBody id={`tool-md-${card.id}`} text={card.text} width={width} theme={theme} />
      : <text wrapMode="word" fg={card.kind === "error" ? p.error : p.text}>{visibleText(card.text)}</text>)}
  </box>;
});
