import { expect, test } from "bun:test";
import { act } from "react";
import { testRender } from "@opentui/react/test-utils";
import { SessionController } from "../src/session/controller";
import { TuiShell } from "../src/shells/TuiShell";
import { geometry, WorkbenchState } from "../src/workbench/layout";
import { finalFrame } from "./render-helpers";

async function fixture() {
  const controller = new SessionController(); controller.connected = true; controller.notice = "";
  const workbench = new WorkbenchState();
  const setup = await testRender(<TuiShell controller={controller} workbench={workbench} />,
    { width: 120, height: 36, kittyKeyboard: true });
  const node = (id: string) => setup.renderer.root.findDescendantById(id)!;
  await finalFrame(setup);
  return { controller, workbench, setup, node };
}

test("workbench draws one shared border between panes, the header and the composer", async () => {
  const { setup, node } = await fixture();
  try {
    const frame = (await finalFrame(setup)).split("\n");
    const bench = node("workbench"), header = node("terminal-header"), input = node("composer-input");
    expect(bench.y).toBe(header.y + header.height);
    expect(frame[bench.y]).toMatch(/^├─+┬─+┬─+┤$/);
    expect(frame[bench.y + bench.height - 1]).toMatch(/^├─+┴─+┴─+┤$/);
    expect(input.y).toBe(bench.y + bench.height);
    expect(node("composer-editor").y).toBe(input.y);
    expect(frame[input.y + input.height - 1]).toMatch(/^└─+┘$/);
    for (const id of ["status", "capabilities", "output", "findings", "subagents"]) {
      const pane = node(`${id}-pane`), title = node(`title-${id}`);
      expect(title.y).toBe(pane.y + 1); expect(title.height).toBe(1);
      expect(frame[title.y][title.x - 1]).toBe("│");
    }
    expect(node("status-scroll").y).toBe(node("title-status").y + 1);
    const sash = node("sash-0");
    expect(sash.width).toBe(1);
    expect(frame[node("title-status").y][sash.x]).toBe("│");
    expect(frame[node("capabilities-pane").y][sash.x]).toBe("┤");
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("title whitespace moves a pane, while a click and a one-cell motion only focus it", async () => {
  const { workbench, setup, node } = await fixture();
  try {
    const title = node("title-status"), x = title.x + title.width - 2;
    const original = structuredClone(workbench.layout);
    await act(async () => {
      await setup.mockMouse.pressDown(x, title.y);
      await setup.mockMouse.emitMouseEvent("drag", x - 1, title.y);
    });
    expect(workbench.gesture?.kind).toBe("move");
    expect(workbench.gesture?.kind === "move" && workbench.gesture.dragging).toBe(false);
    await act(async () => { await setup.mockMouse.release(x - 1, title.y); });
    await finalFrame(setup);
    expect(workbench.layout.primary).toEqual(original.primary);
    expect(workbench.layout.focus).toBe("status");
    const destination = node("subagents-pane"), endX = destination.x + 4, endY = destination.y + destination.height - 2;
    await act(async () => {
      await setup.mockMouse.pressDown(x, title.y);
      await setup.mockMouse.emitMouseEvent("drag", endX, endY);
    });
    await finalFrame(setup);
    expect(workbench.gesture?.kind === "move" && workbench.gesture.target?.column).toBe("secondary");
    expect(workbench.layout.primary).toEqual(original.primary);
    await act(async () => { await setup.mockMouse.release(endX, endY); });
    await finalFrame(setup);
    expect(workbench.layout.secondary.at(-1)?.id).toBe("status");
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("content, the outer frame and collapse clicks do not start pane movement", async () => {
  const { workbench, setup, node } = await fixture();
  try {
    const content = node("status-scroll"), original = structuredClone(workbench.layout);
    await act(async () => { await setup.mockMouse.drag(content.x + 2, content.y, content.x + 5, content.y + 1); });
    await finalFrame(setup);
    expect(workbench.gesture).toBeNull(); expect(workbench.layout.primary).toEqual(original.primary);
    const bench = node("workbench");
    await act(async () => { await setup.mockMouse.drag(bench.x, bench.y + 4, bench.x + 3, bench.y + 5); });
    expect(workbench.gesture).toBeNull(); expect(workbench.layout.primaryWidth).toBe(original.primaryWidth);
    const arrow = node("collapse-status");
    await act(async () => {
      await setup.mockMouse.pressDown(arrow.x, arrow.y);
      await setup.mockMouse.emitMouseEvent("drag", arrow.x + 4, arrow.y + 3);
      await setup.mockMouse.release(arrow.x + 4, arrow.y + 3);
    });
    const frame = await finalFrame(setup);
    expect(workbench.gesture).toBeNull(); expect(workbench.layout.primary[0].collapsed).toBe(true);
    expect(node("status-pane").height).toBe(2); expect(node("title-status").height).toBe(1);
    expect(frame.split("\n")[node("title-status").y]).toContain("▶ Status");
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("horizontal borders resize adjacent panes and their junctions resize columns", async () => {
  const { workbench, setup, node } = await fixture();
  try {
    const bench = node("workbench"), originalWidth = workbench.layout.primaryWidth;
    const bounds = geometry(bench.width, bench.height, workbench.layout);
    const stack = bounds.sashes.find((item) => item.sash.kind === "stack" && item.sash.column === "primary")!;
    const statusHeight = node("status-pane").height, capabilitiesHeight = node("capabilities-pane").height;
    const x = bench.x + stack.rect.x + 3, y = bench.y + stack.rect.y;
    await act(async () => {
      await setup.mockMouse.pressDown(x, y);
      await setup.mockMouse.emitMouseEvent("drag", x, y - 2);
    });
    await finalFrame(setup);
    expect(workbench.gesture?.kind === "resize" && workbench.gesture.sash.kind).toBe("stack");
    expect(node("status-pane").height).toBe(statusHeight - 2);
    expect(node("capabilities-pane").height).toBe(capabilitiesHeight + 2);
    expect(workbench.layout.primaryWidth).toBe(originalWidth);
    await act(async () => { await setup.mockMouse.release(x, y - 2); });
    await finalFrame(setup);
    const junctionX = node("sash-0").x, junctionY = node("capabilities-pane").y;
    const heights = workbench.layout.primary.map((view) => view.expandedHeight);
    await act(async () => {
      await setup.mockMouse.pressDown(junctionX, junctionY);
      await setup.mockMouse.emitMouseEvent("drag", junctionX + 3, junctionY + 2);
    });
    await finalFrame(setup);
    expect(workbench.gesture?.kind === "resize" && workbench.gesture.sash.kind).toBe("primary");
    expect(workbench.layout.primaryWidth).toBe(originalWidth + 3);
    expect(workbench.layout.primary.map((view) => view.expandedHeight)).toEqual(heights);
    await act(async () => { await setup.mockMouse.release(junctionX + 3, junctionY + 2); });
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("a collapsed pane can move by its title and restore its saved height", async () => {
  const { workbench, setup, node } = await fixture();
  try {
    const savedHeight = node("status-pane").height, arrow = node("collapse-status");
    await act(async () => { await setup.mockMouse.click(arrow.x, arrow.y); });
    await finalFrame(setup);
    const title = node("title-status"), destination = node("subagents-pane");
    await act(async () => {
      await setup.mockMouse.drag(title.x + 3, title.y, destination.x + 4, destination.y + destination.height - 2);
    });
    await finalFrame(setup);
    const moved = workbench.layout.secondary.find((view) => view.id === "status")!;
    expect(moved.collapsed).toBe(true); expect(moved.expandedHeight).toBe(savedHeight);
    expect(node("status-pane").height).toBe(2);
    const restore = node("collapse-status");
    await act(async () => { await setup.mockMouse.click(restore.x, restore.y); });
    await finalFrame(setup);
    expect(moved.collapsed).toBe(false); expect(moved.expandedHeight).toBe(savedHeight);
    expect(node("status-pane").height).toBeGreaterThan(2);
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("terminal resizing cancels an active resize and paints the restored layout", async () => {
  const { workbench, setup, node } = await fixture();
  try {
    const originalWidth = workbench.layout.primaryWidth, sash = node("sash-0");
    await act(async () => {
      await setup.mockMouse.pressDown(sash.x, sash.y + 3);
      await setup.mockMouse.emitMouseEvent("drag", sash.x + 5, sash.y + 3);
    });
    await finalFrame(setup); expect(workbench.layout.primaryWidth).toBe(originalWidth + 5);
    await act(async () => { setup.resize(110, 34); });
    await finalFrame(setup);
    expect(workbench.gesture).toBeNull(); expect(workbench.layout.primaryWidth).toBe(originalWidth);
    expect(node("sash-0").x).toBe(originalWidth);
  } finally { await act(async () => { await setup.dispose(); }); }
});
