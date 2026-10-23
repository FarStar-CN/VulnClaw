import { expect, test, spyOn } from "bun:test";
import { act } from "react";
import { testRender } from "@opentui/react/test-utils";
import * as clipboard from "../src/platform/clipboard";
import { Reader } from "../src/components/Reader";
import { SessionController } from "../src/session/controller";
import { paneText } from "../src/platform/pane-copy";
import { finalFrame } from "./render-helpers";

test("MD-12/14: reader copies the source, code whitespace and visible pane separately", async () => {
  const text = "# 中文 source\n\n```python\n\tvalue = 1  \n\n    print(value)\n```\n";
  const controller = new SessionController(); const copied: string[] = [];
  const copy = spyOn(clipboard, "copyText").mockImplementation(async (_renderer, value) => { copied.push(value); return "copied"; });
  const setup = await testRender(<Reader controller={controller} title="Report" text={text} format="markdown" active width={45} />, { width: 45, height: 18, kittyKeyboard: true });
  try {
    await finalFrame(setup);
    await act(async () => { setup.mockInput.pressKey("m"); setup.mockInput.pressKey("c"); });
    expect(copied).toEqual([text, "\tvalue = 1  \n\n    print(value)\n"]);
    const pane = setup.renderer.root.findDescendantById("reader-pane")!;
    const visible = paneText(setup.renderer, { x: pane.x, y: pane.y, width: pane.width, height: pane.height });
    expect(visible).toContain("┌"); expect(visible).toContain("Report"); expect(visible).toContain("中文 source");
    expect(visible).not.toBe(text); expect(visible).not.toBe(copied[1]);
    await act(async () => { setup.mockInput.pressKey("v"); });
    expect(await finalFrame(setup)).toContain("# 中文 source");
    expect(controller.notice).toBe("copied");
  } finally { copy.mockRestore(); await act(async () => { await setup.dispose(); }); }
});
