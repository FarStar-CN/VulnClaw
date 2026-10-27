import { expect, test } from "bun:test";
import { act } from "react";
import { testRender } from "@opentui/react/test-utils";
import { MessageCard, ToolCard } from "../src/components/MessageCard";
import { finalFrame } from "./render-helpers";

for (const kind of ["log", "status"] as const) test(`${kind}: only the chevron and label toggle details`, async () => {
  const setup = await testRender(<ToolCard theme="blue" width={80} card={{ id: "fixture", source: "main", kind,
    title: "Log", text: "first preview\nDetailed receipt body", format: "text" }} />, { width: 80, height: 10 });
  const detailsVisible = async () => (await finalFrame(setup)).split("\n").some((line) => line.trim() === "│ Detailed receipt body");
  try {
    expect(await detailsVisible()).toBe(false);
    const entry = setup.renderer.root.findDescendantById("entry-fixture")!;
    expect((await finalFrame(setup)).trimStart()).toStartWith("〉Log");
    expect((await finalFrame(setup)).split("first preview").length - 1).toBe(1);
    const previewX = entry.getChildren()[0].getChildren()[1].x;
    for (const x of [35, 75]) {
      await act(async () => { await setup.mockMouse.click(entry.x + x, entry.y); });
      expect(await detailsVisible()).toBe(false);
    }
    await act(async () => { await setup.mockMouse.click(entry.x + 1, entry.y); });
    expect(await detailsVisible()).toBe(true);
    expect((await finalFrame(setup)).trimStart()).toStartWith("﹀Log");
    expect((await finalFrame(setup)).split("first preview").length - 1).toBe(1);
    expect(entry.getChildren()[0].getChildren()[1].x).toBe(previewX);
    await act(async () => { await setup.mockMouse.click(entry.x + 30, entry.y + 2); });
    expect(await detailsVisible()).toBe(true);
    await act(async () => { await setup.mockMouse.click(entry.x + 3, entry.y); });
    expect(await detailsVisible()).toBe(false);
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("thinking toggles through its label and chevron while its line count stays inert", async () => {
  const setup = await testRender(<MessageCard id="thinking" role="reasoning" text="Detailed thought body"
    format="text" status="completed" theme="blue" width={80} showRole={false} />, { width: 80, height: 10 });
  try {
    expect(await finalFrame(setup)).toContain("〉Thinking · 1 lines");
    const entry = setup.renderer.root.findDescendantById("entry-thinking")!;
    const countX = entry.getChildren()[0].getChildren()[1].x;
    for (const x of [14, 45]) {
      await act(async () => { await setup.mockMouse.click(entry.x + x, entry.y); });
      expect(await finalFrame(setup)).not.toContain("Detailed thought body");
    }
    await act(async () => { await setup.mockMouse.click(entry.x + 4, entry.y); });
    const expanded = await finalFrame(setup);
    expect(expanded).toContain("│ Detailed thought body"); expect(expanded).toContain("﹀Thinking · 1 lines");
    expect(entry.getChildren()[0].getChildren()[1].x).toBe(countX);
    await act(async () => { await setup.mockMouse.click(entry.x + 1, entry.y); });
    expect(await finalFrame(setup)).not.toContain("Detailed thought body");
  } finally { await act(async () => { await setup.dispose(); }); }
});
