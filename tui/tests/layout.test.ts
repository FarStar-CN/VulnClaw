import { expect, test } from "bun:test";
import { defaultLayout, geometry, moveView, normalizeLayout, WorkbenchState } from "../src/workbench/layout";

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
  expect(geometry(100, 24, workbench.layout).views.find((view) => view.id === "status")?.height).toBe(1);
  workbench.collapse("status", geometry(100, 24, workbench.layout));
  expect(geometry(100, 24, workbench.layout).views.find((view) => view.id === "status")?.height).toBe(12);
  workbench.layout.secondary.push({ id: "status", collapsed: true, expandedHeight: 1 });
  normalizeLayout(workbench.layout);
  expect(new Set([...workbench.layout.primary, ...workbench.layout.secondary].map((view) => view.id)).size).toBe(4);
});
