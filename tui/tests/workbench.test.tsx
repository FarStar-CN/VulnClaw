import { expect, test } from "bun:test";
import { act } from "react";
import { testRender } from "@opentui/react/test-utils";
import { RGBA } from "@opentui/core";
import stringWidth from "string-width";
import { SessionController } from "../src/session/controller";
import { TuiShell } from "../src/shells/TuiShell";
import { geometry, WorkbenchState } from "../src/workbench/layout";
import { finalFrame } from "./render-helpers";
import { palettes } from "../src/theme/palette";

type RenderSetup = Awaited<ReturnType<typeof testRender>>;

function cellAt(setup: RenderSetup, x: number, y: number, frame = setup.captureSpans()) {
  let end = 0;
  return frame.lines[y].spans.find((span) => { end += span.width; return x < end; });
}

function glyphAt(row: string, column: number) {
  for (const character of row) {
    const width = stringWidth(character);
    if (column < width) return character;
    column -= width;
  }
}

function expectBorderBackgrounds(setup: RenderSetup, workbench: WorkbenchState) {
  const bench = setup.renderer.root.findDescendantById("workbench")!;
  const header = setup.renderer.root.findDescendantById("terminal-header")!;
  const borders = geometry(bench.width, bench.height, workbench.layout).borders.map(({ rect }) => ({
    ...rect, x: bench.x + rect.x, y: bench.y + rect.y,
  }));
  borders.push(
    { x: header.x, y: header.y, width: header.width, height: 1 },
    { x: header.x, y: header.y + 1, width: 1, height: header.height - 1 },
    { x: header.x + header.width - 1, y: header.y + 1, width: 1, height: header.height - 1 },
  );
  const frame = setup.captureSpans(), colors = new Set<string>();
  for (const rect of borders) for (let y = rect.y; y < rect.y + rect.height; y++) {
    for (let x = rect.x; x < rect.x + rect.width; x++) {
      colors.add(cellAt(setup, x, y, frame)!.bg.toInts().join(","));
    }
  }
  expect(colors).toEqual(new Set([RGBA.fromHex("#181a1f").toInts().join(",")]));
}

async function fixture(width = 120) {
  const controller = new SessionController(); controller.connected = true; controller.notice = "";
  const workbench = new WorkbenchState();
  const setup = await testRender(<TuiShell controller={controller} workbench={workbench} />,
    { width, height: 36, kittyKeyboard: true });
  const node = (id: string) => setup.renderer.root.findDescendantById(id)!;
  await finalFrame(setup);
  return { controller, workbench, setup, node };
}

test.each([120, 64])("background zones and wide disclosure icons preserve the thin borders at %i columns", async (width) => {
  const { controller, workbench, setup, node } = await fixture(width);
  try {
    const frame = (await finalFrame(setup)).split("\n");
    const bench = node("workbench"), header = node("terminal-header"), input = node("composer-input");
    expect(bench.y).toBe(header.y + header.height);
    expect(frame[bench.y]).toMatch(/^├─+┬─+┬─+┤$/);
    expect(frame[bench.y + bench.height - 1]).toMatch(/^└─+┴─+┴─+┘$/);
    expect(input.y).toBe(bench.y + bench.height);
    expect(node("composer-editor").y).toBe(input.y + 1);
    expect(frame[input.y]).toMatch(/^┌─+┐$/);
    expect(frame[input.y + input.height - 1]).toMatch(/^└─+┘$/);
    for (const id of ["status", "capabilities", "output", "findings", "subagents"]) {
      const pane = node(`${id}-pane`), title = node(`title-${id}`);
      expect(title.y).toBe(pane.y + 1); expect(title.height).toBe(1);
      expect(glyphAt(frame[title.y], title.x - 1)).toBe("│");
      if (id !== "output") {
        const arrow = node(`collapse-${id}`);
        expect(arrow.width).toBe(2);
        expect(glyphAt(frame[title.y], arrow.x)).toBe("﹀");
      }
    }
    expect(node("status-scroll").y).toBe(node("title-status").y + 1);
    const sash = node("sash-0");
    expect(sash.width).toBe(1);
    expect(glyphAt(frame[node("title-status").y], sash.x)).toBe("│");
    expect(glyphAt(frame[node("capabilities-pane").y], sash.x)).toBe("┤");
    expect([input.x, input.y, input.width, input.height]).toEqual([0, 31, width, 3]);
    for (const theme of ["blue", "orange"] as const) {
      await act(async () => { controller.theme = theme; controller.changed(); });
      await finalFrame(setup);
      const p = palettes[theme], output = node("output-pane"), status = node("status-pane"), title = node("title-status");
      expect(cellAt(setup, output.x + 2, output.y + 3)?.bg).toEqual(RGBA.fromHex("#1e2127"));
      expect(cellAt(setup, node("title-output").x + 2, node("title-output").y)?.bg).toEqual(RGBA.fromHex("#1e2127"));
      expect(cellAt(setup, status.x + 2, status.y + 3)?.bg).toEqual(RGBA.fromHex("#202329"));
      expect(cellAt(setup, title.x + 2, title.y)?.bg).toEqual(RGBA.fromHex("#262c36"));
      expect(cellAt(setup, header.x + 15, header.y + 1)?.bg).toEqual(RGBA.fromHex("#181a1f"));
      expect(cellAt(setup, status.x, title.y)?.fg).toEqual(RGBA.fromHex(p.border));
      expect(cellAt(setup, input.x + 2, input.y + 1)?.bg).toEqual(RGBA.fromHex("#2a2e36"));
      expect(cellAt(setup, input.x + 2, input.y)?.fg).toEqual(RGBA.fromHex(p.composerBorder));
      expectBorderBackgrounds(setup, workbench);
    }
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
    expectBorderBackgrounds(setup, workbench);
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
      await setup.mockMouse.pressDown(arrow.x + 1, arrow.y);
      await setup.mockMouse.emitMouseEvent("drag", arrow.x + 4, arrow.y + 3);
      await setup.mockMouse.release(arrow.x + 4, arrow.y + 3);
    });
    const frame = await finalFrame(setup);
    expect(workbench.gesture).toBeNull(); expect(workbench.layout.primary[0].collapsed).toBe(true);
    expect(node("status-pane").height).toBe(2); expect(node("title-status").height).toBe(1);
    expect(frame.split("\n")[node("title-status").y]).toContain("〉Status");
    expectBorderBackgrounds(setup, workbench);
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
    expectBorderBackgrounds(setup, workbench);
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
    expectBorderBackgrounds(setup, workbench);
  } finally { await act(async () => { await setup.dispose(); }); }
});
