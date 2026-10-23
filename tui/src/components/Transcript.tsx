import { useLayoutEffect, useRef } from "react";
import { CodeRenderable, type Renderable, type ScrollBoxRenderable } from "@opentui/core";
import { useRenderer } from "@opentui/react";
import type { SessionController, Entry } from "../session/controller";
import { MessageCard, ToolCard } from "./MessageCard";

export interface ReadingPosition { top: number; follow: boolean; anchor?: string; fraction?: number }
export function Transcript({ controller, width, entries, positions, focused = false }: {
  controller: SessionController; width: number; entries?: Entry[];
  positions: Map<string, ReadingPosition>; focused?: boolean;
}) {
  const scroll = useRef<ScrollBoxRenderable>(null);
  const renderer = useRenderer();
  const source = controller.source;
  useLayoutEffect(() => {
    const saved = positions.get(source);
    let restoring = true;
    const capture = () => {
      const current = renderer.root.findDescendantById("transcript-scroll") as ScrollBoxRenderable | undefined;
      if (!current || current.isDestroyed || current.viewport.height === 0 || controller.source !== source) return;
      if (restoring) {
        let highlighting = false;
        const visit = (node: Renderable) => {
          if (node instanceof CodeRenderable && node.isHighlighting) highlighting = true;
          for (const child of node.getChildren()) visit(child);
        };
        visit(current);
        if (highlighting) return;
        if (!saved || saved.follow) current.scrollTo(current.scrollHeight);
        else {
          const anchor = saved.anchor ? renderer.root.findDescendantById(saved.anchor) : undefined;
          current.scrollTo(anchor ? current.scrollTop + anchor.y - current.viewport.y + (saved.fraction ?? 0) * anchor.height : saved.top);
        }
        restoring = false;
        return;
      }
      const anchor = current.getChildren().find((child) => child.y + child.height > current.viewport.y);
      positions.set(source, { top: current.scrollTop,
        follow: current.scrollTop + current.viewport.height >= current.scrollHeight - 1,
        anchor: anchor?.id, fraction: anchor ? (current.viewport.y - anchor.y) / Math.max(1, anchor.height) : 0 });
    };
    renderer.on("frame", capture);
    return () => {
      renderer.off("frame", capture);
    };
  }, [source, width, positions, renderer, controller]);
  return <scrollbox id="transcript-scroll" ref={scroll} flexGrow={1} width="100%"
    stickyScroll stickyStart="bottom" scrollX={false} focused={focused}>
    {(entries ?? controller.visibleEntries()).map((entry) => {
      const message = controller.messages.get(entry.id);
      const card = controller.cards.get(entry.id);
      return entry.kind === "message" && message
        ? <MessageCard key={entry.id} id={entry.id} role={message.role} source={message.source}
          text={message.text} status={message.status} format={message.format} theme={controller.theme}
          width={width} onRead={controller.readMessage} />
        : card ? <ToolCard key={entry.id} card={card} theme={controller.theme} width={width - 2} /> : null;
    })}
  </scrollbox>;
}
