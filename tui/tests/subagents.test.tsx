import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import type { ScrollBoxRenderable, TextRenderable } from "@opentui/core";
import { testRender } from "@opentui/react/test-utils";
import { setLanguage } from "../src/i18n";
import { SessionController } from "../src/session/controller";
import { TuiShell } from "../src/shells/TuiShell";
import type { SubagentEvent } from "../src/transport/protocol.generated";
import { activeAgentRows, subagentRows } from "../src/workbench/agents";
import { WorkbenchState } from "../src/workbench/layout";
import { finalFrame } from "./render-helpers";

afterEach(() => { setLanguage("en"); });

const agent = (id: string, status: SubagentEvent["status"], parent = "main", group = ""): SubagentEvent => ({
  protocol_version: 2, type: "subagent", task_id: "task", agent_id: id, name: id,
  parent_id: parent, group_id: group, agent_type: id === "leader" ? "group-leader" : "general", status,
});

async function fixture(agents: SubagentEvent[], width = 130) {
  setLanguage("en");
  const controller = new SessionController(); controller.connected = true; controller.notice = "";
  for (const entry of agents) {
    controller.receive(entry);
    const message = { protocol_version: 2, type: "message", task_id: "task", message_id: `${entry.agent_id}-message`,
      source: entry.agent_id, role: "assistant", format: "text" } as const;
    controller.receive({ ...message, operation: "append", text: `Retained output from ${entry.agent_id}.` });
    controller.receive({ ...message, operation: "end", status: "completed" });
  }
  const workbench = new WorkbenchState();
  const setup = await testRender(<TuiShell controller={controller} workbench={workbench} />,
    { width, height: 40, kittyKeyboard: true });
  const node = (id: string) => setup.renderer.root.findDescendantById(id)!;
  await finalFrame(setup);
  async function click(id: string) {
    const scroll = node("subagents-scroll") as ScrollBoxRenderable;
    await act(async () => { scroll.scrollChildIntoView(id); });
    await finalFrame(setup);
    const target = node(id);
    await act(async () => { await setup.mockMouse.click(target.x, target.y); });
    return finalFrame(setup);
  }
  return { controller, workbench, setup, node, click };
}

test("active and ended trees retain groups and nearest visible ancestors without duplicating agents", () => {
  const agents = new Map([
    agent("ended-child", "completed", "runner", "group"),
    agent("queued", "pending", "runner", "group"),
    agent("leader", "completed", "main", "group"),
    agent("runner", "running", "leader", "group"),
    agent("failed", "failed", "missing"),
    agent("killed", "killed"),
  ].map((entry) => [entry.agent_id, entry]));
  const active = activeAgentRows(agents);
  expect(active.filter((row) => row.kind === "agent").map((row) => row.id)).toEqual(["main", "runner", "queued"]);
  const folded = subagentRows(agents, false);
  expect(folded.slice(0, -1)).toEqual(active);
  expect(folded.at(-1)).toMatchObject({ kind: "directory", count: 4 });
  const expanded = subagentRows(agents, true);
  const directory = expanded.findIndex((row) => row.kind === "directory");
  const ended = expanded.slice(directory + 1);
  expect(ended.filter((row) => row.kind === "agent").map((row) => row.id)).toEqual(["leader", "ended-child", "failed", "killed"]);
  expect(ended.find((row) => row.id === "ended-child")?.text).toContain("└─ ended-child");
  expect(new Set(expanded.filter((row) => row.kind === "agent").map((row) => row.id)).size).toBe(agents.size + 1);
});

test.each(["completed", "failed", "killed"] as const)("%s agents move into the folded directory while their open output survives", async (status) => {
  const { controller, workbench, setup, node, click } = await fixture([agent("child", "running")]);
  try {
    await act(async () => { controller.inputFocused = false; workbench.layout.focus = "subagents"; controller.changed(); });
    await act(async () => { setup.mockInput.pressArrow("down"); }); await finalFrame(setup);
    await act(async () => { setup.mockInput.pressEnter(); }); await finalFrame(setup);
    expect(controller.source).toBe("child");
    await act(async () => { controller.receive(agent("child", status)); });
    let frame = await finalFrame(setup);
    expect(controller.source).toBe("child");
    expect(frame).toContain(`child (${status})`);
    expect(frame).toContain("Retained output from child.");
    expect(node("agent-row-child")).toBeUndefined();
    expect((node("ended-agents-directory") as TextRenderable).plainText).toBe("〉Ended subagents (1)");
    await click("ended-agents-directory");
    expect(node("agent-row-child")).toBeDefined();
    await click("agent-row-main"); expect(controller.source).toBe("main");
    frame = await click("agent-row-child");
    expect(controller.source).toBe("child"); expect(frame).toContain("Retained output from child.");
    expect(controller.agents.size).toBe(1);
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("keyboard navigation skips group labels and folding returns the cursor to the directory without changing the source", async () => {
  const { controller, workbench, setup, node, click } = await fixture([
    agent("leader", "completed", "main", "group"), agent("child", "running", "leader", "group"),
    agent("failed", "failed", "main", "group"), agent("killed", "killed", "main", "group"),
  ]);
  async function key(name: "down" | "up" | "return") {
    await act(async () => { if (name === "return") setup.mockInput.pressEnter(); else setup.mockInput.pressArrow(name); });
    return finalFrame(setup);
  }
  try {
    await act(async () => { controller.inputFocused = false; workbench.layout.focus = "subagents"; controller.changed(); });
    await key("down"); await key("return"); expect(controller.source).toBe("child");
    await key("down"); await key("return");
    expect(controller.source).toBe("child");
    expect((node("ended-agents-directory") as TextRenderable).plainText).toBe("﹀Ended subagents (3)");
    await key("down"); await key("return"); expect(controller.source).toBe("leader");
    await click("ended-agents-directory");
    expect(controller.source).toBe("leader"); expect(node("agent-row-leader")).toBeUndefined();
    await key("up"); await key("return"); expect(controller.source).toBe("child");
    await key("down"); await key("return");
    const frame = await click("agent-row-failed");
    expect(controller.source).toBe("failed"); expect(frame).toContain("Retained output from failed.");
  } finally { await act(async () => { await setup.dispose(); }); }
});

test.each([120, 64])("the directory title starts at the list edge and uses the existing pane at %i columns", async (width) => {
  const { workbench, setup, node, click } = await fixture([], width);
  try {
    const columns = structuredClone([workbench.layout.primary, workbench.layout.secondary]);
    const paneHeight = node("subagents-pane").height;
    const directory = node("ended-agents-directory") as TextRenderable;
    expect(directory.x).toBe(node("agent-row-main").x);
    expect(directory.plainText).toBe("〉Ended subagents (0)");
    await click("ended-agents-directory");
    expect((node("ended-agents-directory") as TextRenderable).plainText).toBe("﹀Ended subagents (0)");
    expect((node("ended-agents-empty") as TextRenderable).plainText).toBe("  No ended subagents");
    expect(node("subagents-pane").height).toBe(paneHeight);
    expect([workbench.layout.primary, workbench.layout.secondary]).toEqual(columns);
  } finally { await act(async () => { await setup.dispose(); }); }
});

test.each(["task", "session"] as const)("a new %s clears the ended directory and returns reading to main", async (kind) => {
  const { controller, setup, node, click } = await fixture([agent("finished", "completed")]);
  try {
    await click("ended-agents-directory"); await click("agent-row-finished");
    expect(controller.source).toBe("finished");
    await act(async () => {
      if (kind === "task") controller.receive({ protocol_version: 2, type: "task_started", task_id: "next", task: { command: "run", target: "example.test" },
        state: { target: "example.test", phase: "idle", task_constraints: {}, task: { active: true, task_id: "next" },
          last_run: null, findings: [], evidence: [], constraint_violations: [] } });
      else controller.receive({ protocol_version: 2, type: "control_result", operation: "session.new", result: {} });
    });
    await finalFrame(setup);
    expect(controller.source).toBe("main"); expect(controller.agents.size).toBe(0);
    expect(node("agent-row-finished")).toBeUndefined();
    expect((node("ended-agents-directory") as TextRenderable).plainText).toBe("﹀Ended subagents (0)");
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("language changes refresh directory labels and terminated-agent statuses", async () => {
  const { setup, node, click } = await fixture([agent("killed", "killed")]);
  try {
    await click("ended-agents-directory");
    await act(async () => { setLanguage("zh"); }); await finalFrame(setup);
    expect((node("ended-agents-directory") as TextRenderable).plainText).toBe("﹀已结束子代理（1）");
    expect((node("agent-row-killed") as TextRenderable).plainText).toContain("已终止");
    await act(async () => { setLanguage("en"); }); await finalFrame(setup);
    expect((node("ended-agents-directory") as TextRenderable).plainText).toBe("﹀Ended subagents (1)");
  } finally { await act(async () => { await setup.dispose(); }); }
});
