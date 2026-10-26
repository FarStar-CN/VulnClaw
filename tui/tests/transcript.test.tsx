import { expect, test } from "bun:test";
import { act } from "react";
import { TextBufferRenderable, type Renderable, type ScrollBoxRenderable } from "@opentui/core";
import { testRender } from "@opentui/react/test-utils";
import { SessionController } from "../src/session/controller";
import { TuiShell } from "../src/shells/TuiShell";
import { ReplShell } from "../src/shells/ReplShell";
import { WorkbenchState } from "../src/workbench/layout";
import { finalFrame } from "./render-helpers";

for (const mode of ["tui", "repl"] as const) test(`${mode}: role labels preserve the conversation and composer draft`, async () => {
  const controller = new SessionController(); controller.connected = true; controller.notice = "";
  controller.draft = "preserved draft";
  const shell = mode === "tui" ? <TuiShell controller={controller} workbench={new WorkbenchState()} /> : <ReplShell controller={controller} />;
  const setup = await testRender(shell, { width: 120, height: 40, screenMode: "alternate-screen",
    externalOutputMode: "passthrough" });
  try {
    for (const role of ["user", "assistant", "tool", "reasoning"] as const) {
      await act(async () => {
        controller.messages.clear(); controller.entries = [];
        controller.receive({ protocol_version: 2, type: "message", task_id: "fixture", message_id: role,
          source: "main", role, format: "text", operation: "append", text: "Conversation content" });
      });
      await finalFrame(setup);
      const label = setup.renderer.root.findDescendantById(`entry-${role}`)!.getChildren()[0];
      await act(async () => { await setup.mockMouse.click(label.x + 1, label.y); });
      expect(controller.overlay).toBeNull();
      expect(controller.messages.get(role)?.text).toBe("Conversation content");
      expect(controller.draft).toBe("preserved draft");
      expect(await finalFrame(setup)).toContain("preserved draft");
    }
  } finally { await act(async () => { await setup.dispose(); }); }
});

function overflowingText(node: Renderable): TextBufferRenderable | undefined {
  if (node instanceof TextBufferRenderable && node.maxScrollY > 0) return node;
  for (const child of node.getChildren()) {
    const found = overflowingText(child); if (found) return found;
  }
}

for (const kind of ["log", "status"] as const) test(`${kind}: overflowing message forwards the wheel to the transcript without scrolling itself`, async () => {
  const controller = new SessionController(); controller.connected = true; controller.notice = "";
  controller.draft = "preserved draft";
  const preview = "overflow preview ".repeat(50);
  if (kind === "log") controller.receive({ protocol_version: 2, type: kind, task_id: "fixture", message: preview });
  else if (kind === "status") controller.receive({ protocol_version: 2, type: kind, task_id: "fixture", status: preview });
  else controller.receive({ protocol_version: 2, type: kind, task_id: "fixture", tool: "evidence_search", arguments: preview });
  controller.receive({ protocol_version: 2, type: "message", task_id: "fixture", message_id: "long", source: "main",
    role: "assistant", format: "text", operation: "append", text: Array.from({ length: 100 }, (_, i) => `line ${i}`).join("\n") });
  const workbench = new WorkbenchState();
  const setup = await testRender(<TuiShell controller={controller} workbench={workbench} />,
    { width: 120, height: 36, useMouse: true, kittyKeyboard: true });
  try {
    await finalFrame(setup);
    const scroll = setup.renderer.root.findDescendantById("transcript-scroll") as ScrollBoxRenderable;
    scroll.scrollTo(0); await finalFrame(setup);
    const card = [...controller.cards.values()][0];
    const entry = setup.renderer.root.findDescendantById(`entry-${card.id}`)!;
    const text = overflowingText(entry)!;
    expect(text).toBeDefined(); expect(text.maxScrollY).toBeGreaterThan(0);
    const before = scroll.scrollTop;
    await act(async () => { await setup.mockMouse.scroll(text.x + 2, text.y, "down"); }); await finalFrame(setup);
    expect(text.scrollY).toBe(0); expect(text.scrollX).toBe(0);
    expect(scroll.scrollTop).toBeGreaterThan(before);
    expect(controller.draft).toBe("preserved draft"); expect(controller.inputFocused).toBe(true);
    scroll.scrollTo(0); await finalFrame(setup);
    await act(async () => { await setup.mockMouse.click(text.x + 2, text.y); }); await finalFrame(setup);
    expect(controller.inputFocused).toBe(false); expect(workbench.layout.focus).toBe("output");
    await act(async () => { setup.mockInput.pressArrow("down"); }); await finalFrame(setup);
    expect(scroll.scrollTop).toBeGreaterThan(0);
    expect(text.scrollY).toBe(0);
  } finally { await act(async () => { await setup.dispose(); }); }
});
