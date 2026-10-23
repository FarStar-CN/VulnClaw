export type ViewId = "status" | "capabilities" | "output" | "findings" | "subagents";
export type Column = "primary" | "secondary";
export interface View { id: ViewId; collapsed: boolean; expandedHeight: number; mcpExpanded?: boolean }
export interface LayoutState { primaryWidth: number; secondaryWidth: number; primary: View[]; secondary: View[]; focus: ViewId }
export interface Rect { x: number; y: number; width: number; height: number }
export interface Region extends Rect { id: ViewId; column: Column | "center"; content: Rect }
export type Sash = { kind: Column } | { kind: "stack"; column: Column; index: number };
export interface Geometry { views: Region[]; columns: Record<Column | "center", Rect>; borders: { rect: Rect; text: string }[]; sashes: { sash: Sash; rect: Rect }[]; dock: Rect | null; tooSmall: boolean; minimumWidth: number; minimumHeight: number }
export interface DropTarget { column: Column; index: number; indicator: Rect }
const view = (id: ViewId): View => ({ id, collapsed: false, expandedHeight: 0 });
export const SIDE_MIN = 16, OUTPUT_MIN = 24, VIEW_MIN = 3;
export const MCP_LIST_ROWS = 4;
export const TUI_HEADER_ROWS = 2;
export function tuiChromeHeight(composerRows: number, completionRows: number): number {
  return TUI_HEADER_ROWS + composerRows + 2 + completionRows + 2;
}
const minimumHeight = (view: View) => view.id === "capabilities" ? view.mcpExpanded ? 5 : 4 : VIEW_MIN;
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));
export function defaultLayout(): LayoutState {
  return { primaryWidth: 28, secondaryWidth: 40, primary: [view("status"), { ...view("capabilities"), expandedHeight: 4 }],
    secondary: [view("findings"), { ...view("subagents"), expandedHeight: 8 }], focus: "output" };
}
export function normalizeLayout(state: LayoutState): LayoutState {
  const seen = new Set<ViewId>();
  const allowed: ViewId[] = ["status", "capabilities", "findings", "subagents"];
  for (const column of ["primary", "secondary"] as const) {
    state[column] = state[column].filter((item) => allowed.includes(item.id) && !seen.has(item.id) && !!seen.add(item.id));
    for (const item of state[column]) {
      item.expandedHeight = Number.isFinite(item.expandedHeight) ? Math.max(0, Math.floor(item.expandedHeight)) : 0;
      if (item.expandedHeight) item.expandedHeight = Math.max(minimumHeight(item), item.expandedHeight);
    }
  }
  for (const id of allowed) if (!seen.has(id)) state[id === "status" || id === "capabilities" ? "primary" : "secondary"].push(view(id));
  if (!state.primary.length) state.primary.push(state.secondary.shift()!);
  state.primaryWidth = Number.isFinite(state.primaryWidth) ? Math.max(SIDE_MIN, Math.floor(state.primaryWidth)) : 28;
  state.secondaryWidth = Number.isFinite(state.secondaryWidth) ? Math.max(SIDE_MIN, Math.floor(state.secondaryWidth)) : 40;
  if (![...allowed, "output"].includes(state.focus)) state.focus = "output";
  return state;
}
function stackMinimum(views: View[]): number { return views.reduce((total, item) => total + (item.collapsed ? 2 : minimumHeight(item)), 0); }
export function stackHeights(views: View[], height: number): number[] {
  const flexible = views.filter((view) => !view.collapsed && !view.expandedHeight).length;
  const fixed = views.reduce((sum, view) => sum + (view.collapsed ? 2 : view.expandedHeight), 0);
  const share = Math.floor((height - fixed) / Math.max(1, flexible));
  const heights = views.map((view) => view.collapsed ? 2 : Math.max(minimumHeight(view), view.expandedHeight || share));
  const total = heights.reduce((a, b) => a + b, 0);
  let shortage = Math.max(0, total - height);
  for (let i = views.length - 1; i >= 0; i--) if (!views[i].collapsed) {
    const reduction = Math.min(shortage, heights[i] - minimumHeight(views[i])); heights[i] -= reduction; shortage -= reduction;
  }
  const lastFlexible = views.findLastIndex((view) => !view.collapsed && !view.expandedHeight);
  const last = lastFlexible < 0 ? views.findLastIndex((view) => !view.collapsed) : lastFlexible;
  if (total < height && last >= 0) heights[last] += height - total;
  return heights;
}
export function geometry(width: number, height: number, state: LayoutState): Geometry {
  const rightMin = state.secondary.length ? SIDE_MIN : 0;
  const minimumWidth = SIDE_MIN + OUTPUT_MIN + rightMin;
  const minimumHeight = Math.max(6, stackMinimum(state.primary) + 1, stackMinimum(state.secondary) + 1);
  const result: Geometry = { views: [], columns: { primary: { x: 0, y: 0, width: 0, height: 0 }, center: { x: 0, y: 0, width: 0, height: 0 }, secondary: { x: 0, y: 0, width: 0, height: 0 } },
    borders: [], sashes: [], dock: null, minimumWidth, minimumHeight, tooSmall: width < minimumWidth || height < minimumHeight };
  if (result.tooSmall) return result;
  let left = Math.max(SIDE_MIN, state.primaryWidth), right = state.secondary.length ? Math.max(SIDE_MIN, state.secondaryWidth) : 0;
  const shortage = Math.max(0, left + right + OUTPUT_MIN - width);
  const shrink = Math.min(shortage, right - rightMin); right -= shrink;
  left -= Math.min(shortage - shrink, left - SIDE_MIN);
  const center = width - left - right;
  result.columns = { primary: { x: 0, y: 0, width: left, height }, center: { x: left, y: 0, width: center, height },
    secondary: { x: left + center, y: 0, width: right, height } };
  result.views.push({ ...result.columns.center, height: height - 1, id: "output", column: "center",
    content: { x: left + 1, y: 1, width: center - (right ? 1 : 2), height: height - 2 } });
  result.sashes.push({ sash: { kind: "primary" }, rect: { x: left, y: 0, width: 1, height } });
  if (right) result.sashes.push({ sash: { kind: "secondary" }, rect: { x: left + center, y: 0, width: 1, height } });
  else if (width >= SIDE_MIN * 2 + OUTPUT_MIN) {
    const dockWidth = Math.min(Math.max(SIDE_MIN, state.secondaryWidth), Math.max(SIDE_MIN, center - OUTPUT_MIN));
    result.dock = { x: width - dockWidth, y: 0, width: dockWidth, height };
  }
  const horizontal = [{ x: 0, end: width - 1, y: 0 }, { x: 0, end: width - 1, y: height - 1 }];
  for (const column of ["primary", "secondary"] as const) {
    const rect = result.columns[column]; const views = state[column]; const heights = stackHeights(views, height - 1);
    const contentWidth = rect.width - (column === "secondary" ? 2 : 1);
    let y = 0;
    views.forEach((view, index) => {
      result.views.push({ x: rect.x, y, width: rect.width, height: heights[index], id: view.id, column,
        content: { x: rect.x + 1, y: y + 1, width: contentWidth, height: heights[index] - 1 } });
      y += heights[index];
      if (index + 1 < views.length) {
        horizontal.push({ x: rect.x, end: rect.x + rect.width - (column === "secondary" ? 1 : 0), y });
        if (!view.collapsed && !views[index + 1].collapsed) result.sashes.push({
          sash: { kind: "stack", column, index }, rect: { x: rect.x + 1, y, width: contentWidth, height: 1 },
        });
      }
    });
  }
  const vertical = [0, left, ...(right ? [left + center] : []), width - 1];
  // Each border cell has one owner. Vertical lines also draw all junctions.
  for (const line of horizontal) {
    const crosses = vertical.filter((x) => x >= line.x && x <= line.end);
    for (let index = 0; index + 1 < crosses.length; index++) {
      const x = crosses[index] + 1, length = crosses[index + 1] - x;
      result.borders.push({ rect: { x, y: line.y, width: length, height: 1 }, text: "─".repeat(length) });
    }
  }
  for (const x of vertical) {
    const text = Array.from({ length: height }, (_, y) => {
      const lines = horizontal.filter((line) => line.y === y && x >= line.x && x <= line.end);
      const before = lines.some((line) => line.x < x), after = lines.some((line) => line.end > x);
      if (!before && !after) return "│";
      if (x === 0) return y === height - 1 ? "└" : "├";
      if (x === width - 1) return y === height - 1 ? "┘" : "┤";
      if (y === 0) return "┬";
      if (y === height - 1) return "┴";
      return before && after ? "┼" : before ? "┤" : "├";
    }).join("\n");
    result.borders.push({ rect: { x, y: 0, width: 1, height }, text });
  }
  return result;
}
export function dropTarget(layout: Geometry, x: number, y: number): DropTarget | null {
  const contains = (rect: Rect) => x >= rect.x && y >= rect.y && x < rect.x + rect.width && y < rect.y + rect.height;
  if (layout.dock && contains(layout.dock) && x >= layout.dock.x + layout.dock.width - 3) return { column: "secondary", index: 0, indicator: layout.dock };
  for (const column of ["primary", "secondary"] as const) if (contains(layout.columns[column])) {
    const views = layout.views.filter((view) => view.column === column);
    let index = views.findIndex((view) => y < view.y + Math.ceil(view.height / 2));
    if (index < 0) index = views.length;
    return { column, index, indicator: { ...layout.columns[column], y: views[index]?.y ?? Math.max(0, layout.columns[column].height - 1), height: 1 } };
  }
  return null;
}
export function moveView(state: LayoutState, id: ViewId, target: DropTarget, layout: Geometry): boolean {
  if (id === "output") return false;
  const source = state.primary.some((view) => view.id === id) ? "primary" : "secondary";
  if (target.column === "secondary" && source === "primary" && state.primary.length === 1) return false;
  const index = state[source].findIndex((view) => view.id === id);
  if (index < 0) return false;
  let insertion = Math.min(target.index, state[target.column].length);
  if (source === target.column && index < insertion) insertion--;
  if (source === target.column && index === insertion) return false;
  const [view] = state[source].splice(index, 1);
  if (!view.collapsed) view.expandedHeight = layout.views.find((region) => region.id === id)?.height ?? view.expandedHeight;
  state[target.column].splice(insertion, 0, view); state.focus = id; return true;
}
export function resizeLayout(state: LayoutState, layout: Geometry, sash: Sash, dx: number, dy: number): void {
  if (sash.kind !== "stack") {
    const left = layout.columns.primary.width, right = layout.columns.secondary.width, center = layout.columns.center.width;
    state.primaryWidth = left; if (right) state.secondaryWidth = right;
    if (sash.kind === "primary") state.primaryWidth = clamp(left + dx, SIDE_MIN, left + center - OUTPUT_MIN);
    else state.secondaryWidth = clamp(right - dx, SIDE_MIN, right + center - OUTPUT_MIN);
  } else {
    const views = state[sash.column]; const index = sash.index;
    if (!views[index + 1] || views[index].collapsed || views[index + 1].collapsed) return;
    for (const view of views) if (!view.collapsed) view.expandedHeight = layout.views.find((region) => region.id === view.id)!.height;
    const total = views[index].expandedHeight + views[index + 1].expandedHeight;
    const first = clamp(views[index].expandedHeight + dy, minimumHeight(views[index]), total - minimumHeight(views[index + 1]));
    views[index].expandedHeight = first; views[index + 1].expandedHeight = total - first;
  }
}

export type Gesture = { kind: "move"; id: ViewId; x: number; y: number; target: DropTarget | null; dragging: boolean }
  | { kind: "resize"; sash: Sash; x: number; y: number; original: LayoutState; layout: Geometry };
export class WorkbenchState {
  layout = defaultLayout();
  gesture: Gesture | null = null;
  constructor(public persist: () => void = () => {}) {}
  beginMove(id: ViewId, x: number, y: number): void { if (id !== "output") this.gesture = { kind: "move", id, x, y, target: null, dragging: false }; }
  beginResize(sash: Sash, x: number, y: number, layout: Geometry): void { this.gesture = { kind: "resize", sash, x, y, original: structuredClone(this.layout), layout }; }
  update(x: number, y: number, layout: Geometry): void {
    const gesture = this.gesture; if (!gesture) return;
    if (gesture.kind === "resize") {
      this.layout = structuredClone(gesture.original);
      resizeLayout(this.layout, gesture.layout, gesture.sash, x - gesture.x, y - gesture.y);
    } else {
      gesture.dragging ||= Math.abs(x - gesture.x) + Math.abs(y - gesture.y) > 1;
      if (gesture.dragging) {
        const target = dropTarget(layout, x, y);
        const preview = structuredClone(this.layout);
        gesture.target = target && moveView(preview, gesture.id, target, layout) ? target : null;
        if (gesture.target) {
          const width = layout.columns.secondary.x + layout.columns.secondary.width;
          const indicator = geometry(width, layout.columns.primary.height, preview).views.find((region) => region.id === gesture.id);
          if (indicator) gesture.target.indicator = indicator;
        }
      }
    }
  }
  commit(layout: Geometry): void {
    const gesture = this.gesture; if (!gesture) return;
    if (gesture.kind === "move" && gesture.dragging && gesture.target) moveView(this.layout, gesture.id, gesture.target, layout);
    this.gesture = null; this.persist();
  }
  cancel(): void {
    if (this.gesture?.kind === "resize") this.layout = this.gesture.original;
    this.gesture = null;
  }
  collapse(id: ViewId, layout: Geometry): void {
    const view = [...this.layout.primary, ...this.layout.secondary].find((view) => view.id === id);
    if (!view) return;
    if (!view.collapsed) view.expandedHeight = layout.views.find((region) => region.id === id)?.height ?? view.expandedHeight;
    view.collapsed = !view.collapsed; this.layout.focus = id; this.persist();
  }
  toggleMcp(layout: Geometry): void {
    const view = [...this.layout.primary, ...this.layout.secondary].find((view) => view.id === "capabilities");
    if (!view || view.collapsed) return;
    const height = view.expandedHeight || layout.views.find((region) => region.id === "capabilities")!.height;
    view.mcpExpanded = !view.mcpExpanded;
    view.expandedHeight = Math.max(minimumHeight(view), height + (view.mcpExpanded ? MCP_LIST_ROWS : -MCP_LIST_ROWS));
    this.layout.focus = "capabilities"; this.persist();
  }
  focus(backwards: boolean): void {
    const order = [...this.layout.primary.map((view) => view.id), "output" as const, ...this.layout.secondary.map((view) => view.id)];
    this.layout.focus = order[(order.indexOf(this.layout.focus) + (backwards ? -1 : 1) + order.length) % order.length];
  }
}
