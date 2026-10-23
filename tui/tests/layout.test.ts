import { expect, test } from "bun:test";
import { defaultLayout, geometry, moveView, normalizeLayout, resizeLayout, WorkbenchState } from "../src/workbench/layout";

test("layout preserves pane ownership, supports an empty secondary column and rejects an empty primary", () => {
  const layout = defaultLayout();
  let bounds = geometry(100, 20, layout);
  expect(bounds.tooSmall).toBe(false);
  moveView(layout, "findings", { column: "primary", index: 1, indicator: bounds.columns.primary }, bounds);
  bounds = geometry(100, 20, layout);
  moveView(layout, "subagents", { column: "primary", index: 2, indicator: bounds.columns.primary }, bounds);
  bounds = geometry(100, 20, layout);
  expect(layout.secondary).toHaveLength(0);
  expect(bounds.dock).not.toBeNull();
  for (const id of ["capabilities", "subagents", "findings"] as const) moveView(layout, id, {
    column: "secondary", index: 0, indicator: bounds.columns.secondary,
  }, bounds);
  expect(moveView(layout, "status", { column: "secondary", index: 0, indicator: bounds.columns.secondary }, bounds)).toBe(false);
  expect(layout.primary.map((view) => view.id)).toEqual(["status"]);
  expect(geometry(30, 20, layout).tooSmall).toBe(true);
});

test("resize and move gestures preview, commit on release and roll back on Esc", () => {
  let saves = 0;
  const workbench = new WorkbenchState(() => saves++);
  let bounds = geometry(120, 24, workbench.layout);
  const original = structuredClone(workbench.layout);
  workbench.beginResize({ kind: "primary" }, 28, 1, bounds);
  workbench.update(36, 1, bounds);
  expect(workbench.layout.primaryWidth).toBe(36);
  expect(saves).toBe(0);
  workbench.cancel(); expect(workbench.layout).toEqual(original);
  workbench.beginResize({ kind: "stack", column: "primary", index: 0 }, 2, 12, bounds);
  workbench.update(2, 15, bounds); workbench.commit(bounds);
  expect(saves).toBe(1);
  expect(workbench.layout.primary[0].expandedHeight).toBeGreaterThan(workbench.layout.primary[1].expandedHeight);
  bounds = geometry(120, 24, workbench.layout);
  workbench.beginMove("status", 2, 0);
  workbench.update(110, 23, bounds);
  expect(workbench.layout.primary[0].id).toBe("status");
  workbench.cancel(); expect(workbench.layout.primary[0].id).toBe("status");
  workbench.beginMove("status", 2, 0); workbench.update(110, 23, bounds); workbench.commit(bounds);
  expect(workbench.layout.secondary.at(-1)?.id).toBe("status");
});

test("collapsed panes restore height and normalization keeps exactly one of each view", () => {
  const workbench = new WorkbenchState();
  const bounds = geometry(100, 24, workbench.layout);
  workbench.collapse("status", bounds);
  expect(geometry(100, 24, workbench.layout).views.find((view) => view.id === "status")?.height).toBe(2);
  workbench.collapse("status", geometry(100, 24, workbench.layout));
  expect(geometry(100, 24, workbench.layout).views.find((view) => view.id === "status")?.height).toBe(19);
  workbench.layout.secondary.push({ id: "status", collapsed: true, expandedHeight: 1 });
  normalizeLayout(workbench.layout);
  expect(new Set([...workbench.layout.primary, ...workbench.layout.secondary].map((view) => view.id)).size).toBe(4);
});

test("MCP disclosure restores custom heights and leaves room for both counters on short terminals", () => {
  const workbench = new WorkbenchState();
  const capabilities = workbench.layout.primary.find((view) => view.id === "capabilities")!;
  capabilities.expandedHeight = 10;
  workbench.toggleMcp(geometry(120, 24, workbench.layout));
  expect(capabilities.mcpExpanded).toBe(true); expect(capabilities.expandedHeight).toBe(14);
  const short = geometry(64, 9, workbench.layout);
  expect(short.tooSmall).toBe(false);
  expect(short.views.find((view) => view.id === "capabilities")?.height).toBe(5);
  workbench.toggleMcp(short);
  expect(capabilities.mcpExpanded).toBe(false); expect(capabilities.expandedHeight).toBe(10);
});

test("shared borders and title rows fit minimum terminals with expanded or collapsed panes", () => {
  for (const mode of ["default", "expanded", "collapsed", "empty-secondary"] as const) {
    const workbench = new WorkbenchState();
    if (mode === "expanded") workbench.layout.primary[1].mcpExpanded = true;
    if (mode === "collapsed") for (const view of [...workbench.layout.primary, ...workbench.layout.secondary]) view.collapsed = true;
    if (mode === "empty-secondary") workbench.layout.primary.push(...workbench.layout.secondary.splice(0));
    const minimum = geometry(120, 24, workbench.layout);
    const bounds = geometry(minimum.minimumWidth, minimum.minimumHeight, workbench.layout);
    expect(bounds.tooSmall).toBe(false);
    expect(geometry(minimum.minimumWidth - 1, minimum.minimumHeight, workbench.layout).tooSmall).toBe(true);
    expect(geometry(minimum.minimumWidth, minimum.minimumHeight - 1, workbench.layout).tooSmall).toBe(true);
    for (const view of bounds.views) {
      expect(view.content.x).toBeGreaterThan(0); expect(view.content.y).toBeGreaterThan(0);
      expect(view.content.x + view.content.width).toBeLessThanOrEqual(minimum.minimumWidth - 1);
      expect(view.content.y + view.content.height).toBeLessThanOrEqual(minimum.minimumHeight - 1);
      expect(view.content.height).toBeGreaterThanOrEqual(1);
    }
    const cells = new Set<string>();
    for (const border of bounds.borders) for (let y = 0; y < border.rect.height; y++) for (let x = 0; x < border.rect.width; x++) {
      const key = `${border.rect.x + x},${border.rect.y + y}`;
      expect(cells.has(key)).toBe(false); cells.add(key);
    }
    for (const { sash } of bounds.sashes) {
      const layout = structuredClone(workbench.layout);
      resizeLayout(layout, bounds, sash, -1000, 1000);
      const resized = geometry(minimum.minimumWidth, minimum.minimumHeight, layout);
      expect(resized.tooSmall).toBe(false);
      expect(resized.views.every((view) => view.content.width > 0 && view.content.height > 0)).toBe(true);
    }
  }
});
