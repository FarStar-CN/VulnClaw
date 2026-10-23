import { expect, test } from "bun:test";
import { act } from "react";
import { testRender } from "@opentui/react/test-utils";
import { MessageCard, ToolCard } from "../src/components/MessageCard";
import { finalFrame } from "./render-helpers";

for (const kind of ["log", "status", "tool_call", "tool_result"] as const) test(`${kind}: only the triangle and label toggle details`, async () => {
  const setup = await testRender(<ToolCard theme="blue" width={80} card={{ id: "fixture", source: "main", kind,
    title: kind === "tool_call" ? "evidence_view" : "Log", text: "first preview\nDetailed receipt body", format: "text" }} />, { width: 80, height: 10 });
  const detailsVisible = async () => (await finalFrame(setup)).split("\n").some((line) => line.trim() === "Detailed receipt body");
  try {
    expect(await detailsVisible()).toBe(false);
    const entry = setup.renderer.root.findDescendantById("entry-fixture")!;
    for (const x of [35, 75]) {
      await act(async () => { await setup.mockMouse.click(entry.x + x, entry.y); });
      expect(await detailsVisible()).toBe(false);
    }
    await act(async () => { await setup.mockMouse.click(entry.x, entry.y); });
    expect(await detailsVisible()).toBe(true);
    await act(async () => { await setup.mockMouse.click(entry.x + 30, entry.y + 2); });
    expect(await detailsVisible()).toBe(true);
    await act(async () => { await setup.mockMouse.click(entry.x + 3, entry.y); });
    expect(await detailsVisible()).toBe(false);
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("thinking toggles through its label and triangle while its line count stays inert", async () => {
  const setup = await testRender(<MessageCard id="thinking" role="reasoning" source="main" text="Detailed thought body"
    format="text" status="completed" theme="blue" width={80} showRole={false} />, { width: 80, height: 10 });
  try {
    expect(await finalFrame(setup)).toContain("Thinking · 1 lines");
    const entry = setup.renderer.root.findDescendantById("entry-thinking")!;
    for (const x of [14, 45]) {
      await act(async () => { await setup.mockMouse.click(entry.x + x, entry.y); });
      expect(await finalFrame(setup)).not.toContain("Detailed thought body");
    }
    await act(async () => { await setup.mockMouse.click(entry.x + 4, entry.y); });
    expect(await finalFrame(setup)).toContain("Detailed thought body");
    await act(async () => { await setup.mockMouse.click(entry.x, entry.y); });
    expect(await finalFrame(setup)).not.toContain("Detailed thought body");
  } finally { await act(async () => { await setup.dispose(); }); }
});
