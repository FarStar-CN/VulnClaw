import { expect, spyOn, test } from "bun:test";
import { act } from "react";
import type { TextareaRenderable } from "@opentui/core";
import { testRender } from "@opentui/react/test-utils";
import { SessionController } from "../src/session/controller";
import { TuiShell } from "../src/shells/TuiShell";
import { ReplShell } from "../src/shells/ReplShell";
import { WorkbenchState } from "../src/workbench/layout";
import { finalFrame } from "./render-helpers";

test("opening settings clears a command submitted in one input packet before returning to compose", async () => {
  const controller = new SessionController(); controller.connected = true;
  const control = spyOn(controller, "control").mockResolvedValue({ config: {}, schema: {} });
  const setup = await testRender(<TuiShell controller={controller} workbench={new WorkbenchState()} />,
    { width: 120, height: 40, kittyKeyboard: true });
  try {
    await finalFrame(setup);
    await act(async () => { setup.mockInput.pressKey("/settings\r"); });
    await finalFrame(setup);
    expect(controller.overlay?.kind).toBe("config");
    const editor = setup.renderer.root.findDescendantById("composer-editor") as TextareaRenderable;
    expect(editor.plainText).toBe(""); expect(controller.draft).toBe("");
    await act(async () => { controller.dismiss(); }); await finalFrame(setup);
    await act(async () => { await setup.mockInput.typeText("next"); }); await finalFrame(setup);
    expect(editor.plainText).toBe("next"); expect(controller.draft).toBe("next");
  } finally { control.mockRestore(); await act(async () => { await setup.dispose(); }); }
});

for (const mode of ["tui", "repl"] as const) test(`${mode}: theme saves consume the command before awaiting the backend and preserve subsequent input`, async () => {
  const controller = new SessionController(); controller.connected = true;
  let finish!: (document: Record<string, unknown>) => void;
  const control = spyOn(controller, "control").mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const shell = mode === "tui" ? <TuiShell controller={controller} workbench={new WorkbenchState()} /> : <ReplShell controller={controller} />;
  const setup = await testRender(shell, { width: 120, height: 40, kittyKeyboard: true,
    screenMode: "alternate-screen",
    externalOutputMode: "passthrough" });
  try {
    await finalFrame(setup);
    await act(async () => { setup.mockInput.pressKey("/theme orange\r"); }); await finalFrame(setup);
    const editor = setup.renderer.root.findDescendantById("composer-editor") as TextareaRenderable;
    expect(editor.plainText).toBe(""); expect(controller.draft).toBe("");
    await act(async () => { await setup.mockInput.typeText("next"); }); await finalFrame(setup);
    await act(async () => { finish({}); }); await finalFrame(setup);
    expect(editor.plainText).toBe("next"); expect(controller.draft).toBe("next");
  } finally { control.mockRestore(); await act(async () => { await setup.dispose(); }); }
});

for (const [mode, accept] of [["tui", "tab"], ["tui", "enter"], ["tui", "mouse"], ["repl", "tab"], ["repl", "enter"]] as const) {
  test(`${mode}: ${accept} completion places the cursor last and survives editing and clearing`, async () => {
    const controller = new SessionController(); controller.connected = true; controller.notice = "";
    controller.commands = { run: "Run a task" };
    const shell = mode === "tui" ? <TuiShell controller={controller} workbench={new WorkbenchState()} /> : <ReplShell controller={controller} />;
    const setup = await testRender(shell, { width: 120, height: 36, kittyKeyboard: true, useMouse: true,
      screenMode: "alternate-screen",
      externalOutputMode: "passthrough" });
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
    screenMode: "alternate-screen",
    externalOutputMode: "passthrough" });
  try {
    for (const width of [120, 90]) {
      await act(async () => { setup.resize(width, 36); });
      const frame = await finalFrame(setup);
      const menu = setup.renderer.root.findDescendantById("completion-menu")!;
      const input = setup.renderer.root.findDescendantById("composer-input")!;
      const editor = setup.renderer.root.findDescendantById("composer-editor")!;
      expect(menu.y + menu.height).toBe(input.y);
      expect(editor.y).toBe(input.y + 1);
      expect(frame.split("\n")[input.y]).toMatch(mode === "tui" ? /^┌─+┐$/ : /^─+$/);
    }
  } finally { await act(async () => { await setup.dispose(); }); }
});

for (const mode of ["tui", "repl"] as const) test(`${mode}: completion selection stays in the third row between fixed list boundaries`, async () => {
  const controller = new SessionController(); controller.connected = true;
  const names = ["pick00", "pick01", "pick02", "pick10", "pick11", "pick12", "pick13", "pick14", "pick15", "pick16"];
  controller.commands = Object.fromEntries(names.map((name) => [name, "Pick a command"]));
  controller.draft = "/pick";
  const shell = mode === "tui" ? <TuiShell controller={controller} workbench={new WorkbenchState()} /> : <ReplShell controller={controller} />;
  const setup = await testRender(shell, { width: 120, height: 36, kittyKeyboard: true,
    screenMode: "alternate-screen", externalOutputMode: "passthrough" });
  const checkMenu = async (visible: string[], selectedRow: number) => {
    const lines = (await finalFrame(setup)).split("\n");
    const menu = setup.renderer.root.findDescendantById("completion-menu")!;
    expect(menu.height).toBe(visible.length + 1);
    const rows = lines.slice(menu.y + 1, menu.y + menu.height);
    expect(rows.map((line) => line.match(/\/(pick\d+)/)?.[1])).toEqual(visible);
    expect(rows.map((line) => line.trimStart().startsWith("›")))
      .toEqual(visible.map((_, index) => index === selectedRow));
  };
  try {
    await checkMenu(names.slice(0, 5), 0);
    const editor = setup.renderer.root.findDescendantById("composer-editor") as TextareaRenderable;
    editor.gotoBufferEnd();
    // Each expected window is explicit, including both ends, wrapping and reverse movement.
    for (const [key, begin, row] of [
      ["down", 0, 1], ["down", 0, 2], ["down", 1, 2], ["down", 2, 2],
      ["down", 3, 2], ["down", 4, 2], ["down", 5, 2], ["down", 5, 3],
      ["down", 5, 4], ["down", 0, 0], ["up", 5, 4], ["up", 5, 3],
      ["up", 5, 2], ["up", 4, 2], ["up", 3, 2],
    ] as const) {
      await act(async () => { setup.mockInput.pressArrow(key); });
      await checkMenu(names.slice(begin, begin + 5), row);
      expect(controller.draft).toBe("/pick");
    }
    await act(async () => { await setup.mockInput.typeText("0"); });
    await checkMenu(names.slice(0, 3), 0);
    await act(async () => { setup.mockInput.pressArrow("up"); });
    await checkMenu(names.slice(0, 3), 2);
    await act(async () => { setup.mockInput.pressArrow("down"); });
    await checkMenu(names.slice(0, 3), 0);
    await act(async () => { setup.mockInput.pressArrow("down"); });
    await checkMenu(names.slice(0, 3), 1);
    await act(async () => { await setup.mockInput.typeText("2"); });
    await checkMenu(["pick02"], 0);
  } finally { await act(async () => { await setup.dispose(); }); }
});

for (const mode of ["tui", "repl"] as const) test(`${mode}: Tab, Enter and mouse accept the correct command in a scrolled completion window`, async () => {
  const controller = new SessionController(); controller.connected = true;
  controller.commands = Object.fromEntries(Array.from({ length: 10 }, (_, index) => [`pick${index}`, "Pick a command"]));
  const shell = mode === "tui" ? <TuiShell controller={controller} workbench={new WorkbenchState()} /> : <ReplShell controller={controller} />;
  const setup = await testRender(shell, { width: 120, height: 36, kittyKeyboard: true, useMouse: true,
    screenMode: "alternate-screen", externalOutputMode: "passthrough" });
  try {
    for (const accept of ["tab", "enter", "mouse"] as const) {
      await act(async () => { controller.draft = "/pick"; controller.changed(); });
      await finalFrame(setup);
      await act(async () => { for (let i = 0; i < 6; i++) setup.mockInput.pressArrow("down"); });
      const lines = (await finalFrame(setup)).split("\n");
      const menu = setup.renderer.root.findDescendantById("completion-menu")!;
      expect(lines[menu.y + 3]).toContain("› /pick6");
      await act(async () => {
        if (accept === "tab") setup.mockInput.pressTab();
        else if (accept === "enter") setup.mockInput.pressEnter();
        else await setup.mockMouse.click(menu.x + 4, menu.y + 4);
      });
      await finalFrame(setup);
      const expected = accept === "mouse" ? "/pick7 " : "/pick6 ";
      const editor = setup.renderer.root.findDescendantById("composer-editor") as TextareaRenderable;
      expect(controller.draft).toBe(expected);
      expect(editor.plainText).toBe(expected);
      expect(editor.cursorOffset).toBe(expected.length);
      expect(controller.history).toEqual([]);
      if (accept === "tab") {
        await act(async () => { setup.mockInput.pressTab(); });
        const frame = await finalFrame(setup);
        expect(controller.draft).toBe("/pick7 ");
        expect(frame.split("\n")[menu.y + 3]).toContain("› /pick7");
      }
    }
  } finally { await act(async () => { await setup.dispose(); }); }
});
