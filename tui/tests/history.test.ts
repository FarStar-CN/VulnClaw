import { expect, test, spyOn } from "bun:test";
import { createTestRenderer } from "@opentui/core/testing";
import { HistoryQueue } from "../src/scrollback/history";
import { SessionController } from "../src/session/controller";

test("MD-06/07/10: completed Markdown, tool and later body commit in event order once", async () => {
  const setup = await createTestRenderer({ width: 60, height: 24, footerHeight: 8,
    screenMode: "split-footer", externalOutputMode: "capture-stdout" });
  const controller = new SessionController();
  const queue = new HistoryQueue(setup.renderer, controller);
  const event = { protocol_version: 2, type: "message", message_id: "one", source: "main",
    role: "assistant", format: "markdown", task_id: "t" } as const;
  try {
    controller.receive({ ...event, operation: "append", text: "[first][ref]" });
    await queue.drain();
    expect(setup.externalOutput.take()).toHaveLength(0);
    controller.receive({ ...event, operation: "append", text: "\n\n[ref]: https://example.test" });
    controller.receive({ ...event, operation: "end", status: "completed" });
    controller.receive({ protocol_version: 2, type: "tool_call", task_id: "t", tool: "probe", arguments: "{}" });
    controller.receive({ protocol_version: 2, type: "tool_result", task_id: "t", result: "second result" });
    controller.receive({ ...event, message_id: "two", operation: "append", text: "third body" });
    controller.receive({ ...event, message_id: "two", operation: "end", status: "cancelled" });
    await queue.drain(); await setup.renderOnce();
    const text = setup.externalOutput.takeText();
    expect(text).toContain("first");
    expect(text.indexOf("first")).toBeLessThan(text.indexOf("probe"));
    expect(text.indexOf("second result")).toBeLessThan(text.indexOf("third body"));
    expect(text).toContain("cancelled");
    await queue.drain(); await setup.renderOnce();
    expect(setup.externalOutput.take()).toHaveLength(0);
  } finally { queue.dispose(); await setup.dispose(); }
});

test("MD-10/15: long output resumes unsubmitted batches after an admission failure", async () => {
  const setup = await createTestRenderer({ width: 40, height: 24, screenMode: "split-footer",
    footerHeight: 8, externalOutputMode: "capture-stdout" });
  const controller = new SessionController();
  const event = { protocol_version: 2, type: "message", task_id: "t", message_id: "long", source: "main", role: "assistant", format: "text" } as const;
  const text = Array.from({ length: 300 }, (_, index) => `line-${String(index).padStart(3, "0")}`).join("\n");
  controller.receive({ ...event, operation: "append", text });
  controller.receive({ ...event, operation: "end", status: "completed" });
  const original = setup.renderer.createScrollbackSurface.bind(setup.renderer);
  const spy = spyOn(setup.renderer, "createScrollbackSurface").mockImplementation((options) => {
    const surface = original(options); const commit = surface.commitRows.bind(surface); let calls = 0;
    surface.commitRows = (start, end, options) => { if (++calls === 2) throw new Error("injected output admission failure"); commit(start, end, options); };
    return surface;
  });
  const queue = new HistoryQueue(setup.renderer, controller);
  try {
    await queue.drain();
    expect(queue.error).toContain("admission failure");
    expect(queue.committed.has("long")).toBe(false);
    expect(controller.messages.get("long")?.text).toBe(text);
    controller.receive({ protocol_version: 2, type: "control_result", request_id: "new", operation: "session.new", result: {} });
    controller.receive({ ...event, message_id: "next", operation: "append", text: "new-session-body" });
    controller.receive({ ...event, message_id: "next", operation: "end", status: "completed" });
    await queue.retry(); await setup.renderOnce();
    expect(queue.committed.has("long")).toBe(true);
    const output = setup.externalOutput.takeText();
    expect(output.split("new-session-body")).toHaveLength(2);
    for (let index = 0; index < 300; index++) expect(output.split(`line-${String(index).padStart(3, "0")}`)).toHaveLength(2);
  } finally { spy.mockRestore(); queue.dispose(); await setup.dispose(); }
});
