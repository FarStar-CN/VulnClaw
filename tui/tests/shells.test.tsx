import { expect, test } from "bun:test";
import { act } from "react";
import { testRender } from "@opentui/react/test-utils";
import { type ScrollBoxRenderable } from "@opentui/core";
import { SessionController, record } from "../src/session/controller";
import type { ClientMessage, StateSnapshot } from "../src/transport/protocol.generated";
import { WorkbenchState } from "../src/workbench/layout";
import { TuiShell } from "../src/shells/TuiShell";
import { ReplShell } from "../src/shells/ReplShell";
import { finalFrame } from "./render-helpers";

const emptyState = (): StateSnapshot => ({ permission_mode: "ask", target: "example.test", phase: "idle", task_constraints: {},
  task: { active: false, task_id: null }, last_run: null, findings: [], evidence: [], constraint_violations: [] });
function session() {
  const controller = new SessionController();
  const requests: ClientMessage[] = [];
  controller.attach({ send(request) { requests.push(request); }, async close() {} });
  controller.connected = true; controller.state = emptyState();
  controller.commands = { run: "Run a task", help: "Help", config: "Configure" };
  controller.taskCommands = ["run"]; controller.skills = ["web-pentest"];
  return { controller, requests };
}

for (const mode of ["tui", "repl"] as const) test(`${mode}: approval owns keys and paste while preserving the composer draft`, async () => {
  const { controller, requests } = session(); controller.draft = "draft 中文";
  const shell = mode === "tui" ? <TuiShell controller={controller} workbench={new WorkbenchState()} /> : <ReplShell controller={controller} />;
  const setup = await testRender(shell, { width: 100, height: 36, screenMode: "alternate-screen",
    externalOutputMode: "passthrough", kittyKeyboard: true, exitOnCtrlC: false });
  try {
    expect(await finalFrame(setup)).toContain("draft 中文");
    await act(async () => { controller.receive({ protocol_version: 2, type: "approval_required", task_id: "t",
      request_hash: "a".repeat(64), question: "echo approved", detail: "line\n".repeat(30) }); });
    expect(await finalFrame(setup)).toContain("Execution approval");
    await act(async () => { setup.mockInput.pressKey("\x1b[6~"); });
    await finalFrame(setup);
    expect((setup.renderer.root.findDescendantById("approval-scroll") as ScrollBoxRenderable).scrollTop).toBeGreaterThan(0);
    await act(async () => {
      await setup.mockInput.typeText("injected text");
      await setup.mockInput.pasteBracketedText("pasted secret");
      setup.mockInput.pressKey("v", { ctrl: true });
      setup.mockInput.pressKey("y");
    });
    expect(controller.draft).toBe("draft 中文");
    const decision = requests.filter((request) => request.type === "control").at(-1);
    expect(decision?.type === "control" && decision.payload?.arguments).toEqual({ request_hash: "a".repeat(64), decision: "approve" });
    await act(async () => { controller.receive({ protocol_version: 2, type: "approval_closed", task_id: "t", request_hash: "a".repeat(64), status: "approved" }); });
    expect(await finalFrame(setup)).toContain("draft 中文");
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("TUI: native mouse resize and Esc rollback retain the existing layout", async () => {
  const { controller } = session(); const workbench = new WorkbenchState();
  const setup = await testRender(<TuiShell controller={controller} workbench={workbench} />, { width: 120, height: 40, kittyKeyboard: true, exitOnCtrlC: false });
  try {
    const frame = await finalFrame(setup);
    for (const label of ["Status", "Capabilities", "Session transcript", "Findings inspector", "Subagents"]) expect(frame).toContain(label);
    const sash = setup.renderer.root.findDescendantById("sash-0")!;
    const original = workbench.layout.primaryWidth;
    await act(async () => {
      await setup.mockMouse.pressDown(sash.x, sash.y + 3);
      await setup.mockMouse.emitMouseEvent("drag", sash.x + 8, sash.y + 3);
    });
    await finalFrame(setup);
    expect(workbench.layout.primaryWidth).toBe(original + 8);
    await act(async () => { setup.mockInput.pressEscape(); });
    await finalFrame(setup);
    expect(workbench.layout.primaryWidth).toBe(original);
    await act(async () => { await setup.mockMouse.release(sash.x + 8, sash.y + 3); });
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("TUI: cancellation leaves input available and a following request goes to the same backend", async () => {
  const { controller, requests } = session();
  controller.state = { ...emptyState(), task: { active: true, task_id: "running" } };
  const setup = await testRender(<TuiShell controller={controller} workbench={new WorkbenchState()} />, { width: 100, height: 36, kittyKeyboard: true, exitOnCtrlC: false });
  try {
    await finalFrame(setup);
    await act(async () => { setup.mockInput.pressCtrlC(); });
    expect(requests.at(-1)?.type).toBe("cancel_task");
    await act(async () => { controller.receive({ protocol_version: 2, type: "task_cancelled", task_id: "running", state: emptyState() }); });
    await finalFrame(setup);
    await act(async () => { await setup.mockInput.typeText("hello 中文"); setup.mockInput.pressEnter(); });
    const request = requests.at(-1);
    expect(request?.type).toBe("submit_input");
    expect(request?.type === "submit_input" && request.payload?.text).toBe("hello 中文");
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("TUI: source changes keep independent Markdown and scroll state", async () => {
  const { controller } = session();
  const event = { protocol_version: 2, type: "message", task_id: "t", role: "assistant", format: "markdown", operation: "append" } as const;
  controller.receive({ ...event, message_id: "main", source: "main", text: Array.from({ length: 80 }, (_, i) => `main line ${i}\n`).join("\n") });
  controller.receive({ ...event, message_id: "child", source: "child", text: "# Child source\n\nindependent" });
  const setup = await testRender(<TuiShell controller={controller} workbench={new WorkbenchState()} />, { width: 110, height: 32 });
  try {
    await finalFrame(setup);
    const main = setup.renderer.root.findDescendantById("transcript-scroll") as ScrollBoxRenderable;
    main.scrollTo(10);
    await setup.renderOnce();
    const saved = main.scrollTop;
    await act(async () => { controller.source = "child"; controller.changed(); });
    expect(await finalFrame(setup)).toContain("Child source");
    await act(async () => { controller.source = "main"; controller.changed(); });
    await finalFrame(setup);
    const restored = setup.renderer.root.findDescendantById("transcript-scroll") as ScrollBoxRenderable;
    expect(restored.scrollTop).toBe(saved);
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("TUI: native pane drag commits across columns and collapse restores the pane", async () => {
  const { controller } = session(); let saves = 0;
  const workbench = new WorkbenchState(() => saves++);
  const setup = await testRender(<TuiShell controller={controller} workbench={workbench} />, { width: 120, height: 40, kittyKeyboard: true });
  try {
    await finalFrame(setup);
    const title = setup.renderer.root.findDescendantById("title-status")!;
    const destination = setup.renderer.root.findDescendantById("subagents-pane")!;
    await act(async () => { await setup.mockMouse.pressDown(title.x + 3, title.y); await setup.mockMouse.emitMouseEvent("drag", destination.x + 5, destination.y + destination.height - 2); });
    expect(await finalFrame(setup)).toContain("Status");
    expect(workbench.gesture?.kind).toBe("move"); expect(saves).toBe(0);
    await act(async () => { await setup.mockMouse.release(destination.x + 5, destination.y + destination.height - 2); });
    await finalFrame(setup);
    expect(workbench.layout.secondary.map((view) => view.id)).toContain("status"); expect(saves).toBe(1);
    const collapse = setup.renderer.root.findDescendantById("collapse-status")!;
    await act(async () => { await setup.mockMouse.pressDown(collapse.x, collapse.y); await setup.mockMouse.release(collapse.x, collapse.y); });
    await finalFrame(setup);
    expect(workbench.layout.secondary.find((view) => view.id === "status")?.collapsed).toBe(true);
    const restore = setup.renderer.root.findDescendantById("collapse-status")!;
    await act(async () => { await setup.mockMouse.pressDown(restore.x, restore.y); await setup.mockMouse.release(restore.x, restore.y); });
    await finalFrame(setup);
    expect(workbench.layout.secondary.find((view) => view.id === "status")?.collapsed).toBe(false);
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("TUI: finding selection resets only its detail scroll and focused subagents choose the reading source", async () => {
  const { controller, requests } = session(); const workbench = new WorkbenchState(); workbench.layout.focus = "findings"; controller.inputFocused = false;
  controller.findings = [{ id: "f1", target: "synthetic.test", title: "First finding", severity: "High", evidence_refs: [{ kind: "http", path: "evidence/first.json" }], description: "# First\n\n" + "long detail\n\n".repeat(70) },
    { id: "f2", target: "synthetic.test", title: "Second finding", severity: "Low", description: "# Second\n\nEvidence remains readable" }];
  controller.receive({ protocol_version: 2, type: "subagent", task_id: "t", agent_id: "child", parent_id: "main", group_id: "", name: "Child reader", agent_type: "general", status: "running" });
  const setup = await testRender(<TuiShell controller={controller} workbench={workbench} />, { width: 120, height: 44, kittyKeyboard: true });
  try {
    expect(await finalFrame(setup)).toContain("〉[HIGH] First finding");
    await act(async () => { setup.mockInput.pressEnter(); });
    const expanded = await finalFrame(setup);
    expect(expanded).toContain("﹀[HIGH] First finding"); expect(expanded).toContain("evidence/first.json");
    await act(async () => { setup.mockInput.pressArrow("down"); });
    expect(await finalFrame(setup)).toContain("Second finding");
    await act(async () => { setup.mockInput.pressArrow("right", { ctrl: true }); }); await finalFrame(setup);
    expect(String(workbench.layout.focus)).toBe("subagents");
    await act(async () => { setup.mockInput.pressArrow("down"); }); await finalFrame(setup);
    await act(async () => { setup.mockInput.pressEnter(); }); await finalFrame(setup);
    expect(controller.source).toBe("child");
    expect(requests.filter((request) => request.type === "submit_input")).toHaveLength(0);
  } finally { await act(async () => { await setup.dispose(); }); }
});

for (const mode of ["tui", "repl"] as const) test(`${mode}: permission cycling and multiline input preserve their contexts without history completion`, async () => {
  const { controller, requests } = session(); controller.history = ["first", "second"]; controller.draft = "retained draft";
  const shell = mode === "tui" ? <TuiShell controller={controller} workbench={new WorkbenchState()} /> : <ReplShell controller={controller} />;
  const setup = await testRender(shell, { width: 100, height: 36, screenMode: "alternate-screen", externalOutputMode: "passthrough", kittyKeyboard: true });
  try {
    await finalFrame(setup);
    await act(async () => { setup.mockInput.pressKey("p", { ctrl: true }); }); await finalFrame(setup);
    expect(controller.draft).toBe("retained draft");
    await act(async () => { setup.mockInput.pressKey("n", { ctrl: true }); }); await finalFrame(setup);
    expect(controller.draft).toBe("retained draft");
    const thinking = controller.showReasoning;
    await act(async () => { setup.mockInput.pressKey("t", { ctrl: true }); }); await finalFrame(setup);
    expect(controller.showReasoning).toBe(thinking); expect(controller.draft).toBe("retained draft");
    await act(async () => { setup.mockInput.pressTab(); setup.mockInput.pressTab({ shift: true }); });
    const controls = requests.filter((request) => request.type === "control");
    expect(controls.some((request) => request.payload?.operation === "session.permission.set" && record(request.payload.arguments).mode === "auto_review")).toBe(true);
    expect(controller.permission).toBe("ask");
    await act(async () => { setup.mockInput.pressEscape(); }); await finalFrame(setup);
    await act(async () => { await setup.mockInput.typeText("line one"); setup.mockInput.pressEnter({ shift: true }); await setup.mockInput.typeText("line two"); });
    await finalFrame(setup);
    expect(controller.draft).toBe("line one\nline two");
    await act(async () => { setup.mockInput.pressEnter(); });
    const request = requests.at(-1);
    expect(request?.type === "submit_input" && request.payload?.text).toBe("line one\nline two");
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("TUI: conversation and new-session status do not inherit the previous task receipt", async () => {
  const { controller } = session();
  const taskState = { ...emptyState(), phase: "reporting", last_run: { command: "run", target: "example.test", status: "completed", exit_code: 0, summary: {}, findings: [], run: { name: "previous-task" } } };
  controller.receive({ protocol_version: 2, type: "task_started", request_id: "start", task_id: "t", task: { command: "run", target: "example.test" }, state: taskState });
  const setup = await testRender(<TuiShell controller={controller} workbench={new WorkbenchState()} />, { width: 120, height: 40 });
  function status() {
    const pane = setup.renderer.root.findDescendantById("status-scroll") as ScrollBoxRenderable;
    const text = (node: import("@opentui/core").Renderable): string => "plainText" in node
      ? String(node.plainText) : node.getChildren().map(text).join(" ");
    return pane.getChildren().map(text).join("\n");
  }
  try {
    await finalFrame(setup);
    expect(status()).toContain("Task execution"); expect(status()).toContain("previous-task");
    await act(async () => { controller.startTask({ command: "run", target: "next.test" }); }); await finalFrame(setup);
    expect(status()).toContain("Submitting"); expect(status()).not.toContain("reporting");
    await act(async () => { await controller.submit("Explain the results"); }); await finalFrame(setup);
    expect(status()).not.toContain("Task execution");
    await act(async () => { controller.receive({ protocol_version: 2, type: "turn_started", request_id: "turn", task_id: "conversation", text: "Explain the results", state: taskState }); });
    await finalFrame(setup);
    expect(status()).toContain("Workspace"); expect(status()).not.toContain("Task execution"); expect(status()).not.toContain("reporting"); expect(status()).not.toContain("previous-task");
    await act(async () => { controller.receive({ protocol_version: 2, type: "control_result", request_id: "new", operation: "session.new", result: {}, state: emptyState() }); });
    await finalFrame(setup);
    expect(controller.activity).toBeNull(); expect(status()).not.toContain("Last command");
  } finally { await act(async () => { await setup.dispose(); }); }
});

for (const mode of ["tui", "repl"] as const) test(`${mode}: native command and skill completion produce backend-readable input`, async () => {
  const { controller, requests } = session(); controller.draft = "/conf";
  const shell = mode === "tui" ? <TuiShell controller={controller} workbench={new WorkbenchState()} /> : <ReplShell controller={controller} />;
  const setup = await testRender(shell, { width: 100, height: 36, screenMode: "alternate-screen", externalOutputMode: "passthrough" });
  try {
    await finalFrame(setup);
    await act(async () => { setup.mockInput.pressEnter(); }); await finalFrame(setup);
    expect(controller.draft).toBe("/config ");
    await act(async () => { controller.draft = "@web"; controller.changed(); });
    expect(await finalFrame(setup)).toContain("Use VulnClaw skill web-pentest.");
    await act(async () => { setup.mockInput.pressEnter(); }); await finalFrame(setup);
    expect(controller.draft).toBe("Use VulnClaw skill web-pentest. ");
    await act(async () => { setup.mockInput.pressEnter(); });
    const request = requests.at(-1);
    expect(request?.type === "submit_input" && request.payload?.text).toBe("Use VulnClaw skill web-pentest. ");
  } finally { await act(async () => { await setup.dispose(); }); }
});
