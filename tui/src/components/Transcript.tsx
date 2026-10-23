import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { CodeRenderable, TextBufferRenderable, type Renderable, type ScrollBoxRenderable } from "@opentui/core";
import { useKeyboard, useRenderer } from "@opentui/react";
import type { SessionController, Entry } from "../session/controller";
import { ExecutionCard, type ExecutionNavigation } from "./ExecutionCard";
import { MessageCard, ToolCard } from "./MessageCard";
import { palettes } from "../theme/palette";
import { useI18n } from "../i18n";

export interface ReadingPosition { top: number; follow: boolean; anchor?: string; fraction?: number }
export function Transcript({ controller, width, entries, positions, focused = false }: {
  controller: SessionController; width: number; entries?: Entry[];
  positions: Map<string, ReadingPosition>; focused?: boolean;
}) {
  const scroll = useRef<ScrollBoxRenderable>(null);
  const t = useI18n();
  const messageText = useRef(new WeakSet<TextBufferRenderable>());
  const renderer = useRenderer();
  const source = controller.source;
  const actions = useMemo(() => new Map<string, () => void>(), []);
  const expanded = useMemo(() => new Map<string, boolean>(), []);
  const [selected, setSelected] = useState("");
  const selectedAction = useRef("");
  const select = (id: string) => { selectedAction.current = id; setSelected(id); };
  const navigation: ExecutionNavigation = { actions, expanded, selected, select };
  useKeyboard((key) => {
    if (key.defaultPrevented || !focused || controller.inputFocused || controller.overlay || controller.approvals.length) return;
    const candidates = [...actions.keys()].map((id) => ({ id, node: renderer.root.findDescendantById(id) }))
      .filter((item) => item.node && !item.node.isDestroyed).sort((a, b) => a.node!.y - b.node!.y || a.node!.x - b.node!.x);
    if ((key.option || key.meta) && ["up", "down"].includes(key.name) && candidates.length) {
      key.preventDefault(); key.stopPropagation();
      const index = candidates.findIndex((item) => item.id === selectedAction.current);
      const next = candidates[Math.max(0, Math.min(candidates.length - 1, index + (key.name === "up" ? -1 : 1)))]!;
      select(next.id); scroll.current?.scrollChildIntoView(next.id);
    } else if (key.name === "return" && !controller.draft && actions.has(selectedAction.current)) {
      key.preventDefault(); key.stopPropagation(); actions.get(selectedAction.current)!();
    }
  });
  useLayoutEffect(() => {
    const saved = positions.get(source);
    let restoring = true;
    const capture = () => {
      const current = renderer.root.findDescendantById("transcript-scroll") as ScrollBoxRenderable | undefined;
      if (!current || current.isDestroyed || current.viewport.height === 0 || controller.source !== source) return;
      let highlighting = false;
      const visit = (node: Renderable) => {
        if (node instanceof TextBufferRenderable && !messageText.current.has(node)) {
          // Text/code also scroll themselves by default. Let only the containing transcript scroll.
          const processMouseEvent = node.processMouseEvent.bind(node);
          node.processMouseEvent = (event) => {
            if (event.type === "scroll") node.parent?.processMouseEvent(event);
            else processMouseEvent(event);
          };
          messageText.current.add(node);
        }
        if (node instanceof CodeRenderable && node.isHighlighting) highlighting = true;
        for (const child of node.getChildren()) visit(child);
      };
      visit(current);
      if (restoring) {
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
  const visible = entries ?? controller.visibleEntries();
  let previousRole = "";
  return <scrollbox id="transcript-scroll" ref={scroll} flexGrow={1} width="100%"
    stickyScroll stickyStart="bottom" scrollX={false} focused={focused} onMouseDown={() => {
      if (!controller.overlay && !controller.approvals.length) { controller.inputFocused = false; controller.changed(); }
    }}>
    {!visible.length && <text fg={palettes[controller.theme].muted} wrapMode="word">
      {controller.source === "main" ? controller.connected ? t("Ask a question, / for commands, @ for skills") : t("Connecting to Python…") : t("No messages from this agent yet")}
    </text>}
    {visible.map((entry) => {
      const message = controller.messages.get(entry.id);
      const card = controller.cards.get(entry.id);
      const role = message?.role === "reasoning" ? "assistant" : message?.role;
      const showRole = role !== previousRole;
      if (role) previousRole = role;
      return entry.kind === "message" && message
        ? <MessageCard key={entry.id} id={entry.id} role={message.role}
          text={message.text} status={message.status} format={message.format} theme={controller.theme}
          width={width} showRole={showRole} />
        : card?.execution ? <ExecutionCard key={entry.id} card={card} theme={controller.theme} width={width - 2}
          navigation={navigation} />
        : card ? <ToolCard key={entry.id} card={card} theme={controller.theme} width={width - 2} /> : null;
    })}
    {controller.activity === "conversation" && controller.state?.task.active && <text fg={palettes[controller.theme].muted}>
      {controller.approvals.length ? t("Awaiting approval") : controller.state.task.waiting_for_input ? t("Requires input") : t("VulnClaw is responding…")}
    </text>}
  </scrollbox>;
}
