export type ViewId = "status" | "capabilities" | "output" | "findings" | "subagents";
export type Column = "primary" | "secondary";
export interface View { id: ViewId; collapsed: boolean; expandedHeight: number }
export interface LayoutState { primaryWidth: number; secondaryWidth: number; primary: View[]; secondary: View[]; focus: ViewId }
export interface Rect { x: number; y: number; width: number; height: number }
export interface Region extends Rect { id: ViewId; column: Column | "center" }
export type Sash = { kind: Column } | { kind: "stack"; column: Column; index: number };
export interface Geometry { views: Region[]; columns: Record<Column | "center", Rect>; sashes: { sash: Sash; rect: Rect }[]; dock: Rect | null; tooSmall: boolean }
export interface DropTarget { column: Column; index: number; indicator: Rect }
const view = (id: ViewId): View => ({ id, collapsed: false, expandedHeight: 0 });
export const SIDE_MIN = 16, OUTPUT_MIN = 24, VIEW_MIN = 3;
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));
export function defaultLayout(): LayoutState {
  return { primaryWidth: 28, secondaryWidth: 40, primary: [view("status"), view("capabilities")],
    secondary: [view("findings"), view("subagents")], focus: "output" };
}
export function normalizeLayout(state: LayoutState): LayoutState {
  const seen = new Set<ViewId>();
  const allowed: ViewId[] = ["status", "capabilities", "findings", "subagents"];
  for (const column of ["primary", "secondary"] as const) {
    state[column] = state[column].filter((item) => allowed.includes(item.id) && !seen.has(item.id) && !!seen.add(item.id));
    for (const item of state[column]) {
      item.expandedHeight = Number.isFinite(item.expandedHeight) ? Math.max(0, Math.floor(item.expandedHeight)) : 0;
      if (item.expandedHeight) item.expandedHeight = Math.max(VIEW_MIN, item.expandedHeight);
    }
  }
  for (const id of allowed) if (!seen.has(id)) state[id === "status" || id === "capabilities" ? "primary" : "secondary"].push(view(id));
  if (!state.primary.length) state.primary.push(state.secondary.shift()!);
  state.primaryWidth = Number.isFinite(state.primaryWidth) ? Math.max(SIDE_MIN, Math.floor(state.primaryWidth)) : 28;
  state.secondaryWidth = Number.isFinite(state.secondaryWidth) ? Math.max(SIDE_MIN, Math.floor(state.secondaryWidth)) : 40;
  if (![...allowed, "output"].includes(state.focus)) state.focus = "output";
  return state;
}
function stackMinimum(views: View[]): number { return views.reduce((total, item) => total + (item.collapsed ? 1 : VIEW_MIN), 0); }
export function stackHeights(views: View[], height: number): number[] {
  const expanded = views.filter((view) => !view.collapsed).length;
  const share = Math.floor((height - (views.length - expanded)) / Math.max(1, expanded));
  const heights = views.map((view) => view.collapsed ? 1 : Math.max(VIEW_MIN, view.expandedHeight || share));
  const total = heights.reduce((a, b) => a + b, 0);
  let shortage = Math.max(0, total - height);
  for (let i = views.length - 1; i >= 0; i--) if (!views[i].collapsed) {
    const reduction = Math.min(shortage, heights[i] - VIEW_MIN); heights[i] -= reduction; shortage -= reduction;
  }
  const last = views.findLastIndex((view) => !view.collapsed);
  if (total < height && last >= 0) heights[last] += height - total;
  return heights;
}
export function geometry(width: number, height: number, state: LayoutState): Geometry {
  const rightMin = state.secondary.length ? SIDE_MIN : 0;
  const result: Geometry = { views: [], columns: { primary: { x: 0, y: 0, width: 0, height: 0 }, center: { x: 0, y: 0, width: 0, height: 0 }, secondary: { x: 0, y: 0, width: 0, height: 0 } },
    sashes: [], dock: null, tooSmall: width < SIDE_MIN + OUTPUT_MIN + rightMin || height < Math.max(6, stackMinimum(state.primary), stackMinimum(state.secondary)) };
  if (result.tooSmall) return result;
  let left = Math.max(SIDE_MIN, state.primaryWidth), right = state.secondary.length ? Math.max(SIDE_MIN, state.secondaryWidth) : 0;
  const shortage = Math.max(0, left + right + OUTPUT_MIN - width);
  const shrink = Math.min(shortage, right - rightMin); right -= shrink;
  left -= Math.min(shortage - shrink, left - SIDE_MIN);
  const center = width - left - right;
  result.columns = { primary: { x: 0, y: 0, width: left, height }, center: { x: left, y: 0, width: center, height },
    secondary: { x: left + center, y: 0, width: right, height } };
  result.views.push({ ...result.columns.center, id: "output", column: "center" });
  result.sashes.push({ sash: { kind: "primary" }, rect: { x: left - 1, y: 0, width: 2, height } });
  if (right) result.sashes.push({ sash: { kind: "secondary" }, rect: { x: left + center - 1, y: 0, width: 2, height } });
  else if (width >= SIDE_MIN * 2 + OUTPUT_MIN) {
    const dockWidth = Math.min(Math.max(SIDE_MIN, state.secondaryWidth), Math.max(SIDE_MIN, center - OUTPUT_MIN));
    result.dock = { x: width - dockWidth, y: 0, width: dockWidth, height };
  }
  for (const column of ["primary", "secondary"] as const) {
    const rect = result.columns[column]; const views = state[column]; const heights = stackHeights(views, height);
    let y = 0;
    views.forEach((view, index) => {
      result.views.push({ x: rect.x, y, width: rect.width, height: heights[index], id: view.id, column });
      y += heights[index];
      if (index + 1 < views.length && !view.collapsed && !views[index + 1].collapsed) result.sashes.push({
        sash: { kind: "stack", column, index }, rect: { x: rect.x, y: y - 1, width: rect.width, height: 1 },
      });
    });
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
    const first = clamp(views[index].expandedHeight + dy, VIEW_MIN, total - VIEW_MIN);
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
      if (gesture.dragging) gesture.target = dropTarget(layout, x, y);
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
  focus(backwards: boolean): void {
    const order = [...this.layout.primary.map((view) => view.id), "output" as const, ...this.layout.secondary.map((view) => view.id)];
    this.layout.focus = order[(order.indexOf(this.layout.focus) + (backwards ? -1 : 1) + order.length) % order.length];
  }
}
