import { expect, test, spyOn } from "bun:test";
import { act } from "react";
import { testRender } from "@opentui/react/test-utils";
import * as clipboard from "../src/platform/clipboard";
import { DataDialog } from "../src/components/DataDialog";
import { SessionController } from "../src/session/controller";
import { finalFrame } from "./render-helpers";

test("learning data remains scrollable, copies its content and closes with Escape", async () => {
  const text = JSON.stringify({ lesson: "中文 lesson", lines: Array.from({ length: 30 }, (_, i) => `record ${i}`) }, null, 2);
  let notice = "", closed = false;
  const copy = spyOn(clipboard, "copyText").mockImplementation(async () => "copied");
  const setup = await testRender(<DataDialog title="learn" text={text} active theme="blue"
    onClose={() => { closed = true; }} onNotify={(message) => { notice = message; }} />, { width: 45, height: 18, kittyKeyboard: true });
  try {
    expect(await finalFrame(setup)).toContain("中文 lesson");
    await act(async () => { setup.mockInput.pressKey("m"); });
    expect(copy.mock.calls[0]?.[1]).toBe(text); expect(notice).toBe("copied");
    await act(async () => { setup.mockInput.pressKey("END"); setup.mockInput.pressEscape(); });
    expect(closed).toBe(true);
  } finally { copy.mockRestore(); await act(async () => { await setup.dispose(); }); }
});

for (const operation of ["learn", "feedback"]) test(`${operation} keeps its data dialog`, () => {
  const controller = new SessionController();
  controller.receive({ protocol_version: 2, type: "control_result", operation, result: { message: "Saved data" } });
  expect(controller.overlay).toEqual({ kind: "data", title: operation, data: { message: "Saved data" } });
});
