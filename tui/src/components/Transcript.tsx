import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { TextBufferRenderable, type BoxRenderable, type Renderable, type ScrollBoxRenderable } from "@opentui/core";
import { useKeyboard, useRenderer } from "@opentui/react";
import type { SessionController } from "../session/controller";
import { executionPresentation } from "../session/executions";
import { ExecutionCard, type ExecutionNavigation } from "./ExecutionCard";
import { MessageCard, ToolCard } from "./MessageCard";
import { TranscriptLayout } from "./transcript-layout";
import { palettes } from "../theme/palette";
import { currentLanguage, useI18n } from "../i18n";

export interface ReadingPosition { top: number; follow: boolean; anchor?: string; fraction?: number }

function positionTop(layout: TranscriptLayout, position: ReadingPosition, height: number): number {
  if (position.follow) return Math.max(0, layout.total - height);
  const index = position.anchor ? layout.indices.get(position.anchor.slice("entry-".length)) : undefined;
  const top = index === undefined ? position.top : layout.offsets[index]! + (position.fraction ?? 0) * layout.height(index);
  return Math.max(0, Math.min(top, Math.max(0, layout.total - height)));
}

export function Transcript({ controller, width, positions, focused = false, showEmptyHint = true }: {
  controller: SessionController; width: number;
  positions: Map<string, ReadingPosition>; focused?: boolean; showEmptyHint?: boolean;
}) {
  const scroll = useRef<ScrollBoxRenderable>(null);
  const t = useI18n(), renderer = useRenderer();
  const source = controller.source;
  const context = `${width}:${currentLanguage()}`;
  const layouts = useMemo(() => new Map<string, TranscriptLayout>(), []);
  const expansions = useMemo(() => new Map<string, Map<string, boolean>>(), []);
  const rows = useRef(new Map<string, BoxRenderable>());
  const messageText = useRef(new WeakSet<TextBufferRenderable>());
  const actions = useMemo(() => new Map<string, () => void>(), []);
  const [selected, setSelected] = useState("");
  const selectedAction = useRef(""), selectedEntry = useRef("");
  const reveal = useRef("");
  const pendingNavigation = useRef<{ entry: string; direction: number } | null>(null);
  const pinned = useRef(new Set<string>());
  const pinnedSelection = useRef<ReturnType<typeof renderer.getSelection>>(null);
  const viewport = useRef({ source: "", top: 0, height: renderer.height });
  const restoring = useRef<ReadingPosition | null>(null);
  const signature = useRef("");
  const history = useRef(controller.entries);
  const generation = useRef(0);
  const [, refresh] = useState(0);
  if (history.current !== controller.entries) {
    history.current = controller.entries; generation.current++;
    positions.clear(); layouts.clear(); expansions.clear(); pinned.current.clear();
    restoring.current = null;
    selectedEntry.current = ""; selectedAction.current = ""; reveal.current = "";
  }
  const visible = controller.visibleEntries();
  const liveIds = new Set([...controller.messages.keys(), ...controller.cards.keys()]);
  if (!liveIds.size) { positions.clear(); layouts.clear(); }
  if (selectedEntry.current && !liveIds.has(selectedEntry.current)) {
    selectedEntry.current = ""; selectedAction.current = ""; reveal.current = "";
  }
  for (const id of expansions.keys()) if (!liveIds.has(id)) expansions.delete(id);
  for (const [id, cached] of layouts) {
    if (!cached.entries.some((entry) => liveIds.has(entry.id))) layouts.delete(id);
  }
  const layout = layouts.get(source) ?? new TranscriptLayout();
  layouts.set(source, layout);
  const revision = layout.revision;
  layout.sync(visible, context);
  const height = Math.max(1, viewport.current.height);
  const saved = positions.get(source) ?? { top: 0, follow: true };
  if (viewport.current.source !== source) {
    selectedEntry.current = ""; selectedAction.current = ""; reveal.current = "";
  }
  if (layout.revision !== revision && viewport.current.source === source && !restoring.current) {
    restoring.current = { ...saved };
  }
  const top = viewport.current.source === source && !restoring.current
    ? viewport.current.top : positionTop(layout, restoring.current ?? saved, height);
  const mounted = layout.window(top, height, pinned.current);
  signature.current = `${layout.revision}:${mounted.join(",")}`;

  const entryOf = (node: Renderable | undefined): string => {
    while (node) {
      if (node.id.startsWith("transcript-item-")) return node.id.slice("transcript-item-".length);
      node = node.parent ?? undefined;
    }
    return "";
  };
  const select = (id: string) => {
    selectedAction.current = id;
    selectedEntry.current = entryOf(renderer.root.findDescendantById(id));
    reveal.current = id;
    setSelected(id);
  };
  const candidates = () => [...actions.keys()].map((id) => ({ id, node: renderer.root.findDescendantById(id) }))
    .filter((item) => item.node && !item.node.isDestroyed)
    .sort((a, b) => a.node!.y - b.node!.y || a.node!.x - b.node!.x);

  useKeyboard((key) => {
    if (key.defaultPrevented || !focused || controller.inputFocused || controller.overlay || controller.approvals.length) return;
    if ((key.option || key.meta) && ["up", "down"].includes(key.name)) {
      key.preventDefault(); key.stopPropagation();
      const direction = key.name === "up" ? -1 : 1;
      const available = candidates();
      const index = available.findIndex((item) => item.id === selectedAction.current);
      const next = index < 0 && !selectedEntry.current ? available[0] : available[index + direction];
      if (next && (index >= 0 || !selectedEntry.current)) {
        select(next.id); scroll.current?.scrollChildIntoView(next.id); return;
      }
      const start = layout.indices.get(selectedEntry.current) ?? layout.indexAt(viewport.current.top);
      for (let i = start + direction; i >= 0 && i < visible.length; i += direction) {
        const entry = visible[i]!, execution = controller.cards.get(entry.id)?.execution;
        if (!execution || !executionPresentation(execution).expandable) continue;
        pendingNavigation.current = { entry: entry.id, direction };
        const position: ReadingPosition = { top: layout.offsets[i]!, follow: false, anchor: `entry-${entry.id}`, fraction: 0 };
        restoring.current = position; positions.set(source, position);
        refresh((value) => value + 1); return;
      }
    } else if (key.name === "return" && !controller.draft && actions.has(selectedAction.current)) {
      key.preventDefault(); key.stopPropagation(); actions.get(selectedAction.current)!();
      reveal.current = `transcript-item-${selectedEntry.current}`;
    }
  });

  useLayoutEffect(() => {
    restoring.current = { ...(positions.get(source) ?? { top: 0, follow: true }) };
    pinned.current.clear();
    pendingNavigation.current = null;
    const capture = () => {
      const current = scroll.current;
      if (!current || current.isDestroyed || !current.viewport.height || controller.source !== source) return;
      const selection = renderer.getSelection();
      if (selection !== pinnedSelection.current) {
        pinned.current.clear(); pinnedSelection.current = selection;
      }
      if (!selection) pinned.current.clear();
      else for (const node of selection.selectedRenderables) {
        const id = entryOf(node); if (id) pinned.current.add(id);
      }
      const mountedRows = [...rows.current].filter(([, node]) => !node.isDestroyed && node.parent);
      const anchor = mountedRows.find(([, node]) => node.height > 0
        && node.y + node.height > current.viewport.y && node.y <= current.viewport.y);
      const index = layout.indexAt(current.scrollTop);
      const captured: ReadingPosition = {
        top: current.scrollTop,
        follow: current.scrollTop + current.viewport.height >= current.scrollHeight - 1,
        anchor: anchor ? `entry-${anchor[0]}` : layout.entries[index] ? `entry-${layout.entries[index]!.id}` : undefined,
        fraction: anchor ? (current.viewport.y - anchor[1].y) / anchor[1].height
          : (current.scrollTop - (layout.offsets[index] ?? 0)) / Math.max(1, layout.height(index) || 1),
      };
      const visit = (node: Renderable) => {
        if (node instanceof TextBufferRenderable && !messageText.current.has(node)) {
          const processMouseEvent = node.processMouseEvent.bind(node);
          node.processMouseEvent = (event) => {
            if (event.type === "scroll") node.parent?.processMouseEvent(event);
            else processMouseEvent(event);
          };
          messageText.current.add(node);
        }
        for (const child of node.getChildren()) visit(child);
      };
      for (const [, node] of mountedRows) visit(node);
      const measured = layout.measure(mountedRows.map(([id, node]) => [id, node.height] as const));
      // Layout has already moved the nodes by this frame. Preserve the previous logical
      // anchor when content grew, unless the user actually changed the scroll offset.
      const previous = positions.get(source);
      const manuallyScrolled = viewport.current.source === source
        && Math.abs(current.scrollTop - viewport.current.top) > 0.5;
      const reading = restoring.current ?? (measured && previous && !manuallyScrolled ? previous : captured);
      const nextTop = positionTop(layout, reading, current.viewport.height);
      if (measured) restoring.current = reading;
      if (restoring.current) {
        current.scrollTo(reading.follow ? current.scrollHeight : nextTop);
        if (!measured && Math.abs(current.scrollTop - (reading.follow
          ? Math.max(0, current.scrollHeight - current.viewport.height) : nextTop)) < 1) restoring.current = null;
      }
      positions.set(source, { ...reading, top: nextTop });
      viewport.current = { source, top: nextTop, height: current.viewport.height };
      const indices = layout.window(nextTop, current.viewport.height, pinned.current);
      const nextSignature = `${layout.revision}:${indices.join(",")}`;
      if (nextSignature !== signature.current) refresh((value) => value + 1);
      const pending = pendingNavigation.current;
      if (pending) {
        const available = candidates().filter((item) => entryOf(item.node) === pending.entry);
        const target = pending.direction > 0 ? available[0] : available.at(-1);
        if (target) {
          pendingNavigation.current = null; select(target.id); current.scrollChildIntoView(target.id);
        }
      }
      const target = reveal.current ? renderer.root.findDescendantById(reveal.current) : undefined;
      if (target) {
        if (target.y >= current.viewport.y && target.y + Math.min(target.height, current.viewport.height)
          <= current.viewport.y + current.viewport.height) reveal.current = "";
        else current.scrollChildIntoView(target.id);
      }
    };
    renderer.on("frame", capture);
    return () => { renderer.off("frame", capture); };
  }, [source, context, positions, renderer, controller, layout, history.current]);

  // Role grouping includes logical entries outside the mounted window.
  let previousRole = "";
  const roles = visible.map((entry) => {
    const message = controller.messages.get(entry.id);
    const role = message?.role === "reasoning" ? "assistant" : message?.role;
    const show = role !== previousRole;
    if (role) previousRole = role;
    return show;
  });
  let end = 0;
  const children: ReactNode[] = [];
  for (const index of mounted) {
    const entry = visible[index]!;
    const offset = layout.offsets[index]!;
    if (offset > end) children.push(<box key={`gap-${entry.id}`} height={offset - end} flexShrink={0} />);
    let expanded = expansions.get(entry.id);
    if (!expanded) { expanded = new Map(); expansions.set(entry.id, expanded); }
    const navigation: ExecutionNavigation = { actions, expanded, selected, select, details: controller.details };
    const message = controller.messages.get(entry.id), card = controller.cards.get(entry.id);
    children.push(<box key={`${generation.current}:${entry.id}`} id={`transcript-item-${entry.id}`} flexDirection="column" flexShrink={0} width="100%"
      ref={(node: BoxRenderable | null) => { if (node) rows.current.set(entry.id, node); else rows.current.delete(entry.id); }}>
      {entry.kind === "message" && message
        ? <MessageCard id={entry.id} role={message.role} text={message.text} status={message.status}
          format={message.format} theme={controller.theme} width={width} showRole={roles[index]} expansion={expanded} />
        : card?.execution ? <ExecutionCard card={card} theme={controller.theme} width={width - 2} navigation={navigation} />
        : card ? <ToolCard card={card} theme={controller.theme} width={width - 2} expansion={expanded} /> : null}
    </box>);
    end = layout.offsets[index + 1]!;
  }
  if (end < layout.total) children.push(<box key="gap-end" height={layout.total - end} flexShrink={0} />);
  return <scrollbox id="transcript-scroll" ref={scroll} flexGrow={1} width="100%"
    stickyScroll stickyStart="bottom" scrollX={false} focused={focused} onMouseDown={() => {
      if (!controller.overlay && !controller.approvals.length) { controller.inputFocused = false; controller.changed(); }
    }}>
    {showEmptyHint && !visible.length && <text fg={palettes[controller.theme].muted} wrapMode="word">
      {controller.source === "main" ? controller.connected ? t("Ask a question, / for commands, @ for skills") : t("Connecting to Python…") : t("No messages from this agent yet")}
    </text>}
    {children}
    {controller.activity === "conversation" && controller.state?.task.active && <text fg={palettes[controller.theme].muted}>
      {controller.approvals.length ? t("Awaiting approval") : controller.state.task.waiting_for_input ? t("Requires input") : t("VulnClaw is responding…")}
    </text>}
  </scrollbox>;
}
