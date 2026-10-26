import { expect, test } from "bun:test";
import { act } from "react";
import { testRender } from "@opentui/react/test-utils";
import { SessionController } from "../src/session/controller";
import { executionComplete, executionPresentation } from "../src/session/executions";
import { currentLanguage, setLanguage } from "../src/i18n";
import { Transcript } from "../src/components/Transcript";
import { TuiShell } from "../src/shells/TuiShell";
import { WorkbenchState } from "../src/workbench/layout";
import { finalFrame } from "./render-helpers";

const envelope = { protocol_version: 2, task_id: "presentation" } as const;
function announce(controller: SessionController, id: string, tool: string, args: object = {}, source = "main") {
  controller.receive({ ...envelope, type: "tool_call", call_id: id, tool, arguments: JSON.stringify(args), agent_id: source });
}
function complete(controller: SessionController, id: string, structured_content: Record<string, unknown>, result = "model-only prefix", source = "main") {
  controller.receive({ ...envelope, type: "tool_result", call_id: id, structured_content, result, agent_id: source });
}
const last = (controller: SessionController) => [...controller.cards.values()].at(-1)!.execution!;

test("confirmed Chinese actions cover coordination and analysis tools", () => {
  const language = currentLanguage(); setLanguage("zh");
  const names: Record<string, string> = {
    submit_finding: "提交候选发现", agent_run: "派发子代理", evidence_list: "查看证据列表",
    evidence_view: "查看证据详情", evidence_search: "检索证据", load_skill_reference: "加载技能参考",
    memory_search: "检索历史对话", vault_archive: "归档历史对话", vault_restore: "恢复历史归档",
    vault_search: "检索归档摘要", vault_status: "查看归档状态", source_extract: "整理源码证据",
    crypto_decode: "编解码处理", traffic_list: "查看流量列表", traffic_view: "查看流量详情",
    traffic_sitemap: "汇总站点地图", topology_build: "生成网络拓扑", compliance_map: "映射合规控制",
    findings_report: "汇总发现风险", findings_diff: "对比评估结果", remediation_advice: "生成修复建议", attack_map: "映射攻击技术",
  };
  try {
    for (const [tool, name] of Object.entries(names)) {
      const controller = new SessionController(); announce(controller, tool, tool);
      expect(executionPresentation(last(controller)).title.split(" · ")[0]).toBe(name);
    }
    for (const [action, name] of Object.entries({ list: "查看子代理任务", collect: "收集子代理结果", message: "发送子代理消息", cancel: "取消子代理任务" })) {
      const controller = new SessionController(); announce(controller, action, "agent_job", { action });
      expect(executionPresentation(last(controller)).title).toBe(name);
    }
  } finally { setLanguage(language); }
});

for (const [action, label] of Object.entries({ created: "Candidate recorded", updated: "Evidence added", duplicate: "Already recorded" })) {
  test(`finding ${action} retains business data and excludes storage receipts`, () => {
    const controller = new SessionController();
    announce(controller, "finding", "submit_finding", { title: "SQL injection", severity: "High", method: "POST",
      endpoint: "/login", description: "Candidate description", evidence_ids: ["e017", "e021"] });
    complete(controller, "finding", { action, finding_id: "hidden-finding-id", verification_status: "pending", path: "/hidden/evidence.json" });
    const view = executionPresentation(last(controller));
    const details = view.sections.map((section) => `${section.label}: ${section.text}`).join("\n");
    expect(view.receipt).toContain(label);
    expect(view.receipt).toContain("Pending verification");
    expect(details).toContain("POST /login"); expect(details).toContain("Candidate description");
    expect(details).toContain("e017, e021");
    expect(details).not.toContain("hidden-finding-id"); expect(details).not.toContain("/hidden/");
    expect(details).not.toContain("model-only prefix");
  });
}

test("background launch acceptance differs from foreground failure and job snapshots", () => {
  const controller = new SessionController();
  announce(controller, "background", "agent_run", { description: "Coordinate", agent_type: "group-leader", background: true });
  complete(controller, "background", { ok: true, background: true, status: "running", task_id: "job-1" });
  expect(executionPresentation(last(controller)).receipt).toBe("Dispatched");
  expect(executionComplete(last(controller))).toBe(true);
  announce(controller, "foreground", "agent_run", { description: "Verify", agent_type: "verifier" });
  complete(controller, "foreground", { ok: true, status: "failed", summary: "Verification timed out" });
  expect(executionPresentation(last(controller)).status).toBe("failed");
  announce(controller, "collect", "agent_job", { action: "collect", job_id: "job-1" });
  complete(controller, "collect", { status: "running", name: "Coordinate" });
  expect(executionPresentation(last(controller)).receipt).toContain("running");
  expect(executionPresentation(last(controller)).status).toBe("returned");
  expect(executionComplete(last(controller))).toBe(true);
});

test("dispatch membership isolates sources and tasks and survives out-of-order cancellation", () => {
  const controller = new SessionController();
  const dispatch_batch = { id: "same-batch", call_ids: ["a", "b"] as [string, string] };
  for (const source of ["main", "child"]) for (const call_id of dispatch_batch.call_ids) {
    controller.receive({ ...envelope, type: "tool_call", call_id, agent_id: source, dispatch_batch,
      tool: "agent_run", arguments: JSON.stringify({ description: `${source}-${call_id}`, agent_type: "researcher" }) });
  }
  expect(controller.cards.size).toBe(2);
  complete(controller, "b", { ok: true, status: "completed", summary: "second completed", parent_evidence_ids: ["e022"] });
  const group = [...controller.cards.values()][0]!.execution!;
  expect(executionComplete(group)).toBe(false);
  expect(group.members!.get("b")!.structuredContent!.summary).toBe("second completed");
  expect([...controller.cards.values()][1]!.execution!.members!.get("b")!.received).toBe(false);
  controller.receive({ ...envelope, task_id: "other-task", type: "tool_call", call_id: "a", agent_id: "main", dispatch_batch,
    tool: "agent_run", arguments: '{"description":"other"}' });
  expect(controller.cards.size).toBe(3);
  controller.receive({ ...envelope, type: "turn_cancelled", request_id: "request", state: {
    target: "", phase: "idle", task_constraints: {}, task: { active: false, task_id: null },
    last_run: null, findings: [], evidence: [], constraint_violations: [],
  } });
  expect(executionComplete(group)).toBe(true);
  expect(executionPresentation(group).status).toBe("partial");
  const details = executionPresentation(group).sections.map((section) => section.text).join("\n");
  expect(details).toContain("second completed"); expect(details).toContain("e022");
});

test("skill references never expose bodies or enter keyboard expansion navigation", async () => {
  const controller = new SessionController(); controller.inputFocused = false;
  announce(controller, "skill", "load_skill_reference", { skill_name: "web", reference_name: "injection.md" });
  controller.receive({ ...envelope, type: "tool_execution", call_id: "skill", operation_id: "skill", kind: "tool", status: "completed" });
  complete(controller, "skill", { text: "SECRET REFERENCE BODY" }, "SECRET REFERENCE BODY");
  const skill = last(controller);
  announce(controller, "next", "browser_snapshot"); complete(controller, "next", {}, "Next visible result");
  const setup = await testRender(<Transcript controller={controller} width={70} positions={new Map()} focused />,
    { width: 72, height: 22, kittyKeyboard: true });
  try {
    const frame = await finalFrame(setup);
    expect(frame).toContain("Loaded successfully"); expect(frame).not.toContain("SECRET REFERENCE BODY");
    expect(executionPresentation(skill).expandable).toBe(false);
    expect(executionPresentation(skill).sections).toEqual([]);
    await act(async () => { setup.mockInput.pressArrow("down", { meta: true }); setup.mockInput.pressEnter(); });
    expect(await finalFrame(setup)).toContain("Next visible result");
  } finally { await act(async () => { await setup.dispose(); }); }
});

for (const status of ["failed", "denied", "cancelled", "skipped"] as const) test(`skill ${status} stays non-expandable`, () => {
  const controller = new SessionController(); announce(controller, "skill", "load_skill_reference", { reference_name: "missing.md" });
  controller.receive({ ...envelope, type: "tool_execution", call_id: "skill", operation_id: "skill", kind: "tool", status });
  complete(controller, "skill", {}, "must not appear");
  const view = executionPresentation(last(controller));
  expect(view.status).toBe(status); expect(view.expandable).toBe(false); expect(view.sections).toEqual([]);
});

test("business details have one expansion and preserve that expansion across source switches", async () => {
  const controller = new SessionController(); controller.inputFocused = false;
  announce(controller, "decode", "crypto_decode", { operation: "base64_decode" });
  complete(controller, "decode", { text: "Decoded business result" });
  const card = [...controller.cards.values()][0]!;
  const setup = await testRender(<TuiShell controller={controller} workbench={new WorkbenchState()} />,
    { width: 140, height: 38, kittyKeyboard: true });
  try {
    await finalFrame(setup);
    const header = setup.renderer.root.findDescendantById(`execution-${card.id}`)!;
    await act(async () => { await setup.mockMouse.click(header.x + 3, header.y); });
    expect(await finalFrame(setup)).toContain("Decoded business result");
    expect(setup.renderer.root.findDescendantById(`execution-${card.id}-business-0`)).toBeUndefined();
    await act(async () => { controller.source = "child"; controller.changed(); });
    expect(await finalFrame(setup)).not.toContain("Decoded business result");
    await act(async () => { controller.source = "main"; controller.changed(); });
    expect(await finalFrame(setup)).toContain("Decoded business result");
  } finally { await act(async () => { await setup.dispose(); }); }
});
