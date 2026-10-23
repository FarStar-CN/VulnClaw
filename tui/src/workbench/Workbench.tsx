import { useEffect, useMemo, useRef, useState } from "react";
import { type BoxRenderable, type ScrollBoxRenderable } from "@opentui/core";
import { useKeyboard, useRenderer } from "@opentui/react";
import { type SessionController, record } from "../session/controller";
import { palettes } from "../theme/palette";
import { visibleText } from "../components/content";
import { Transcript, type ReadingPosition } from "../components/Transcript";
import { MarkdownBody } from "../components/MarkdownBody";
import { geometry, type ViewId, type WorkbenchState, type Region } from "./layout";

const labels: Record<ViewId, string> = { status: "Status", capabilities: "Capabilities", output: "Session transcript", findings: "Findings inspector", subagents: "Subagents" };

export function Workbench({ controller, model, width, height }: {
  controller: SessionController; model: WorkbenchState; width: number; height: number;
}) {
  const renderer = useRenderer();
  const root = useRef<BoxRenderable>(null);
  const positions = useMemo(() => new Map<string, ReadingPosition>(), []);
  const [findingIndex, setFindingIndex] = useState(0);
  const [agentIndex, setAgentIndex] = useState(0);
  const [expanded, setExpanded] = useState(true);
  const p = palettes[controller.theme];
  const layout = geometry(width, height, model.layout);
  const selected = controller.findings[Math.min(findingIndex, controller.findings.length - 1)];
  const agentRows = useMemo(() => {
    const agents = [...controller.agents.values()];
    const rows = [{ id: "main", text: "main" }];
    const seen = new Set<string>();
    function append(id: string, prefix: string) {
      if (seen.has(id)) return;
      seen.add(id);
      const agent = controller.agents.get(id)!;
      rows.push({ id, text: `${prefix}${agent.name} (${agent.agent_type}) · ${agent.status}` });
      agents.filter((child) => child.parent_id === id).forEach((child, i, list) => append(child.agent_id, prefix + (i === list.length - 1 ? "└─ " : "├─ ")));
    }
    agents.filter((agent) => !controller.agents.has(agent.parent_id)).forEach((agent) => append(agent.agent_id, ""));
    agents.filter((agent) => !seen.has(agent.agent_id)).forEach((agent) => append(agent.agent_id, ""));
    return rows;
  }, [controller, controller.version]);
  useEffect(() => { model.cancel(); }, [width, height, model, controller.overlay, controller.approvals.length]);
  function scroll(id: string, delta: number) {
    const box = renderer.root.findDescendantById(id);
    if (box && "scrollBy" in box) (box as ScrollBoxRenderable).scrollBy(delta);
  }
  useKeyboard((key) => {
    if (key.defaultPrevented || controller.overlay || controller.approvals.length || model.gesture) return;
    const query = controller.draft.trimStart();
    if ((query.startsWith("/") || query.startsWith("@")) && !query.includes(" ")) return;
    const focus = model.layout.focus;
    if (key.name === "return" && !key.shift && focus === "subagents") {
      key.preventDefault(); controller.source = agentRows[Math.min(agentIndex, agentRows.length - 1)]?.id ?? "main"; controller.changed();
    } else if (key.name === "return" && !key.shift && focus === "findings") {
      key.preventDefault(); setExpanded((value) => !value);
    } else if (["up", "down", "pageup", "pagedown"].includes(key.name) && !key.shift && !key.ctrl) {
      key.preventDefault();
      const direction = key.name === "up" || key.name === "pageup" ? -1 : 1;
      if (focus === "findings" && !key.name.startsWith("page")) {
        setFindingIndex((index) => Math.max(0, Math.min(controller.findings.length - 1, index + direction)));
      } else if (focus === "subagents" && !key.name.startsWith("page")) {
        setAgentIndex((index) => Math.max(0, Math.min(agentRows.length - 1, index + direction)));
      } else scroll(focus === "output" ? "transcript-scroll" : `${focus}-scroll`, direction * (key.name.startsWith("page") ? 8 : 1));
    }
  });
  useEffect(() => {
    const box = renderer.root.findDescendantById("subagents-scroll") as ScrollBoxRenderable | undefined;
    box?.scrollChildIntoView(`agent-row-${agentIndex}`);
  }, [agentIndex, renderer]);
  useEffect(() => {
    const box = renderer.root.findDescendantById("findings-list") as ScrollBoxRenderable | undefined;
    box?.scrollChildIntoView(`finding-row-${findingIndex}`);
    const details = renderer.root.findDescendantById("findings-scroll") as ScrollBoxRenderable | undefined;
    if (details) details.scrollTo(0);
  }, [findingIndex, renderer]);
  if (layout.tooSmall) return <box height={Math.max(1, height)}><text wrapMode="word" fg={p.accent}>Terminal too small for the workbench. Enlarge the window; Ctrl+C remains available.</text></box>;
  const local = (event: { x: number; y: number }) => ({ x: event.x - (root.current?.x ?? 0), y: event.y - (root.current?.y ?? 0) });
  function content(region: Region) {
    if (region.id === "output") return <Transcript controller={controller} width={region.width - 2} positions={positions} />;
    if (region.id === "status") {
      const state = controller.state;
      const last = record(state?.last_run);
      return <scrollbox id="status-scroll" flexGrow={1} scrollX={false}>
        <text wrapMode="word" fg={p.text}>{visibleText(`Workspace
Mode: ${state?.execution_mode ?? "agent"}
Guard: ${controller.permission}
Target: ${state?.target || "not selected"}
Scope: H${state?.task_constraints.allowed_hosts?.length ?? 0} P${state?.task_constraints.allowed_ports?.length ?? 0} A${state?.task_constraints.allowed_actions?.length ?? 0}
Violations: ${state?.constraint_violations.length ?? 0}` + (controller.activity === "task" ? `

Task execution
State: ${controller.approvals.length ? "Awaiting approval" : state?.task.waiting_for_input ? "Requires input" : state?.task.active ? "Running" : controller.taskStatus}
Phase: ${state?.phase ?? "idle"}
Findings: ${controller.findings.length}
Last run: ${record(last.run).name ?? last.status ?? "none"}

${state?.task.active ? "Current command" : "Last command"}
${controller.taskLabel}` : ""))}</text>
      </scrollbox>;
    }
    if (region.id === "capabilities") return <scrollbox id="capabilities-scroll" flexGrow={1} scrollX={false}>
      {!Object.keys(controller.commands).length && !controller.skills.length && <text fg={p.muted}>No capabilities advertised</text>}
      <text fg={p.accent}>Commands</text>
      {Object.entries(controller.commands).map(([name, description]) => <text key={name} fg={p.text} wrapMode="word"
        onMouseDown={() => { controller.draft = `/${name} `; controller.changed(); }}>{visibleText(`├─ /${name} · ${description}`)}</text>)}
      <text fg={p.accent}>Python skills</text>
      {controller.skills.map((skill) => <text key={skill} fg={p.muted} wrapMode="word">{visibleText(`└─ ${skill}`)}</text>)}
    </scrollbox>;
    if (region.id === "subagents") return <scrollbox id="subagents-scroll" flexGrow={1} scrollX={false}>
      {agentRows.map((row, index) => <text id={`agent-row-${index}`} key={row.id} wrapMode="word" fg={index === agentIndex ? p.accent : p.text}
        onMouseDown={() => { setAgentIndex(index); controller.source = row.id; controller.changed(); }}>{visibleText(`${controller.source === row.id ? "●" : " "} ${row.text}`)}</text>)}
    </scrollbox>;
    return <box flexDirection="column" flexGrow={1} minHeight={0}>
      <scrollbox id="findings-list" height={Math.max(1, Math.min(5, Math.floor(region.height / 3)))} scrollX={false} flexShrink={0}>
        {controller.findings.length ? controller.findings.map((finding, index) => <text id={`finding-row-${index}`} key={finding.id}
          fg={index === findingIndex ? p.accent : p.text} wrapMode="word" onMouseDown={() => { setFindingIndex(index); setExpanded(true); }}>
          {visibleText(`${index === findingIndex ? "›" : " "} ${finding.severity} · ${finding.title}`)}
        </text>) : <text fg={p.muted}>No findings</text>}
      </scrollbox>
      {selected && expanded && <scrollbox id="findings-scroll" flexGrow={1} scrollX={false}>
        <text fg={p.accent}>{visibleText(`${selected.title}\n${selected.verification_status ?? "unverified"} · ${selected.evidence_level ?? "unknown"}`)}</text>
        <MarkdownBody id={`finding-${selected.id}`} text={selected.description ?? ""} width={region.width - 2} theme={controller.theme} />
        <text wrapMode="word" fg={p.muted}>{visibleText(`Impact: ${selected.impact ?? ""}\nEvidence: ${selected.evidence ?? ""}\n${JSON.stringify(selected.evidence_refs ?? [], null, 2)}\nRemediation: ${selected.remediation ?? ""}\n${selected.code_location ?? ""}`)}</text>
      </scrollbox>}
    </box>;
  }
  return <box id="workbench" ref={root} width={width} height={height}>
    {layout.views.map((region) => {
      const collapsed = [...model.layout.primary, ...model.layout.secondary].find((view) => view.id === region.id)?.collapsed;
      return <box id={`${region.id}-pane`} key={region.id} position="absolute" left={region.x} top={region.y}
        width={region.width} height={region.height} border title={`${labels[region.id]}${region.id === "output" ? ` · ${controller.source}` : ""}`}
        borderColor={model.layout.focus === region.id ? p.accent : p.border} flexDirection="column"
        onMouseDown={(event) => {
          if (controller.overlay || controller.approvals.length) return;
          model.layout.focus = region.id;
          const point = local(event);
          if (point.y === region.y) model.beginMove(region.id, point.x, point.y);
          controller.changed();
        }} onMouseDrag={(event) => { const point = local(event); model.update(point.x, point.y, layout); controller.changed(); }}
        onMouseDragEnd={() => { model.commit(layout); controller.changed(); }}
        onMouseUp={() => { model.commit(layout); controller.changed(); }}>
        {region.id !== "output" && <text id={`collapse-${region.id}`} position="absolute" top={-1} right={0} fg={p.accent}
          onMouseDown={(event) => { event.stopPropagation(); model.collapse(region.id, layout); controller.changed(); }}>{collapsed ? "+" : "−"}</text>}
        <box flexDirection="column" flexGrow={1} minHeight={0} visible={!collapsed}>{content(region)}</box>
      </box>;
    })}
    {layout.sashes.map(({ sash, rect }, index) => <box key={`sash-${index}`} id={`sash-${index}`} position="absolute"
      left={rect.x} top={rect.y} width={rect.width} height={rect.height} zIndex={5}
      onMouseDown={(event) => { if (!controller.overlay && !controller.approvals.length) { const point = local(event); model.beginResize(sash, point.x, point.y, layout); event.stopPropagation(); } }}
      onMouseDrag={(event) => { const point = local(event); model.update(point.x, point.y, layout); controller.changed(); }}
      onMouseDragEnd={() => { model.commit(layout); controller.changed(); }}
      onMouseUp={() => { model.commit(layout); controller.changed(); }} />)}
    {model.gesture?.kind === "move" && model.gesture.target && <box id="drop-preview" position="absolute"
      left={model.gesture.target.indicator.x} top={model.gesture.target.indicator.y}
      width={model.gesture.target.indicator.width} height={model.gesture.target.indicator.height} backgroundColor={p.accent} zIndex={6} />}
  </box>;
}
