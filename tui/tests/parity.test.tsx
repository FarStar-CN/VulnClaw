import { expect, spyOn, test } from "bun:test";
import { act } from "react";
import { RGBA, type ScrollBoxRenderable, type TextareaRenderable } from "@opentui/core";
import { testRender } from "@opentui/react/test-utils";
import { SessionController } from "../src/session/controller";
import type { ClientMessage, RuntimeInfo, StateSnapshot, SubagentEvent } from "../src/transport/protocol.generated";
import { TuiShell } from "../src/shells/TuiShell";
import { LlmSettings } from "../src/components/LlmSettings";
import { TaskForm } from "../src/components/TaskForm";
import { activeAgentRows } from "../src/workbench/agents";
import { WorkbenchState } from "../src/workbench/layout";
import { permissions } from "../src/theme/palette";
import { finalFrame } from "./render-helpers";

const state = (): StateSnapshot => ({ permission_mode: "ask", target: "example.test", phase: "idle", task_constraints: {},
  task: { active: false, task_id: null }, last_run: null, findings: [], evidence: [], constraint_violations: [] });
const runtime: RuntimeInfo = { config_ready: true, provider: "fixture", model: "fixture-model", mcp_started: 9,
  skills: ["hidden-skill-name"], mcp_ready: 2, skills_loaded: 3, mcp_servers: [
    { name: "burp", ready: false }, { name: "chrome-devtools", ready: false },
    { name: "fetch", ready: true }, { name: "memory", ready: true },
  ] };
function session() {
  const controller = new SessionController(); const requests: ClientMessage[] = [];
  controller.attach({ send(request) { requests.push(request); }, async close() {} });
  controller.connected = true; controller.state = state(); controller.notice = "";
  return { controller, requests };
}

test("permission cycling waits for Python and uses the same four text colors in both themes", async () => {
  const { controller, requests } = session(); controller.permission = "read_only";
  const setup = await testRender(<TuiShell controller={controller} workbench={new WorkbenchState()} />,
    { width: 120, height: 36, kittyKeyboard: true, exitOnCtrlC: false });
  try {
    for (const theme of ["blue", "orange"] as const) {
      await act(async () => { controller.setTheme(theme); });
      for (const [next, current] of [["ask", "read_only"], ["auto_review", "ask"], ["full_access", "auto_review"], ["read_only", "full_access"]] as const) {
        await finalFrame(setup);
        const label = setup.renderer.root.findDescendantById("permission-label")!;
        const span = setup.captureSpans().lines[label.y].spans.find((span) => span.text.includes(permissions[current].label));
        expect(span?.fg).toEqual(RGBA.fromHex(permissions[current].color));
        await act(async () => { setup.mockInput.pressTab({ shift: true }); });
        expect(controller.permission).toBe(current);
        expect(controller.overlay).toBeNull();
        const request = requests.at(-1)!;
        expect(request.type === "control" && request.payload?.arguments).toEqual({ mode: next });
        await act(async () => { controller.receive({ protocol_version: 2, type: "control_result", request_id: request.request_id,
          operation: "session.permission.set", result: { mode: next }, state: { ...state(), permission_mode: next } }); });
        expect(controller.permission).toBe(next);
      }
    }
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("Tab retains its original completion candidates and leaves the ordinary hint unchanged", async () => {
  const { controller, requests } = session(); controller.draft = "/";
  controller.commands = { run: "Run", recon: "Recon", report: "Report" };
  const setup = await testRender(<TuiShell controller={controller} workbench={new WorkbenchState()} />,
    { width: 120, height: 36, kittyKeyboard: true });
  try {
    await finalFrame(setup);
    await act(async () => { setup.mockInput.pressTab(); });
    let frame = await finalFrame(setup);
    expect(controller.draft).toBe("/run "); expect(frame).toContain("Tab complete");
    expect(frame).not.toContain("已补全"); expect(frame).not.toContain("Completed /");
    await act(async () => { setup.mockInput.pressTab(); }); await finalFrame(setup);
    expect(controller.draft).toBe("/recon ");
    await act(async () => { setup.mockInput.pressTab(); }); await finalFrame(setup);
    expect(controller.draft).toBe("/report ");
    expect(requests).toHaveLength(1);
    await act(async () => { controller.draft = "/theme "; controller.changed(); }); await finalFrame(setup);
    await act(async () => { setup.mockInput.pressArrow("down"); }); await finalFrame(setup);
    await act(async () => { setup.mockInput.pressTab(); }); await finalFrame(setup);
    expect(controller.draft).toBe("/theme orange ");
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("capabilities use backend counts, keep two rows, and preserve the chosen layout", async () => {
  const { controller } = session(); const workbench = new WorkbenchState();
  const setup = await testRender(<TuiShell controller={controller} workbench={workbench} />, { width: 120, height: 36 });
  try {
    await finalFrame(setup);
    const pane = setup.renderer.root.findDescendantById("capabilities-pane")!;
    expect(pane.height).toBe(4);
    const before = setup.captureCharFrame().split("\n").slice(pane.y, pane.y + pane.height).map((line) => line.slice(pane.x, pane.x + pane.width)).join("\n");
    expect(before).toMatch(/MCP\s+0\/0/); expect(before).toMatch(/Skills loaded\s+0/);
    const layout = JSON.stringify(workbench.layout);
    await act(async () => { controller.runtime = runtime; controller.changed(); });
    const frame = await finalFrame(setup);
    expect(frame).toMatch(/MCP\s+2\/4/); expect(frame).toMatch(/Skills loaded\s+3/);
    expect(frame).not.toContain("hidden-skill-name"); expect(JSON.stringify(workbench.layout)).toBe(layout);
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("small terminals block input and policy shortcuts, then restore the untouched draft", async () => {
  const { controller, requests } = session(); controller.draft = "retained draft";
  const workbench = new WorkbenchState(); const initial = JSON.stringify(workbench.layout);
  const setup = await testRender(<TuiShell controller={controller} workbench={workbench} />,
    { width: 120, height: 36, kittyKeyboard: true });
  try {
    await finalFrame(setup);
    await act(async () => { setup.resize(40, 12); });
    expect(await finalFrame(setup)).toContain("Terminal too small");
    await act(async () => { await setup.mockInput.typeText("discarded"); setup.mockInput.pressTab({ shift: true });
      await setup.mockInput.pasteBracketedText("discarded paste"); setup.mockInput.pressEnter(); });
    expect(controller.draft).toBe("retained draft"); expect(requests).toHaveLength(1);
    await act(async () => { setup.resize(120, 36); });
    expect(await finalFrame(setup)).toContain("retained draft"); expect(JSON.stringify(workbench.layout)).toBe(initial);
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("multiline editing stays in the input, while typing from a pane returns its first character", async () => {
  const { controller, requests } = session(); const workbench = new WorkbenchState();
  const setup = await testRender(<TuiShell controller={controller} workbench={workbench} />,
    { width: 120, height: 36, kittyKeyboard: true });
  try {
    await finalFrame(setup);
    await act(async () => { await setup.mockInput.typeText("one"); setup.mockInput.pressEnter({ shift: true }); await setup.mockInput.typeText("two"); });
    await finalFrame(setup); expect(controller.draft).toBe("one\ntwo"); expect(controller.composerRows).toBe(2);
    await act(async () => { setup.mockInput.pressArrow("up"); }); await finalFrame(setup);
    expect(controller.inputFocused).toBe(true); expect(requests).toHaveLength(1);
    await act(async () => { setup.mockInput.pressArrow("right", { ctrl: true }); }); await finalFrame(setup);
    expect(controller.inputFocused).toBe(false);
    await act(async () => { await setup.mockInput.typeText("Z"); }); await finalFrame(setup);
    expect(controller.inputFocused).toBe(true); expect(controller.draft).toContain("Z");
    await act(async () => { controller.inputFocused = false; controller.changed(); }); await finalFrame(setup);
    await act(async () => { setup.mockInput.pressEnter(); });
    expect(requests.at(-1)?.type).toBe("submit_input");
  } finally { await act(async () => { await setup.dispose(); }); }
});

const agent = (id: string, status: SubagentEvent["status"], parent = "main", type = "general"): SubagentEvent => ({
  protocol_version: 2, type: "subagent", task_id: "task", agent_id: id, name: id, parent_id: parent,
  group_id: "group", agent_type: type, status,
});
test("agent groups keep active descendants and ended sources remain readable", async () => {
  const { controller } = session();
  for (const entry of [agent("leader", "completed", "main", "group-leader"), agent("child", "running", "leader"),
    agent("queued", "pending", "child"), agent("failed", "failed"), agent("killed", "killed")]) controller.receive(entry);
  const rows = activeAgentRows(controller.agents);
  expect(rows.filter((row) => row.id).map((row) => row.id)).toEqual(["main", "child", "queued"]);
  expect(rows.find((row) => row.text.includes("leader"))?.id).toBeNull();
  controller.source = "child";
  controller.receive({ protocol_version: 2, type: "message", task_id: "task", message_id: "child-message", source: "child",
    role: "assistant", format: "text", operation: "append", text: "The child result survives completion." });
  const setup = await testRender(<TuiShell controller={controller} workbench={new WorkbenchState()} />, { width: 130, height: 38 });
  try {
    await finalFrame(setup);
    await act(async () => { controller.receive(agent("child", "completed", "leader")); });
    const frame = await finalFrame(setup);
    expect(controller.source).toBe("child"); expect(frame).toContain("child (completed)"); expect(frame).toContain("survives completion");
    expect(setup.renderer.root.findDescendantById("agent-row-child")).toBeUndefined();
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("thinking and tool results start folded and the full original remains in the reader", async () => {
  const { controller } = session();
  const event = { protocol_version: 2, type: "message", task_id: "task", source: "main", format: "text", operation: "append" } as const;
  controller.receive({ ...event, message_id: "question", role: "user", text: "Read these observations." });
  controller.receive({ ...event, message_id: "thinking", role: "reasoning", text: "private reasoning fixture\nsecond line" });
  controller.receive({ protocol_version: 2, type: "tool_result", task_id: "task", result: "Complete unabridged tool receipt.", format: "text" });
  controller.receive({ ...event, message_id: "answer", role: "assistant", text: "The answer is preserved." });
  const tool = [...controller.cards.values()][0];
  const setup = await testRender(<TuiShell controller={controller} workbench={new WorkbenchState()} />, { width: 140, height: 38 });
  try {
    let frame = await finalFrame(setup);
    expect(frame).toContain("Thinking · 2 lines"); expect(frame).toContain("Result · 1 lines");
    expect(frame).not.toContain("private reasoning fixture"); expect(frame).not.toContain("unabridged tool receipt");
    expect(frame).not.toContain("main · assistant");
    await act(async () => { controller.readMessage(tool.id); });
    frame = await finalFrame(setup); expect(frame).toContain("Complete unabridged tool receipt.");
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("LLM settings mask typed keys, revert edits, fetch all models and save the selected model", async () => {
  const { controller } = session(); const calls: { operation: string; args: unknown }[] = [];
  const control = spyOn(controller, "control").mockImplementation(async (operation, args) => {
    calls.push({ operation, args });
    return operation === "config.models" ? { models: Array.from({ length: 12 }, (_, i) => `fixture-${i}`) } : { message: "Saved fixture." };
  });
  const setup = await testRender(<LlmSettings controller={controller} active document={{ provider: "fixture", base_url: "https://fixture.test",
    model: "fixture", api_key_set: true, providers: [{ id: "fixture", label: "Fixture" }] }} />, { width: 92, height: 22, kittyKeyboard: true });
  try {
    expect(await finalFrame(setup)).toContain("(saved)");
    await act(async () => { for (let i = 0; i < 3; i++) setup.mockInput.pressTab(); setup.mockInput.pressEnter(); }); await finalFrame(setup);
    await act(async () => { await setup.mockInput.typeText("typed-secret-key"); });
    let frame = await finalFrame(setup); expect(frame).not.toContain("typed-secret-key"); expect(frame).toContain("••••");
    await act(async () => { setup.mockInput.pressEscape(); }); await finalFrame(setup);
    await act(async () => { setup.mockInput.pressTab(); setup.mockInput.pressEnter(); }); await finalFrame(setup);
    expect(calls.at(-1)?.operation).toBe("config.models");
    await act(async () => { for (let i = 0; i < 8; i++) setup.mockInput.pressArrow("down"); });
    frame = await finalFrame(setup); expect(frame).toContain("fixture-7"); expect(frame).not.toContain("fixture-0");
    await act(async () => { setup.mockInput.pressEnter(); }); await finalFrame(setup);
    await act(async () => { setup.mockInput.pressKey("s", { ctrl: true }); }); await finalFrame(setup);
    expect(calls.at(-1)).toEqual({ operation: "config.write", args: { provider: "fixture", website_url: "", base_url: "https://fixture.test", api_key: "", model: "fixture-7" } });
  } finally { control.mockRestore(); await act(async () => { await setup.dispose(); }); }
});

test("task overview remains centered and Read-Only refuses Y while keeping the draft", async () => {
  const { controller, requests } = session(); controller.permission = "read_only"; controller.draft = "composer draft";
  controller.overlay = { kind: "task", original: "/run fixture.test", preview: { task: { command: "run", target: "fixture.test", options: {} }, errors: {} } };
  const setup = await testRender(<TuiShell controller={controller} workbench={new WorkbenchState()} />, { width: 120, height: 40 });
  try {
    let frame = await finalFrame(setup);
    for (const label of ["Access rules", "Allow actions", "Run settings", "Options", "Cancel (N)", "Read-Only"]) expect(frame).toContain(label);
    const panel = setup.renderer.root.findDescendantById("form-pane")!;
    expect(panel.width).toBe(88); expect(panel.x).toBe(16);
    await act(async () => { setup.mockInput.pressKey("y"); });
    frame = await finalFrame(setup);
    expect(frame).toContain("change permission"); expect(requests).toHaveLength(1); expect(controller.draft).toBe("composer draft");
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("provider presets discard old models, and a model lookup failure preserves manual entry", async () => {
  const { controller } = session();
  let failLookup = false;
  const control = spyOn(controller, "control").mockImplementation(async (operation) => {
    if (operation === "config.preset") return { provider: "new", website_url: "https://new.test", base_url: "https://api.new.test", model: "new-default" };
    if (operation === "config.models" && failLookup) throw new Error("Fixture lookup unavailable");
    return { models: ["other-fixture", "fixture-prefix", "fixture-second"] };
  });
  const setup = await testRender(<LlmSettings controller={controller} active document={{ provider: "old", base_url: "https://old.test", model: "fixture",
    providers: [{ id: "old", label: "Old provider" }, { id: "new", label: "New provider" }] }} />, { width: 92, height: 22, kittyKeyboard: true });
  try {
    await finalFrame(setup);
    await act(async () => { setup.mockInput.pressTab({ shift: true }); setup.mockInput.pressEnter(); }); await finalFrame(setup);
    await act(async () => { setup.mockInput.pressArrow("down"); setup.mockInput.pressEnter(); });
    expect(await finalFrame(setup)).toContain("fixture-prefix"); // Prefix matches precede a containing match.
    await act(async () => { setup.mockInput.pressTab(); setup.mockInput.pressEnter(); }); await finalFrame(setup);
    await act(async () => { setup.mockInput.pressTab(); setup.mockInput.pressEnter(); });
    let frame = await finalFrame(setup); expect(frame).toContain("new-default"); expect(frame).not.toContain("fixture-prefix");
    failLookup = true;
    await act(async () => { setup.mockInput.pressTab({ shift: true }); setup.mockInput.pressEnter(); });
    frame = await finalFrame(setup); expect(frame).toContain("Fixture lookup unavailable"); expect(frame).toContain("new-default");
    await act(async () => {
      (setup.renderer.root.findDescendantById("llm-editor") as TextareaRenderable).setText("manual-model");
      setup.mockInput.pressEnter();
    });
    expect(await finalFrame(setup)).toContain("manual-model");
  } finally { control.mockRestore(); await act(async () => { await setup.dispose(); }); }
});

test("cancelling a task preview prevents its late validation reply from launching a task", async () => {
  const { controller } = session();
  const preview = { task: { command: "run", target: "fixture.test", options: {} }, errors: {} };
  let reply!: (value: Record<string, unknown>) => void;
  const control = spyOn(controller, "control").mockImplementation(() => new Promise((resolve) => { reply = resolve; }));
  const start = spyOn(controller, "startTask").mockImplementation(() => {});
  const setup = await testRender(<TaskForm controller={controller} initial={preview} original="/run fixture.test" active />, { width: 88, height: 32, kittyKeyboard: true });
  try {
    await finalFrame(setup);
    await act(async () => { setup.mockInput.pressKey("y"); }); await finalFrame(setup);
    expect(control).toHaveBeenCalledTimes(1);
    await act(async () => { setup.mockInput.pressEscape(); }); await finalFrame(setup);
    await act(async () => { reply(preview); }); await finalFrame(setup);
    expect(start).not.toHaveBeenCalled();
  } finally { control.mockRestore(); start.mockRestore(); await act(async () => { await setup.dispose(); }); }
});

test("long approval details scroll inside the centered modal without hiding the countdown or actions", async () => {
  const { controller } = session();
  controller.receive({ protocol_version: 2, type: "approval_required", task_id: "t", request_hash: "d".repeat(64),
    question: "fixture command", cwd: "/fixture", detail: "Long fixture reason\n".repeat(60), expires_in_seconds: 300, risk: "Fixture risk" });
  const setup = await testRender(<TuiShell controller={controller} workbench={new WorkbenchState()} />,
    { width: 120, height: 40, kittyKeyboard: true });
  try {
    await finalFrame(setup);
    const modal = setup.renderer.root.findDescendantById("approval-pane")!;
    expect([modal.width, modal.height, modal.x, modal.y]).toEqual([78, 18, 21, 11]);
    await act(async () => { setup.mockInput.pressKey("\x1b[6~"); });
    const frame = await finalFrame(setup);
    expect((setup.renderer.root.findDescendantById("approval-scroll") as ScrollBoxRenderable).scrollTop).toBeGreaterThan(0);
    for (const text of ["Fixture risk", "remaining", "[Y] Approve", "[N/Esc] Deny"]) expect(frame).toContain(text);
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("the composer wraps with native cell widths and stops growing after five visual lines", async () => {
  const { controller } = session();
  const setup = await testRender(<TuiShell controller={controller} workbench={new WorkbenchState()} />,
    { width: 100, height: 32, kittyKeyboard: true });
  try {
    await finalFrame(setup);
    await act(async () => { controller.draft = "wrapped words ".repeat(60); controller.changed(); }); await finalFrame(setup);
    expect(controller.composerRows).toBe(5);
    const frame = await finalFrame(setup);
    expect(frame).toContain("Shift+Tab permission"); expect(frame).toContain("model: not configured");
    const editor = setup.renderer.root.findDescendantById("composer-editor") as TextareaRenderable;
    expect(editor.editorView.getTotalVirtualLineCount()).toBeGreaterThan(5);
  } finally { await act(async () => { await setup.dispose(); }); }
});
