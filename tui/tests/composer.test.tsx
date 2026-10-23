import { expect, test } from "bun:test";
import { act } from "react";
import type { TextareaRenderable } from "@opentui/core";
import { testRender } from "@opentui/react/test-utils";
import { SessionController } from "../src/session/controller";
import { TuiShell } from "../src/shells/TuiShell";
import { ReplShell } from "../src/shells/ReplShell";
import { WorkbenchState } from "../src/workbench/layout";
import { finalFrame } from "./render-helpers";

for (const [mode, accept] of [["tui", "tab"], ["tui", "enter"], ["tui", "mouse"], ["repl", "tab"], ["repl", "enter"]] as const) {
  test(`${mode}: ${accept} completion places the cursor last and survives editing and clearing`, async () => {
    const controller = new SessionController(); controller.connected = true; controller.notice = "";
    controller.commands = { run: "Run a task" };
    const shell = mode === "tui" ? <TuiShell controller={controller} workbench={new WorkbenchState()} /> : <ReplShell controller={controller} />;
    const setup = await testRender(shell, { width: 120, height: 36, kittyKeyboard: true, useMouse: true,
      screenMode: mode === "repl" ? "split-footer" : "alternate-screen", footerHeight: 12,
      externalOutputMode: mode === "repl" ? "capture-stdout" : "passthrough" });
    const editor = () => setup.renderer.root.findDescendantById("composer-editor") as TextareaRenderable;
    try {
      await finalFrame(setup);
      await act(async () => { await setup.mockInput.typeText("/ru"); }); await finalFrame(setup);
      await act(async () => {
        if (accept === "tab") setup.mockInput.pressTab();
        else if (accept === "enter") setup.mockInput.pressEnter();
        else {
          const menu = setup.renderer.root.findDescendantById("completion-menu")!;
          await setup.mockMouse.click(menu.x + 4, menu.y + 1);
        }
        expect(editor().plainText).toBe("/run ");
        expect(editor().cursorOffset).toBe(5);
        await setup.mockInput.typeText("target.test");
      }); await finalFrame(setup);
      expect(controller.draft).toBe("/run target.test");
      expect(editor().cursorOffset).toBe(controller.draft.length);
      await act(async () => {
        editor().gotoBufferHome({ select: true }); setup.mockInput.pressBackspace();
      }); await finalFrame(setup);
      expect(controller.draft).toBe(""); expect(editor().plainText).toBe("");
      await act(async () => { await setup.mockInput.typeText("/ru"); }); await finalFrame(setup);
      expect(setup.renderer.root.findDescendantById("completion-menu")).toBeDefined();
      await act(async () => { setup.mockInput.pressTab(); }); await finalFrame(setup);
      expect(controller.draft).toBe("/run "); expect(editor().cursorOffset).toBe(5);
      await act(async () => { setup.mockInput.pressEscape(); }); await finalFrame(setup);
      expect(controller.draft).toBe("");
      await act(async () => { await setup.mockInput.typeText("/ru"); }); await finalFrame(setup);
      await act(async () => { setup.mockInput.pressTab(); }); await finalFrame(setup);
      expect(controller.draft).toBe("/run "); expect(editor().cursorOffset).toBe(5);
    } finally { await act(async () => { await setup.dispose(); }); }
  });
}

for (const mode of ["tui", "repl"] as const) test(`${mode}: completion suggestions sit above the input border and preserve their placement after resizing`, async () => {
  const controller = new SessionController(); controller.connected = true; controller.notice = "";
  controller.commands = { run: "Run a task" }; controller.draft = "/ru";
  const shell = mode === "tui" ? <TuiShell controller={controller} workbench={new WorkbenchState()} /> : <ReplShell controller={controller} />;
  const setup = await testRender(shell, { width: 120, height: 36, kittyKeyboard: true,
    screenMode: mode === "repl" ? "split-footer" : "alternate-screen", footerHeight: 12,
    externalOutputMode: mode === "repl" ? "capture-stdout" : "passthrough" });
  try {
    for (const width of [120, 90]) {
      await act(async () => { setup.resize(width, 36); });
      const frame = await finalFrame(setup);
      const menu = setup.renderer.root.findDescendantById("completion-menu")!;
      const input = setup.renderer.root.findDescendantById("composer-input")!;
      const editor = setup.renderer.root.findDescendantById("composer-editor")!;
      expect(menu.y + menu.height).toBe(input.y);
      expect(editor.y).toBe(input.y + 1);
      expect(frame.split("\n")[input.y]).toMatch(mode === "tui" ? /^├─+┤$/ : /^─+$/);
    }
  } finally { await act(async () => { await setup.dispose(); }); }
});
