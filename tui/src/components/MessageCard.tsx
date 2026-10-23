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
  theme: ThemeId; width: number; onRead?: (id: string) => void;
}) {
  const p = palettes[props.theme];
  return <box id={`entry-${props.id}`} flexDirection="column" flexShrink={0} marginBottom={1}>
    <text fg={p.accent} onMouseDown={() => props.onRead?.(props.id)}>
      {visibleText(`${props.source} · ${props.role}${props.status === "completed" ? "" : ` · ${props.status}`}`)}
    </text>
    {props.format === "markdown" ? <MarkdownBody id={`md-${props.id}`} text={props.text}
      streaming={props.status === "streaming"} width={props.width} theme={props.theme} />
      : <text wrapMode="word" fg={p.text}>{visibleText(props.text)}</text>}
  </box>;
});

export const ToolCard = memo(function ToolCard({ card, theme, width }: { card: Card; theme: ThemeId; width: number }) {
  const p = palettes[theme];
  const [expanded, setExpanded] = useState(false);
  const long = card.text.split("\n").length > 8;
  return <box id={`entry-${card.id}`} flexDirection="column" border borderColor={card.kind === "error" ? p.error : p.border}
    flexShrink={0} marginBottom={1} title={`${card.source} · ${card.title}`}>
    {long && <text fg={p.accent} onMouseDown={() => setExpanded((value) => !value)}>{expanded ? "Collapse" : "Expand full result"}</text>}
    {card.format === "markdown" ? <MarkdownBody id={`tool-md-${card.id}`} text={card.text} width={width} theme={theme} />
      : <text wrapMode="word" fg={p.text}>{visibleText(long && !expanded ? card.text.split("\n").slice(0, 6).join("\n") : card.text)}</text>}
  </box>;
});
