import { expect, test, spyOn } from "bun:test";
import { createTestRenderer } from "@opentui/core/testing";
import { RGBA, TextRenderable, type Renderable } from "@opentui/core";
import { configureThemes, defaultThemeConfig } from "../src/theme/palette";
import { HistoryQueue } from "../src/scrollback/history";
import { SessionController } from "../src/session/controller";

test("REPL dispatch history waits for every declared member and commits once", async () => {
  const setup = await createTestRenderer({ width: 80, height: 24, footerHeight: 8,
    screenMode: "split-footer", externalOutputMode: "capture-stdout" });
  const controller = new SessionController(), queue = new HistoryQueue(setup.renderer, controller);
  const envelope = { protocol_version: 2, task_id: "batch" } as const;
  const dispatch_batch = { id: "wave", call_ids: ["first", "second"] as [string, string] };
  try {
    for (const call_id of dispatch_batch.call_ids) controller.receive({ ...envelope, type: "tool_call", call_id,
      dispatch_batch, tool: "agent_run", arguments: JSON.stringify({ description: call_id, agent_type: "researcher" }) });
    controller.receive({ ...envelope, type: "tool_result", call_id: "second", result: "hidden model envelope",
      structured_content: { ok: true, status: "completed", summary: "Second business summary" } });
    await queue.drain(); expect(setup.externalOutput.takeText()).toBe("");
    controller.receive({ ...envelope, type: "tool_result", call_id: "first", result: "hidden model envelope",
      structured_content: { ok: true, status: "failed", summary: "First partial summary", parent_evidence_ids: ["e100"] } });
    await queue.drain();
    const output = setup.externalOutput.takeText();
    expect(output.split("Dispatch subagents")).toHaveLength(2);
    expect(output).toContain("Second business summary"); expect(output).toContain("First partial summary");
    expect(output).toContain("e100"); expect(output).not.toContain("hidden model envelope");
    await queue.drain(); expect(setup.externalOutput.takeText()).toBe("");
    expect(queue.committed.size).toBe(1);
  } finally { queue.dispose(); await setup.dispose(); }
});

test("REPL skill history contains its status and never its reference body", async () => {
  const setup = await createTestRenderer({ width: 80, height: 24, footerHeight: 8,
    screenMode: "split-footer", externalOutputMode: "capture-stdout" });
  const controller = new SessionController(), queue = new HistoryQueue(setup.renderer, controller);
  const envelope = { protocol_version: 2, task_id: "skill", call_id: "skill" } as const;
  try {
    controller.receive({ ...envelope, type: "tool_call", tool: "load_skill_reference", arguments: '{"reference_name":"web.md"}' });
    controller.receive({ ...envelope, type: "tool_execution", operation_id: "skill", kind: "tool", status: "completed" });
    controller.receive({ ...envelope, type: "tool_result", result: "SECRET REFERENCE BODY", structured_content: { text: "SECRET REFERENCE BODY" } });
    await queue.drain();
    const output = setup.externalOutput.takeText();
    expect(output).toContain("Load skill reference"); expect(output).toContain("Loaded successfully");
    expect(output).not.toContain("SECRET REFERENCE BODY");
  } finally { queue.dispose(); await setup.dispose(); }
});

test("execution history waits for the result and commits the grouped record once", async () => {
  const setup = await createTestRenderer({ width: 60, height: 24, footerHeight: 8,
    screenMode: "split-footer", externalOutputMode: "capture-stdout" });
  const controller = new SessionController(), queue = new HistoryQueue(setup.renderer, controller);
  const envelope = { protocol_version: 2, task_id: "t", call_id: "call" } as const;
  try {
    controller.receive({ ...envelope, type: "tool_call", tool: "shell_command", arguments: "{}" });
    controller.receive({ ...envelope, type: "tool_execution", operation_id: "op", kind: "command",
      status: "completed", command: "printf grouped", stdout: "grouped output", exit_code: 0 });
    await queue.drain(); expect(setup.externalOutput.takeText()).toBe("");
    controller.receive({ ...envelope, type: "tool_result", result: "Command: printf grouped" });
    await queue.drain();
    const output = setup.externalOutput.takeText();
    expect(output.split("printf grouped")).toHaveLength(2);
    expect(output.split("grouped output")).toHaveLength(2);
    await queue.drain(); expect(setup.externalOutput.takeText()).toBe("");
  } finally { queue.dispose(); await setup.dispose(); }
});

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
    controller.receive({ protocol_version: 2, type: "tool_call", task_id: "t", call_id: "history-tool", tool: "probe", arguments: "{}" });
    controller.receive({ protocol_version: 2, type: "tool_result", task_id: "t", call_id: "history-tool", result: "second result" });
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

test("new plain REPL history adopts the saved text color", async () => {
  configureThemes(defaultThemeConfig);
  const setup = await createTestRenderer({ width: 60, height: 24, footerHeight: 8,
    screenMode: "split-footer", externalOutputMode: "capture-stdout" });
  const controller = new SessionController();
  controller.applyConfigDocument({ config: { terminal: { theme: "blue", themes: {
    ...defaultThemeConfig.themes, blue: { ...defaultThemeConfig.themes.blue, text: "#123456" },
  } } } });
  const colors: RGBA[] = [];
  const original = setup.renderer.createScrollbackSurface.bind(setup.renderer);
  const inspect = spyOn(setup.renderer, "createScrollbackSurface").mockImplementation(options => {
    const surface = original(options);
    const settle = surface.settle.bind(surface);
    surface.settle = async timeout => {
      await settle(timeout);
      function visit(node: Renderable) {
        if (node instanceof TextRenderable && node.plainText.includes("historyvalue")) colors.push(node.fg);
        for (const child of node.getChildren()) visit(child);
      }
      visit(surface.root);
    };
    return surface;
  });
  const queue = new HistoryQueue(setup.renderer, controller);
  try {
    const event = { protocol_version: 2, type: "message", message_id: "history", source: "main",
      role: "assistant", format: "text", task_id: "fixture" } as const;
    controller.receive({ ...event, operation: "append", text: "historyvalue" });
    controller.receive({ ...event, operation: "end", status: "completed" });
    await queue.drain(); await setup.renderOnce();
    expect(setup.externalOutput.takeText()).toContain("historyvalue");
    expect(colors).toEqual([RGBA.fromHex("#123456")]);
  } finally { inspect.mockRestore(); queue.dispose(); await setup.dispose(); configureThemes(defaultThemeConfig); }
});
