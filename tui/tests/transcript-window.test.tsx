import { expect, test } from "bun:test";
import { act } from "react";
import { TextBufferRenderable, type Renderable, type ScrollBoxRenderable } from "@opentui/core";
import { testRender } from "@opentui/react/test-utils";
import { SessionController } from "../src/session/controller";
import { TuiShell } from "../src/shells/TuiShell";
import { WorkbenchState } from "../src/workbench/layout";
import { finalFrame } from "./render-helpers";
import { Transcript } from "../src/components/Transcript";

function seed(controller: SessionController, count: number, source = "main") {
  for (let i = 0; i < count; i++) {
    controller.receive({ protocol_version: 2, type: "message", task_id: "fixture",
      message_id: `${source}-${i}`, source, role: "assistant", format: "text",
      operation: "append", text: `${source} history ${i}\nsecond line\nthird line` });
    controller.receive({ protocol_version: 2, type: "message", task_id: "fixture",
      message_id: `${source}-${i}`, source, role: "assistant", format: "text",
      operation: "end", status: "completed" });
  }
}

async function settle(setup: Awaited<ReturnType<typeof testRender>>) {
  for (let pass = 0; pass < 6; pass++) await finalFrame(setup);
  return setup.captureCharFrame();
}

test("large transcript only mounts its window and can return to both ends", async () => {
  const controller = new SessionController(); controller.connected = true; controller.notice = "";
  seed(controller, 1000);
  const setup = await testRender(<TuiShell controller={controller} workbench={new WorkbenchState()} />,
    { width: 120, height: 40 });
  const scroll = () => setup.renderer.root.findDescendantById("transcript-scroll") as ScrollBoxRenderable;
  try {
    expect(await settle(setup)).toContain("main history 999");
    expect(scroll().getChildren().length).toBeLessThan(100);
    expect(setup.renderer.root.findDescendantById("entry-main-0")).toBeUndefined();
    expect(controller.messages.size).toBe(1000);
    expect(controller.entries).toHaveLength(1000);
    await act(async () => { scroll().scrollTo(0); });
    expect(await settle(setup)).toContain("main history 0");
    expect(setup.renderer.root.findDescendantById("entry-main-999")).toBeUndefined();
    await act(async () => { scroll().scrollTo(scroll().scrollHeight / 2); });
    expect(await settle(setup)).toContain("main history");
    await act(async () => { scroll().scrollTo(scroll().scrollHeight); });
    expect(await settle(setup)).toContain("main history 999");
    expect(scroll().getChildren().length).toBeLessThan(100);
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("windowed sources restore their reading anchor when new messages arrive", async () => {
  const controller = new SessionController(); controller.connected = true; controller.notice = "";
  seed(controller, 150); seed(controller, 60, "child");
  const setup = await testRender(<TuiShell controller={controller} workbench={new WorkbenchState()} />,
    { width: 120, height: 40 });
  const scroll = () => setup.renderer.root.findDescendantById("transcript-scroll") as ScrollBoxRenderable;
  try {
    await settle(setup);
    await act(async () => { scroll().scrollTo(0); });
    const before = await settle(setup);
    await act(async () => { controller.source = "child"; controller.changed(); });
    expect(await settle(setup)).toContain("child history 59");
    await act(async () => { controller.source = "main"; controller.changed(); });
    expect(await settle(setup)).toBe(before);
    await act(async () => { controller.receive({ protocol_version: 2, type: "message", task_id: "fixture",
      message_id: "new-main", source: "main", role: "assistant", format: "text", operation: "append", text: "new content below" }); });
    expect(await settle(setup)).toBe(before);
    await act(async () => { setup.resize(90, 32); });
    expect(await settle(setup)).toContain("main history 0");
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("growth above the viewport compensates height without moving the reading anchor", async () => {
  const controller = new SessionController(); controller.connected = true; controller.notice = "";
  seed(controller, 200);
  const setup = await testRender(<TuiShell controller={controller} workbench={new WorkbenchState()} />,
    { width: 120, height: 40 });
  try {
    await settle(setup);
    const scroll = setup.renderer.root.findDescendantById("transcript-scroll") as ScrollBoxRenderable;
    await act(async () => { scroll.scrollTo(scroll.scrollHeight / 2); }); await settle(setup);
    const nodes = scroll.getChildren().filter((node) => node.id.startsWith("transcript-item-"));
    const anchor = nodes.find((node) => node.y + node.height > scroll.viewport.y && node.y <= scroll.viewport.y)!;
    const above = nodes.filter((node) => node.y + node.height <= scroll.viewport.y).at(-1)!;
    const before = anchor.y;
    const id = above.id.slice("transcript-item-".length);
    await act(async () => {
      controller.receive({ protocol_version: 2, type: "message", task_id: "fixture", message_id: id,
        source: "main", role: "assistant", format: "text", operation: "append", text: "\nmore content".repeat(40) });
      controller.receive({ protocol_version: 2, type: "message", task_id: "fixture", message_id: id,
        source: "main", role: "assistant", format: "text", operation: "end", status: "completed" });
    });
    await settle(setup);
    expect(setup.renderer.root.findDescendantById(anchor.id)!.y).toBe(before);
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("a batched new session forgets old anchors even when message IDs are reused", async () => {
  const controller = new SessionController(); controller.connected = true; controller.notice = "";
  seed(controller, 200);
  const setup = await testRender(<TuiShell controller={controller} workbench={new WorkbenchState()} />,
    { width: 120, height: 40 });
  try {
    await settle(setup);
    const scroll = setup.renderer.root.findDescendantById("transcript-scroll") as ScrollBoxRenderable;
    await act(async () => { scroll.scrollTo(0); }); await settle(setup);
    await act(async () => {
      controller.receive({ protocol_version: 2, type: "control_result", operation: "session.new", result: {} });
      controller.receive({ protocol_version: 2, type: "message", task_id: "new", message_id: "main-0", source: "main",
        role: "assistant", format: "text", operation: "append", text: Array.from({ length: 100 }, (_, i) => `new line ${i}`).join("\n") });
    });
    expect(await settle(setup)).toContain("new line 99");
  } finally { await act(async () => { await setup.dispose(); }); }
});

for (const kind of ["reasoning", "log", "execution"] as const) test(`${kind} disclosure survives virtual unmount`, async () => {
  const controller = new SessionController(); controller.connected = true; controller.notice = "";
  seed(controller, 200);
  if (kind === "reasoning") controller.receive({ protocol_version: 2, type: "message", task_id: "fixture",
    message_id: "detail", source: "main", role: "reasoning", format: "text", operation: "append", text: "retained detail" });
  else if (kind === "log") controller.receive({ protocol_version: 2, type: "log", task_id: "fixture", message: "preview\nretained detail" });
  else {
    controller.receive({ protocol_version: 2, type: "tool_call", task_id: "fixture", call_id: "detail", tool: "custom", arguments: "{}" });
    controller.receive({ protocol_version: 2, type: "tool_result", task_id: "fixture", call_id: "detail", result: "retained detail" });
  }
  const id = controller.entries.at(-1)!.id;
  const setup = await testRender(<TuiShell controller={controller} workbench={new WorkbenchState()} />,
    { width: 120, height: 40 });
  const scroll = () => setup.renderer.root.findDescendantById("transcript-scroll") as ScrollBoxRenderable;
  try {
    await settle(setup);
    const entry = setup.renderer.root.findDescendantById(`entry-${id}`)!;
    const label = kind === "execution" ? setup.renderer.root.findDescendantById(`execution-${id}`)!
      : entry.getChildren().find((node) => node.getChildren().length)!.getChildren()[0]!;
    await act(async () => { await setup.mockMouse.click(label.x + 1, label.y); setup.renderer.clearSelection(); });
    expect(await settle(setup)).toContain("retained detail");
    await act(async () => { scroll().scrollTo(0); }); await settle(setup);
    expect(setup.renderer.root.findDescendantById(`entry-${id}`) === undefined).toBe(true);
    await act(async () => { scroll().scrollTo(scroll().scrollHeight); });
    expect(await settle(setup)).toContain("retained detail");
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("selection pins only selected entries until it is cleared", async () => {
  const controller = new SessionController(); seed(controller, 200);
  const setup = await testRender(<Transcript controller={controller} width={70} positions={new Map()} />,
    { width: 72, height: 18 });
  const scroll = () => setup.renderer.root.findDescendantById("transcript-scroll") as ScrollBoxRenderable;
  const textIn = (node: Renderable): TextBufferRenderable | undefined => {
    if (node instanceof TextBufferRenderable) return node;
    for (const child of node.getChildren()) { const found = textIn(child); if (found) return found; }
  };
  try {
    await settle(setup);
    const text = textIn(setup.renderer.root.findDescendantById("entry-main-199")!)!;
    await act(async () => {
      setup.renderer.startSelection(text, text.x, text.y);
      setup.renderer.updateSelection(text, text.x + 4, text.y, { finishDragging: true });
    });
    await settle(setup);
    const selected = setup.renderer.getSelection()!.getSelectedText();
    expect(selected).toContain("main");
    await act(async () => { scroll().scrollTo(0); }); await settle(setup);
    expect(setup.renderer.root.findDescendantById("entry-main-199")).toBeDefined();
    expect(setup.renderer.root.findDescendantById("entry-main-100")).toBeUndefined();
    expect(setup.renderer.getSelection()!.getSelectedText()).toBe(selected);
    const replacement = textIn(setup.renderer.root.findDescendantById("entry-main-0")!)!;
    await act(async () => {
      setup.renderer.startSelection(replacement, replacement.x, replacement.y);
      setup.renderer.updateSelection(replacement, replacement.x + 3, replacement.y, { finishDragging: true });
    });
    await settle(setup);
    expect(setup.renderer.root.findDescendantById("entry-main-199") === undefined).toBe(true);
    await act(async () => { setup.renderer.clearSelection(); }); await settle(setup);
    expect(setup.renderer.root.findDescendantById("entry-main-199")).toBeUndefined();
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("Alt navigation crosses window boundaries before expanding its target", async () => {
  const controller = new SessionController(); controller.inputFocused = false;
  for (let i = 0; i < 60; i++) {
    controller.receive({ protocol_version: 2, type: "tool_call", task_id: "fixture", call_id: `call-${i}`, tool: "custom", arguments: "{}" });
    controller.receive({ protocol_version: 2, type: "tool_result", task_id: "fixture", call_id: `call-${i}`, result: `detail of call ${i}` });
  }
  const setup = await testRender(<Transcript controller={controller} width={70} positions={new Map()} focused />,
    { width: 72, height: 16, kittyKeyboard: true });
  try {
    await settle(setup);
    const scroll = setup.renderer.root.findDescendantById("transcript-scroll") as ScrollBoxRenderable;
    await act(async () => { scroll.scrollTo(0); }); await settle(setup);
    for (let i = 0; i < 35; i++) {
      await act(async () => { setup.mockInput.pressArrow("down", { meta: true }); }); await settle(setup);
    }
    await act(async () => { setup.mockInput.pressEnter(); });
    expect(await settle(setup)).toContain("detail of call 34");
  } finally { await act(async () => { await setup.dispose(); }); }
});
