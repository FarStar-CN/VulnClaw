import { expect, test } from "bun:test";
import { SessionController } from "../src/session/controller";
import type { ClientMessage, MessageEvent, StateSnapshot } from "../src/transport/protocol.generated";
import { codeBlocks } from "../src/components/code-blocks";

const state = (): StateSnapshot => ({ target: "example.test", phase: "idle", task_constraints: {},
  task: { active: false, task_id: null }, last_run: null, findings: [], evidence: [], constraint_violations: [] });
const message = (id: string, source: string, text: string): MessageEvent => ({
  protocol_version: 2, type: "message", task_id: "t", message_id: id, source,
  role: "assistant", format: "markdown", operation: "append", text,
});

test("backend readiness clears the startup notice and retains provider metadata", () => {
  const controller = new SessionController();
  controller.receive({ protocol_version: 2, type: "ready", backend: { pid: 12345, version: "fixture", protocol_version: 2 },
    capabilities: { commands: [], authoritative_state: true, cancellation: true },
    runtime: { config_ready: true, provider: "fixture", model: "fixture-model", mcp_started: 0, skills: [] }, state: state() });
  expect(controller.connected).toBe(true);
  expect(controller.notice).toBe("");
  expect(controller.runtime?.model).toBe("fixture-model");
});

test("saved settings synchronize the backend permission and reasoning display", () => {
  const controller = new SessionController();
  controller.receive({ protocol_version: 2, type: "control_result", request_id: "settings",
    operation: "config.update", result: { config: {
      safety: { permission_mode: "read_only" }, session: { show_thinking: false },
    } } });
  expect(controller.permission).toBe("read_only");
  expect(controller.showReasoning).toBe(false);
});

test("MD-05/07/08/09: lifecycle, source isolation, tool order and failed partial text", () => {
  const controller = new SessionController();
  controller.receive(message("one", "main", "```py\n"));
  controller.receive(message("child-a", "a", "**A"));
  controller.receive(message("child-b", "b", "B"));
  controller.receive(message("one", "main", "  print('中文')"));
  controller.receive({ ...message("one", "main", ""), operation: "end", status: "completed" });
  controller.receive({ protocol_version: 2, type: "tool_call", task_id: "t", tool: "http", arguments: "{}" });
  controller.receive({ protocol_version: 2, type: "tool_result", task_id: "t", result: "HTTP/1.1 200\n\nplain" });
  controller.receive(message("two", "main", "[late][ref]"));
  expect(controller.visibleEntries().map((entry) => entry.kind)).toEqual(["message", "card", "card", "message"]);
  expect(controller.messages.get("one")?.text).toBe("```py\n  print('中文')");
  expect(controller.visibleEntries("a").map((entry) => entry.id)).toEqual(["child-a"]);
  expect(controller.visibleEntries("b").map((entry) => entry.id)).toEqual(["child-b"]);
  controller.disconnect(new Error("backend exited"));
  expect(controller.messages.get("two")?.status).toBe("failed");
  expect(controller.messages.get("two")?.text).toBe("[late][ref]");
  expect(controller.messages.get("one")?.status).toBe("completed");
});

test("MD-16: a cumulative snapshot replaces text before the next delta", () => {
  const controller = new SessionController();
  controller.receive(message("one", "main", "first"));
  controller.receive({ protocol_version: 2, type: "state", state: { ...state(), messages: [{
    message_id: "one", source: "main", role: "assistant", format: "markdown", text: "first more", status: "streaming",
  }] } });
  controller.receive(message("one", "main", " last"));
  expect(controller.messages.get("one")?.text).toBe("first more last");
  expect(controller.visibleEntries()).toHaveLength(1);
});

test("local themes work while disconnected and busy input preserves the draft", async () => {
  const controller = new SessionController();
  await controller.submit("/theme orange");
  expect(controller.theme).toBe("orange");
  controller.connected = true;
  controller.state = { ...state(), task: { active: true, task_id: "busy" } };
  controller.draft = "keep this draft";
  await controller.submit(controller.draft);
  expect(controller.draft).toBe("keep this draft");
  expect(controller.notice).toContain("active");
});

test("task text is previewed by Python and approval decisions are explicit controls", async () => {
  const controller = new SessionController();
  const requests: ClientMessage[] = [];
  controller.attach({ send(request) { requests.push(request); }, async close() {} });
  controller.connected = true; controller.taskCommands = ["run"];
  const pending = controller.submit("/run example.test --only-port 443");
  const preview = requests.at(-1)!;
  expect(preview.type).toBe("control");
  controller.receive({ protocol_version: 2, type: "control_result", request_id: preview.request_id,
    operation: "task.preview", result: { task: { command: "run", target: "example.test" }, errors: {} } });
  await pending;
  expect(controller.overlay?.kind).toBe("task");
  controller.receive({ protocol_version: 2, type: "approval_required", task_id: "t", request_hash: "a".repeat(64), question: "run command" });
  controller.resolveApproval(false);
  const approval = requests.at(-1)!;
  expect(approval.type === "control" && approval.payload?.arguments).toEqual({ request_hash: "a".repeat(64), decision: "deny" });
});

test("MD-12: code copying preserves spaces, tabs, empty lines and original Markdown", () => {
  const source = "# Raw\n\n```python\n\tprint('中文')\n\n    next()\n```\n";
  expect(codeBlocks(source)).toEqual(["\tprint('中文')\n\n    next()\n"]);
  expect(source).toStartWith("# Raw");
  expect(codeBlocks("~~~unknown\n  partial")).toEqual(["  partial"]);
});


test("busy tasks allow backend controls and answers, while rejecting another task", async () => {
  const controller = new SessionController();
  const requests: ClientMessage[] = [];
  controller.attach({ send(request) { requests.push(request); }, async close() {} });
  requests.length = 0;
  controller.connected = true; controller.taskCommands = ["run"];
  controller.state = { ...state(), task: { active: true, task_id: "busy" } };
  await controller.submit("/tools");
  expect(requests.at(-1)?.type).toBe("submit_input");
  controller.state.task.waiting_for_input = true;
  controller.draft = "/run another.test";
  await controller.submit(controller.draft);
  expect(requests).toHaveLength(1);
  expect(controller.draft).toBe("/run another.test");
  await controller.submit("requested answer");
  expect(requests).toHaveLength(2);
  expect(controller.draft).toBe("");
});

test("completion receipts preserve report paths and counts without repeating assistant output", () => {
  const controller = new SessionController();
  controller.receive(message("answer", "main", "# Unique assistant body"));
  controller.receive({ ...message("answer", "main", ""), operation: "end", status: "completed" });
  controller.receive({ protocol_version: 2, type: "turn_completed", request_id: "turn", task_id: "t", state: state(), result: {
    output: "# Unique assistant body", completed: true, reports: [{ path: "/reports/result.md", findings_json: "/reports/findings.json", findings_sarif: "/reports/findings.sarif" }], report_error: "PDF unavailable",
  } });
  const receipt = [...controller.cards.values()].at(-1)!;
  expect(receipt.text).not.toContain("Unique assistant body");
  for (const path of ["/reports/result.md", "/reports/findings.json", "/reports/findings.sarif", "PDF unavailable"]) expect(receipt.text).toContain(path);
  controller.receive({ protocol_version: 2, type: "task_completed", task_id: "task", request_id: "task", state: state(), findings: [], result: {
    command: "run", target: "example.test", status: "completed", exit_code: 0, summary: { findings: { verified: 1, candidates: 2 } }, run: { name: "saved-run", directory: "/runs/saved-run" }, findings: [], report: null, report_error: null,
  } });
  expect([...controller.cards.values()].at(-1)?.text).toContain("1 verified, 2 candidates");
  expect([...controller.cards.values()].at(-1)?.text).toContain("/runs/saved-run");
});
