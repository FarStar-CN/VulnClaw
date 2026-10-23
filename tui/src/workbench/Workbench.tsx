import { useEffect, useMemo, useRef, useState } from "react";
import { MouseButton, type BoxRenderable, type ScrollBoxRenderable } from "@opentui/core";
import { useKeyboard, useRenderer } from "@opentui/react";
import stringWidth from "string-width";
import { type SessionController, record } from "../session/controller";
import { palettes, permissionInfo, severityColor } from "../theme/palette";
import { disclosureIcon, disclosureWidth, visibleText } from "../components/content";
import { Transcript, type ReadingPosition } from "../components/Transcript";
import { geometry, MCP_LIST_ROWS, tuiChromeHeight, type ViewId, type WorkbenchState, type Region } from "./layout";
import { activeAgentRows } from "./agents";

const labels: Record<ViewId, string> = { status: "Status", capabilities: "Capabilities", output: "Session transcript", findings: "Findings inspector", subagents: "Subagents" };

function fitText(text: string, width: number): string {
  if (stringWidth(text) <= width) return text;
  let result = "";
  for (const character of text) {
    if (stringWidth(result + character) >= width) break;
    result += character;
  }
  return result + "…";
}

export function Workbench({ controller, model, width, height, now = Date.now() }: {
  controller: SessionController; model: WorkbenchState; width: number; height: number; now?: number;
}) {
  const renderer = useRenderer();
  const root = useRef<BoxRenderable>(null);
  const positions = useMemo(() => new Map<string, ReadingPosition>(), []);
  const [findingIndex, setFindingIndex] = useState(0);
  const [agentId, setAgentId] = useState("main");
  const [expandedFinding, setExpandedFinding] = useState<string | null>(null);
  const p = palettes[controller.theme];
  const layout = geometry(width, height, model.layout);
  const mcpExpanded = [...model.layout.primary, ...model.layout.secondary].find((view) => view.id === "capabilities")?.mcpExpanded === true;
  const mcpServers = controller.runtime?.mcp_servers ?? [];
  function toggleMcp() {
    if (controller.overlay || controller.approvals.length) return;
    controller.inputFocused = false; model.toggleMcp(layout); controller.changed();
  }
  const agentRows = useMemo(() => activeAgentRows(controller.agents), [controller, controller.version]);
  const selectable = agentRows.filter((row) => row.id !== null);
  const selectedAgent = selectable.some((row) => row.id === agentId) ? agentId : "main";
  const findingSelection = Math.min(findingIndex, Math.max(0, controller.findings.length - 1));
  useEffect(() => {
    if (model.gesture) { model.cancel(); controller.changed(); }
  }, [width, height, model, controller, controller.overlay, controller.approvals.length]);
  function scroll(id: string, delta: number) {
    const box = renderer.root.findDescendantById(id) as ScrollBoxRenderable | undefined;
    box?.scrollBy(delta);
  }
  useKeyboard((key) => {
    if (key.defaultPrevented || controller.overlay || controller.approvals.length || model.gesture || controller.inputFocused) return;
    const focus = model.layout.focus;
    if (key.name === "return" && !key.shift && !controller.draft) {
      if (focus === "subagents") {
        key.preventDefault(); controller.source = selectedAgent; controller.changed();
      } else if (focus === "findings") {
        key.preventDefault();
        const finding = controller.findings[findingSelection];
        if (finding?.evidence_refs?.length) setExpandedFinding((id) => id === finding.id ? null : finding.id);
      } else if (focus === "capabilities") {
        key.preventDefault(); key.stopPropagation(); toggleMcp();
      }
    } else if (["up", "down", "pageup", "pagedown"].includes(key.name) && !key.shift && !key.ctrl) {
      key.preventDefault(); key.stopPropagation();
      const direction = key.name === "up" || key.name === "pageup" ? -1 : 1;
      if (focus === "findings" && !key.name.startsWith("page")) {
        setFindingIndex(Math.max(0, Math.min(controller.findings.length - 1, findingSelection + direction)));
      } else if (focus === "subagents" && !key.name.startsWith("page")) {
        const index = selectable.findIndex((row) => row.id === selectedAgent);
        setAgentId(selectable[Math.max(0, Math.min(selectable.length - 1, index + direction))]?.id ?? "main");
      } else if (focus === "capabilities") {
        const box = renderer.root.findDescendantById("capabilities-scroll") as ScrollBoxRenderable | undefined;
        box?.scrollBy(direction * (key.name.startsWith("page") ? Math.max(1, box.viewport.height - 1) : 1));
      } else scroll(focus === "output" ? "transcript-scroll" : `${focus}-scroll`, direction * (key.name.startsWith("page") ? Math.max(1, layout.views.find((view) => view.id === focus)!.content.height - 1) : 1));
    }
  });
  useEffect(() => {
    const box = renderer.root.findDescendantById("subagents-scroll") as ScrollBoxRenderable | undefined;
    box?.scrollChildIntoView(`agent-row-${selectedAgent}`);
  }, [selectedAgent, renderer]);
  useEffect(() => {
    const box = renderer.root.findDescendantById("findings-scroll") as ScrollBoxRenderable | undefined;
    box?.scrollChildIntoView(`finding-row-${findingSelection}`);
  }, [findingSelection, renderer]);
  if (layout.tooSmall) return <box height={Math.max(1, height)}><text wrapMode="word" fg={p.accent}>
    {`Terminal too small. Need at least ${layout.minimumWidth} columns and ${layout.minimumHeight + tuiChromeHeight(controller.composerRows, controller.completionRows)} rows. Ctrl+C cancels/exits.`}
  </text></box>;
  const local = (event: { x: number; y: number }) => ({ x: event.x - (root.current?.x ?? 0), y: event.y - (root.current?.y ?? 0) });
  function content(region: Region) {
    if (region.id === "output") return <Transcript controller={controller} width={region.content.width - 2} positions={positions} />;
    if (region.id === "status") {
      const state = controller.state;
      const last = record(state?.last_run);
      const policy = permissionInfo(controller.permission, controller.theme);
      const rows = [
        ["Permission", policy.label, policy.color], ["Target", state?.target || "not selected", p.muted],
        ["Scope", `H${state?.task_constraints.allowed_hosts?.length ?? 0} P${state?.task_constraints.allowed_ports?.length ?? 0} A${state?.task_constraints.allowed_actions?.length ?? 0}`, p.muted],
        ["Violations", String(state?.constraint_violations.length ?? 0), state?.constraint_violations.length ? p.warning : p.muted],
      ];
      if (controller.activity === "task") rows.push(["", "", p.muted], ["Task execution", "", p.accent],
        ["State", controller.approvals.length ? "Awaiting approval" : state?.task.waiting_for_input ? "Requires input" : state?.task.active ? "Running" : controller.taskStatus, p.soft],
        ["Phase", controller.taskStatus === "Submitting" ? "—" : state?.phase ?? "idle", p.muted], ["Found", String(controller.findings.length), p.success],
        ["Last run", String(record(last.run).name ?? last.status ?? "none"), p.muted], ["", "", p.muted],
        [state?.task.active || controller.taskStatus === "Submitting" ? "Current command" : "Last command", "", p.accent], ["", controller.taskLabel, p.muted]);
      return <scrollbox id="status-scroll" flexGrow={1} scrollX={false}>
        <text fg={p.action}><b>Workspace</b></text>
        {rows.map(([label, value, color], index) => value && label
          ? <box key={index} flexDirection="row" flexShrink={0}>
            <text fg={color} width={12}>{label}</text>
            <text fg={color} flexGrow={1} wrapMode="word">{visibleText(value)}</text>
          </box>
          : <text key={index} wrapMode="word" fg={color}>{visibleText(value || label || " ")}</text>)}
      </scrollbox>;
    }
    if (region.id === "capabilities") {
      const counter = `${controller.runtime?.mcp_ready ?? 0}/${mcpServers.length}`;
      const mcpLabel = fitText(`${disclosureIcon(mcpExpanded)}MCP`, Math.max(1, region.content.width - stringWidth(counter)));
      return <box flexGrow={1} minHeight={0} flexDirection="column">
        <box height={1} flexShrink={0} flexDirection="row">
          <text id="mcp-toggle" height={1} flexShrink={0} fg={p.text}
            onMouseDown={(event) => { event.stopPropagation(); toggleMcp(); }}>
            <b>{mcpLabel.slice(0, 1)}</b>{mcpLabel.slice(1)}
          </text>
          <box flexGrow={1} /><text id="mcp-count" height={1} fg={p.accent}>{counter}</text>
        </box>
        {mcpExpanded && <scrollbox id="capabilities-scroll" height={Math.min(MCP_LIST_ROWS, Math.max(1, region.content.height - 3))}
          flexShrink={0} scrollX={false}>
          {mcpServers.length ? mcpServers.map((server) => <text id={`mcp-service-${server.name}`} key={server.name}
            height={1} flexShrink={0} wrapMode="none" fg={server.ready ? p.success : p.error}>
            {fitText(`  ● ${visibleText(server.name).replace(/\s/g, " ")}`, region.content.width - 1)}
          </text>) : <text id="mcp-empty" height={1} fg={p.muted}>
            {fitText("  No MCP services enabled", region.content.width - 1)}
          </text>}
        </scrollbox>}
        <box id="skills-count" height={1} flexShrink={0} flexDirection="row">
          <text height={1} flexShrink={0} fg={p.text}>
            {fitText("Skills loaded", Math.max(1, region.content.width - String(controller.runtime?.skills_loaded ?? 0).length))}
          </text>
          <box flexGrow={1} /><text height={1} fg={p.accent}>{String(controller.runtime?.skills_loaded ?? 0)}</text>
        </box>
      </box>;
    }
    if (region.id === "subagents") return <scrollbox id="subagents-scroll" flexGrow={1} scrollX={false}>
      {agentRows.map((row, index) => <text id={row.id ? `agent-row-${row.id}` : `agent-group-${index}`} key={row.id ?? `group-${index}`}
        wrapMode="word" fg={row.id === selectedAgent ? p.action : p.muted}
        onMouseDown={() => { if (row.id) { setAgentId(row.id); controller.source = row.id; controller.changed(); } }}>
        {visibleText(`${row.id === selectedAgent ? "› " : "  "}${row.text}${controller.source === row.id ? " ◀" : ""}`)}
      </text>)}
    </scrollbox>;
    return <scrollbox id="findings-scroll" flexGrow={1} scrollX={false}>
      {!controller.findings.length && <text fg={p.muted}>No findings</text>}
      {controller.findings.map((finding, index) => <box id={`finding-row-${index}`} key={finding.id} flexDirection="column" flexShrink={0}
        backgroundColor={model.layout.focus === "findings" && !controller.inputFocused && index === findingSelection ? p.plate : p.panel}
        onMouseDown={() => { setFindingIndex(index); if (finding.evidence_refs?.length) setExpandedFinding((id) => id === finding.id ? null : finding.id); }}>
        <text wrapMode="word" fg={severityColor(finding.severity, controller.theme)}>
          <b>{finding.evidence_refs?.length ? disclosureIcon(expandedFinding === finding.id) : " ".repeat(disclosureWidth)}</b>
          {finding.severity.toLowerCase() === "critical"
            ? <b>{visibleText(`[${finding.severity.toUpperCase()}] ${finding.title}`)}</b>
            : visibleText(`[${finding.severity.toUpperCase()}] ${finding.title}`)}
        </text>
        <text wrapMode="word" fg={p.muted}>{visibleText(`  ${finding.target}${finding.line ? `:${finding.line}` : ""}`)}</text>
        {expandedFinding === finding.id && finding.evidence_refs?.map((ref, i) => <text key={i} wrapMode="word" fg={p.soft}>
          {visibleText(`   └ ${[ref.kind, ref.path, ref.request_id].filter(Boolean).join(" · ")}`)}
        </text>)}
      </box>)}
    </scrollbox>;
  }
  const viewing = controller.agents.get(controller.source);
  return <box id="workbench" ref={root} width={width} height={height} backgroundColor={p.background}
    onMouseDrag={(event) => {
      if (!model.gesture) return;
      const point = local(event); model.update(point.x, point.y, layout); controller.changed();
    }}
    onMouseDragEnd={() => { if (model.gesture) { model.commit(layout); controller.changed(); } }}
    onMouseUp={() => { if (model.gesture) { model.commit(layout); controller.changed(); } }}>
    {(["primary", "secondary"] as const).map((column) => {
      const rect = layout.columns[column];
      return rect.width > 0 && <box key={`background-${column}`} position="absolute" left={rect.x} top={rect.y}
        width={rect.width} height={rect.height} backgroundColor={p.panel} />;
    })}
    {layout.views.map((region) => {
      const collapsed = [...model.layout.primary, ...model.layout.secondary].find((view) => view.id === region.id)?.collapsed;
      const focused = model.layout.focus === region.id && !controller.inputFocused;
      const source = region.id === "output" ? ` · ${viewing?.name ?? controller.source}${viewing && !["running", "pending"].includes(viewing.status) ? ` (${viewing.status})` : ""}` : "";
      const pulse = controller.state?.task.active && Math.floor(now / 500) % 2 === 0;
      return <box id={`${region.id}-pane`} key={region.id} position="absolute" left={region.x} top={region.y}
        width={region.width} height={region.height} backgroundColor={region.id === "output" ? p.transcript : p.panel}
        onMouseDown={() => {
          if (controller.overlay || controller.approvals.length) return;
          controller.inputFocused = false; model.layout.focus = region.id; controller.changed();
        }}>
        <box position="absolute" left={region.content.x - region.x} top={region.content.y - region.y}
          width={region.content.width} height={region.content.height} flexDirection="column">
          <box id={`title-${region.id}`} height={1} flexShrink={0} flexDirection="row" backgroundColor={region.id === "output" ? p.transcript : p.title}
            onMouseDown={(event) => {
              event.stopPropagation();
              if (event.button !== MouseButton.LEFT || controller.overlay || controller.approvals.length) return;
              controller.inputFocused = false; model.layout.focus = region.id;
              const point = local(event); model.beginMove(region.id, point.x, point.y);
              controller.changed();
            }}>
            {region.id !== "output" && <text id={`collapse-${region.id}`} height={1} width={disclosureWidth} flexShrink={0} selectable={false} fg={p.accent}
              onMouseDown={(event) => {
                event.stopPropagation();
                if (event.button !== MouseButton.LEFT || controller.overlay || controller.approvals.length) return;
                controller.inputFocused = false; model.collapse(region.id, layout); controller.changed();
              }}><b>{disclosureIcon(!collapsed)}</b></text>}
            <text height={1} flexGrow={1} wrapMode="none" selectable={false} fg={focused ? pulse ? p.accent : p.action : p.accent}>
              {fitText(`${labels[region.id]}${region.id === "findings" ? ` (${controller.findings.length})` : ""}${source}${focused ? " *" : ""}${region.id === "output" && pulse ? " ●" : ""}`, region.content.width - (region.id === "output" ? 0 : disclosureWidth))}
            </text>
          </box>
          <box flexDirection="column" flexGrow={1} minHeight={0} visible={!collapsed}>{content(region)}</box>
        </box>
      </box>;
    })}
    {layout.borders.map(({ rect, text }, index) => <text key={`border-${index}`} id={`border-${index}`}
      position="absolute" left={rect.x} top={rect.y} width={rect.width} height={rect.height}
      zIndex={2} wrapMode="none" selectable={false} fg={p.border} bg={p.background}>{text}</text>)}
    {layout.sashes.map(({ sash, rect }, index) => <box key={`sash-${index}`} id={`sash-${index}`} position="absolute"
      left={rect.x} top={rect.y} width={rect.width} height={rect.height} zIndex={5}
      onMouseDown={(event) => {
        event.stopPropagation();
        if (event.button === MouseButton.LEFT && !controller.overlay && !controller.approvals.length) {
          const point = local(event); model.beginResize(sash, point.x, point.y, layout);
          controller.changed();
        }
      }} />)}
    {model.gesture?.kind === "move" && model.gesture.target && <box id="drop-preview" position="absolute"
      left={model.gesture.target.indicator.x} top={model.gesture.target.indicator.y}
      width={model.gesture.target.indicator.width} height={model.gesture.target.indicator.height} backgroundColor={p.dock} zIndex={6} />}
  </box>;
}
