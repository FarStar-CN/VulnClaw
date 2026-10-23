import { expect, test } from "bun:test";
import { act } from "react";
import { TextBufferRenderable } from "@opentui/core";
import { MouseButtons } from "@opentui/core/testing";
import { testRender } from "@opentui/react/test-utils";
import { SessionController } from "../src/session/controller";
import { executionHistory, fitExecution } from "../src/session/executions";
import { Transcript } from "../src/components/Transcript";
import { finalFrame } from "./render-helpers";

const envelope = { protocol_version: 2, task_id: "t" } as const;
function announce(controller: SessionController, call_id: string, source = "main") {
  controller.receive({ ...envelope, type: "tool_call", call_id, agent_id: source,
    tool: "shell_command", arguments: '{"command":"printf fixture"}' });
}
function operation(controller: SessionController, call_id: string, source = "main") {
  controller.receive({ ...envelope, type: "tool_execution", agent_id: source, call_id,
    operation_id: `${source}-${call_id}`, kind: "command", status: "completed",
    command: "printf '中文 😀'", argv: ["/bin/sh", "-c", "printf '中文 😀'"], stdout: "中文 😀", exit_code: 0 });
}

test("same tool and call IDs associate by task and source despite result order", () => {
  const controller = new SessionController();
  announce(controller, "a"); announce(controller, "b"); announce(controller, "a", "child");
  for (const [id, source] of [["b", "main"], ["a", "child"], ["a", "main"]]) {
    operation(controller, id!, source!);
    controller.receive({ ...envelope, type: "tool_result", agent_id: source, call_id: id, result: `result-${source}-${id}` });
  }
  expect(controller.entries).toHaveLength(3);
  const cards = [...controller.cards.values()];
  expect(cards[0]!.execution!.result).toBe("result-main-a");
  expect(cards[1]!.execution!.result).toBe("result-main-b");
  expect(cards[2]!.execution!.result).toBe("result-child-a");
  expect(controller.visibleEntries("child")).toHaveLength(1);
});

test("unknown results are returned, cancellation preserves partial output", () => {
  const controller = new SessionController(); announce(controller, "unknown");
  controller.receive({ ...envelope, type: "tool_result", call_id: "unknown", result: "arbitrary" });
  expect([...controller.cards.values()][0]!.execution!.status).toBe("returned");
  announce(controller, "running");
  controller.receive({ ...envelope, type: "tool_execution", call_id: "running", operation_id: "op",
    kind: "command", status: "running", stdout: "partial output" });
  controller.disconnect(new Error("gone"));
  const card = [...controller.cards.values()][1]!;
  expect(card.execution!.status).toBe("failed");
  expect(card.execution!.operations.get("op")!.stdout).toBe("partial output");
  expect(card.execution!.received).toBe(true);
});

test("expanded operation shows command once, details are separate and keyboard accessible", async () => {
  const controller = new SessionController(); controller.inputFocused = false;
  announce(controller, "a"); operation(controller, "a");
  controller.receive({ ...envelope, type: "tool_result", call_id: "a", result: "Command: printf '中文 😀'" });
  const setup = await testRender(<Transcript controller={controller} width={70} positions={new Map()} focused />,
    { width: 72, height: 22, kittyKeyboard: true });
  try {
    expect(await finalFrame(setup)).toContain("printf '中文 😀'");
    await act(async () => {
      setup.mockInput.pressArrow("down", { meta: true });
      setup.mockInput.pressEnter();
    });
    const expanded = await finalFrame(setup);
    expect(expanded.split("printf '中文 😀'")).toHaveLength(2);
    expect(expanded).not.toContain("Copy original");
    expect(expanded).not.toContain("Raw tool arguments");
    expect(expanded).not.toContain('"command":');
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("execution records retain evidence and semantic scan results", () => {
  const controller = new SessionController();
  controller.receive({ ...envelope, type: "tool_call", call_id: "scan", tool: "nmap_scan", arguments: "{}" });
  controller.receive({ ...envelope, type: "tool_execution", call_id: "scan", operation_id: "op",
    kind: "command", status: "completed", command: "nmap target", stdout: "<nmaprun/>" });
  controller.receive({ ...envelope, type: "tool_result", call_id: "scan", evidence_id: "e101", result: "Open ports: 80, 443" });
  const card = [...controller.cards.values()][0]!;
  expect(card.execution!.evidenceId).toBe("e101");
  expect(executionHistory(card.execution!)).toContain("Open ports: 80, 443");
});

test("history retains exact originals, and summaries fit wide Unicode characters", () => {
  const controller = new SessionController(); announce(controller, "a"); operation(controller, "a");
  const card = [...controller.cards.values()][0]!;
  card.execution!.result = "Command: duplicated";
  const text = executionHistory(card.execution!);
  expect(text.split("printf '中文 😀'")).toHaveLength(2);
  expect(text).not.toContain("duplicated");
  expect(fitExecution("中文 😀 long request", 8)).toEndWith("…");
});

for (const width of [36, 72]) test(`long command retains its original without copy actions at ${width} columns`, async () => {
  const controller = new SessionController(); controller.inputFocused = false;
  announce(controller, "long");
  const original = "printf '%s' '中文 😀 http://127.0.0.1:39136/display-test?q=%27&include=metadata,evidence,request_summary'";
  controller.receive({ ...envelope, type: "tool_execution", call_id: "long", operation_id: "op",
    kind: "command", status: "completed", command: original, stdout: "", exit_code: 0 });
  controller.receive({ ...envelope, type: "tool_result", call_id: "long", result: "" });
  const setup = await testRender(<Transcript controller={controller} width={width - 2} positions={new Map()} focused />,
    { width, height: 30, kittyKeyboard: true });
  try {
    expect(await finalFrame(setup)).toContain("…");
    await act(async () => { setup.mockInput.pressArrow("down", { meta: true }); });
    await finalFrame(setup);
    await act(async () => { setup.mockInput.pressEnter(); });
    const expanded = await finalFrame(setup);
    const card = [...controller.cards.values()][0]!;
    const command = setup.renderer.root.findDescendantById(`execution-${card.id}-op-command`) as TextBufferRenderable;
    expect(command.plainText).toBe(original);
    expect(command.height).toBeGreaterThan(1);
    expect(expanded).not.toContain("Copy original");
    expect(setup.renderer.root.findDescendantById(`execution-${card.id}-op-copy`)).toBeUndefined();
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("a sole external tool result is visible after opening the main record", async () => {
  const controller = new SessionController(); controller.inputFocused = false;
  controller.receive({ ...envelope, type: "tool_call", call_id: "mcp", tool: "browser_snapshot", arguments: "{}" });
  controller.receive({ ...envelope, type: "tool_result", call_id: "mcp", result: "Snapshot first line\nSnapshot second line" });
  const setup = await testRender(<Transcript controller={controller} width={70} positions={new Map()} focused />,
    { width: 72, height: 20, kittyKeyboard: true });
  try {
    await finalFrame(setup);
    await act(async () => { setup.mockInput.pressArrow("down", { meta: true }); setup.mockInput.pressEnter(); });
    const frame = await finalFrame(setup);
    expect(frame).toContain("Snapshot first line");
    expect(frame).toContain("Snapshot second line");
    const card = [...controller.cards.values()][0]!;
    expect(setup.renderer.root.findDescendantById(`execution-${card.id}-result`)).toBeUndefined();
  } finally { await act(async () => { await setup.dispose(); }); }
});

for (const multiple of [false, true]) test(`${multiple ? "multiple outputs stay folded" : "a sole output is shown directly"}`, async () => {
  const controller = new SessionController(); controller.inputFocused = false;
  announce(controller, "outputs");
  controller.receive({ ...envelope, type: "tool_execution", call_id: "outputs", operation_id: "op",
    kind: "command", status: "completed", command: "printf fixture", stdout: "fixture stdout",
    ...(multiple ? { stderr: "fixture stderr" } : {}) });
  controller.receive({ ...envelope, type: "tool_result", call_id: "outputs", result: "" });
  const setup = await testRender(<Transcript controller={controller} width={70} positions={new Map()} focused />,
    { width: 72, height: 22, kittyKeyboard: true });
  try {
    await finalFrame(setup);
    await act(async () => { setup.mockInput.pressArrow("down", { meta: true }); setup.mockInput.pressEnter(); });
    const frame = await finalFrame(setup);
    const card = [...controller.cards.values()][0]!;
    const stdoutId = `execution-${card.id}-op-stdout`;
    if (multiple) {
      expect(setup.renderer.root.findDescendantById(stdoutId)).toBeDefined();
      expect(frame).not.toContain("fixture stdout");
      expect(frame).not.toContain("fixture stderr");
      const stderr = setup.renderer.root.findDescendantById(`execution-${card.id}-op-stderr`)!;
      await act(async () => { await setup.mockMouse.click(stderr.x + 3, stderr.y); });
      const output = await finalFrame(setup);
      expect(output).toContain("fixture stderr");
      expect(output).not.toContain("fixture stdout");
      expect(output).not.toContain("Copy original");
    } else {
      expect(setup.renderer.root.findDescendantById(stdoutId)).toBeUndefined();
      expect(frame).toContain("fixture stdout");
    }
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("execution labels ignore trailing whitespace and non-left mouse buttons", async () => {
  const controller = new SessionController();
  announce(controller, "mouse"); operation(controller, "mouse");
  const setup = await testRender(<Transcript controller={controller} width={70} positions={new Map()} focused />,
    { width: 72, height: 22 });
  try {
    await finalFrame(setup);
    const card = [...controller.cards.values()][0]!;
    const id = `execution-${card.id}`;
    const label = setup.renderer.root.findDescendantById(id)!;
    expect(label.width).toBeLessThan(60);
    await act(async () => { await setup.mockMouse.click(label.x + label.width + 5, label.y); });
    await finalFrame(setup);
    expect(controller.inputFocused).toBe(false);
    expect(setup.renderer.root.findDescendantById(`${id}-op-command`)).toBeUndefined();
    for (const button of [MouseButtons.RIGHT, MouseButtons.MIDDLE]) {
      await act(async () => { await setup.mockMouse.click(label.x + 3, label.y, button); });
      await finalFrame(setup);
      expect(setup.renderer.root.findDescendantById(`${id}-op-command`)).toBeUndefined();
    }
    await act(async () => { await setup.mockMouse.click(label.x + 3, label.y); });
    await finalFrame(setup);
    expect(setup.renderer.root.findDescendantById(`${id}-op-command`)).toBeDefined();
  } finally { await act(async () => { await setup.dispose(); }); }
});
