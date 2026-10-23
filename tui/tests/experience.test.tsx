import { expect, spyOn, test } from "bun:test";
import { act } from "react";
import type { ScrollBoxRenderable, TextareaRenderable } from "@opentui/core";
import { testRender } from "@opentui/react/test-utils";
import { experienceFilters, type Lesson } from "../src/session/experience";
import { WorkbenchState } from "../src/workbench/layout";
import { SessionController } from "../src/session/controller";
import type { ClientMessage } from "../src/transport/protocol.generated";
import { currentLanguage, setLanguage } from "../src/i18n";
import { TuiShell } from "../src/shells/TuiShell";
import { ReplShell } from "../src/shells/ReplShell";
import { configureThemes, defaultThemeConfig } from "../src/theme/palette";
import * as clipboard from "../src/platform/clipboard";
import { finalFrame } from "./render-helpers";


const experienceSamples: Lesson[] = [
  ["test-pending-1", "pending", "先核对响应证据，再选择测试动作"],
  ["test-pending-2", "pending", "保留失败路径，避免重复无效尝试"],
  ["test-approved", "approved", "使用真实请求记录关联发现与来源"],
  ["test-rejected", "rejected", "展示被拒绝的经验记录"],
].map(([id, status, context], index) => ({
  id: id!, status: status as Lesson["status"], scope: "technique", signal: index === 1 ? "deadend" : "success",
  context: context!, lesson: `## 经验样本 ${index + 1}\n\n${context}。\n\n` +
    "- 核对请求、响应和证据编号。\n- 只记录有依据的判断。\n\n```http\nGET /test HTTP/1.1\nHost: test.example.invalid\n```",
  confidence: .8, target_key: null, tags: { tech: ["http", "中文"], vuln_type: "evidence-review", waf: "", service: "test" },
  evidence_refs: { run_id: "test-run", finding_id: `f-demo-${index + 1}`, path: `test/evidence/e10${index + 1}.json` },
  source_runs: ["test-run"], created_at: "2026-10-20T00:00:00Z", reinforced_at: "2026-10-21T00:00:00Z",
}));

function experienceSession() {
  const controller = new SessionController(), workbench = new WorkbenchState();
  const lessons = structuredClone(experienceSamples);
  function result(operation: string, args: Record<string, unknown>) {
    if (operation === "experience.list") {
      const status = args.status ?? "pending";
      if (!experienceFilters.includes(status as typeof experienceFilters[number])) throw new Error("Unknown experience status");
      return { status, lessons: structuredClone(lessons.filter(lesson => status === "all" || lesson.status === status)) };
    }
    const lesson = lessons.find(item => item.id === args.lesson_id);
    if (!lesson) throw new Error("Lesson not found");
    if (operation === "experience.approve") lesson.status = "approved";
    else if (operation === "experience.reject") lesson.status = "rejected";
    else if (operation === "experience.edit") for (const field of ["context", "lesson"] as const) {
      if (typeof args[field] === "string") lesson[field] = args[field];
    }
    return { lesson: structuredClone(lesson) };
  }
  controller.attach({
    send(request) {
      if (request.type === "initialize") return;
      if (request.type !== "control" && request.type !== "submit_input") throw new Error("Unexpected request");
      const tokens = request.type === "submit_input" ? request.payload?.text?.trim().split(/\s+/) ?? [] : [];
      const operation = request.type === "control" ? request.payload.operation : `experience.${tokens[1] ?? "list"}`;
      const args = request.type === "control" ? request.payload.arguments ?? {} : {
        lesson_id: tokens[2], status: tokens.includes("--status") ? tokens[tokens.indexOf("--status") + 1] : "pending",
      };
      controller.receive({ protocol_version: 2, type: "control_result", request_id: request.request_id, operation, result: result(operation, args) });
    },
    async close() {},
  });
  controller.receive({ protocol_version: 2, type: "ready", backend: { pid: 0, version: "test", protocol_version: 2 },
    capabilities: { commands: [], input_commands: { experience: "Manage experience records" }, authoritative_state: true, cancellation: true },
    runtime: { config_ready: true, provider: "test", model: "test", skills: [], mcp_started: 0, mcp_ready: 0, skills_loaded: 0 },
    state: { target: "test.example.invalid", phase: "idle", task_constraints: {}, task: { active: false, task_id: null },
      last_run: null, findings: [], evidence: [], constraint_violations: [] } });
  return { controller, workbench };
}

async function panel(mode: "tui" | "repl" = "tui", width = 120, height = 40) {
  const language = currentLanguage();
  const { controller, workbench } = experienceSession();
  setLanguage("en");
  const setup = await testRender(mode === "tui" ? <TuiShell controller={controller} workbench={workbench} /> : <ReplShell controller={controller} />,
    { width, height, kittyKeyboard: true, screenMode: mode === "repl" ? "split-footer" : "alternate-screen", footerHeight: height - 2 });
  await act(async () => { await controller.submit("/experience"); }); await finalFrame(setup);
  async function click(id: string) {
    const item = setup.renderer.root.findDescendantById(id)!; expect(item).toBeDefined();
    await act(async () => { await setup.mockMouse.click(item.x + 1, item.y + (mode === "repl" ? setup.renderer.terminalHeight - setup.renderer.root.height : 0)); }); await finalFrame(setup);
  }
  async function read(id: string) {
    let result: Record<string, unknown> = {};
    await act(async () => { result = await controller.control("experience.show", { lesson_id: id }); });
    return result.lesson as typeof experienceSamples[number];
  }
  async function key(name: string, ctrl = false) { await act(async () => { setup.mockInput.pressKey(({ DOWN: "\x1b[B", PAGEDOWN: "\x1b[6~" } as Record<string, string>)[name] ?? name, { ctrl }); }); return finalFrame(setup); }
  async function field(text: string) {
    await key("RETURN");
    await act(async () => { (setup.renderer.root.findDescendantById("form-editor") as TextareaRenderable).setText(text); setup.mockInput.pressEnter(); });
    await finalFrame(setup);
  }
  async function dispose() { await act(async () => { await setup.dispose(); }); configureThemes(defaultThemeConfig); setLanguage(language); }
  return { controller, setup, click, read, key, field, dispose };
}

for (const mode of ["tui", "repl"] as const) test(`${mode}: experience filters and provenance use a dedicated readable panel`, async () => {
  const form = await panel(mode);
  try {
    const frame = await finalFrame(form.setup);
    expect(form.controller.overlay?.kind).toBe("experience");
    for (const text of ["Experience review", "Pending review", "test-pending-1", "test-run", "f-demo-1", "e101.json"]) expect(frame).toContain(text);
    expect(frame).not.toContain('"evidence_refs"'); expect(form.setup.renderer.root.findDescendantById("data-pane")).toBeUndefined();
    await form.click("experience-filter-all");
    for (const text of ["test-approved", "test-rejected", "4 lessons"]) expect(await finalFrame(form.setup)).toContain(text);
    await form.click("experience-row-test-approved"); expect(await finalFrame(form.setup)).toContain("Status: Approved");
    await form.key("TAB"); expect(await form.key("PAGEDOWN")).toContain("经验样本 3");
    form.controller.draft = "keep draft"; await form.key("ESCAPE");
    expect(form.controller.overlay).toBeNull(); expect(form.controller.draft).toBe("keep draft");
  } finally { await form.dispose(); }
});

test("review decisions advance selection and emit one receipt, even with repeated keys", async () => {
  const form = await panel(), controls = spyOn(form.controller, "control");
  try {
    await act(async () => { form.setup.mockInput.pressKey("a"); form.setup.mockInput.pressKey("a"); });
    const frame = await finalFrame(form.setup);
    expect(controls.mock.calls.filter(([op]) => op === "experience.approve")).toHaveLength(1);
    expect(frame).not.toContain("test-pending-1"); expect(frame).toContain("test-pending-2");
    expect((await form.read("test-pending-1")).status).toBe("approved");
    await form.click("experience-reject"); expect(await finalFrame(form.setup)).toContain("No lessons in this view.");
    expect((await form.read("test-pending-2")).status).toBe("rejected");
    expect([...form.controller.cards.values()].filter((card) => card.title === "Experience reviewed")).toHaveLength(2);
    expect(form.controller.overlay?.kind).toBe("experience");
  } finally { controls.mockRestore(); await form.dispose(); }
});

test("failed saves retain both multiline fields and retry preserves status and provenance", async () => {
  const form = await panel(), original = form.controller.control.bind(form.controller); let fail = true;
  const controls = spyOn(form.controller, "control").mockImplementation(async (operation, args) => {
    if (operation === "experience.edit" && fail) { fail = false; throw new Error("Save unavailable"); }
    return original(operation, args);
  });
  try {
    await form.click("experience-filter-approved"); await form.click("experience-edit");
    await form.field("中文 context\nsecond line"); await form.key("DOWN");
    const guidance = "## 新的经验\n\n\tkeep whitespace\n  next";
    await form.key("RETURN");
    await act(async () => { (form.setup.renderer.root.findDescendantById("form-editor") as TextareaRenderable).setText(guidance); form.setup.mockInput.pressKey("s", { ctrl: true }); });
    expect(controls.mock.calls.filter(([op]) => op === "experience.edit")).toHaveLength(0);
    expect(await form.key("s", true)).toContain("Save unavailable");
    expect(await finalFrame(form.setup)).toContain("中文 context");
    expect((await form.read("test-approved")).context).toBe(experienceSamples[2]!.context);
    await form.key("s", true);
    const saved = await form.read("test-approved");
    expect(saved.context).toBe("中文 context\nsecond line"); expect(saved.lesson).toBe(guidance);
    expect(saved.status).toBe("approved"); expect(saved.evidence_refs).toEqual(experienceSamples[2]!.evidence_refs);
    expect(form.setup.renderer.root.findDescendantById("form-pane")).toBeUndefined();
    expect([...form.controller.cards.values()].filter((card) => card.title === "Experience updated")).toHaveLength(1);
  } finally { controls.mockRestore(); await form.dispose(); }
});

test("cancelling discards edits, while copy keeps the saved context and Markdown", async () => {
  const form = await panel(), copy = spyOn(clipboard, "copyText").mockResolvedValue("copied");
  try {
    await form.click("experience-edit"); await form.field("discard this draft"); await form.key("ESCAPE");
    expect(await finalFrame(form.setup)).not.toContain("discard this draft");
    const saved = await form.read("test-pending-1"); expect(saved.context).toBe(experienceSamples[0]!.context);
    await form.key("m"); expect(copy.mock.calls[0]?.[1]).toBe(`${saved.context}\n\n${saved.lesson}`);
  } finally { copy.mockRestore(); await form.dispose(); }
});

for (const [mode, width] of [["tui", 80], ["repl", 44]] as const) test(`${mode}: narrow pages navigate, scroll and survive resizing`, async () => {
  const form = await panel(mode, width, 26);
  try {
    expect(form.setup.renderer.root.findDescendantById("experience-detail")).toBeUndefined();
    await form.key("DOWN");
    expect(await form.key("RETURN")).toContain("Back to list"); expect(form.setup.renderer.root.findDescendantById("experience-list")).toBeUndefined();
    await form.key("PAGEDOWN"); expect((form.setup.renderer.root.findDescendantById("experience-detail") as ScrollBoxRenderable).scrollTop).toBeGreaterThan(0);
    await act(async () => { form.setup.resize(120, 40); }); await finalFrame(form.setup);
    expect(form.setup.renderer.root.findDescendantById("experience-list")).toBeDefined();
    await act(async () => { form.setup.resize(width, 26); }); await finalFrame(form.setup);
    await form.key("ESCAPE"); expect(form.controller.overlay?.kind).toBe("experience");
    expect(form.setup.renderer.root.findDescendantById("experience-list")).toBeDefined();
    await form.key("ESCAPE"); expect(form.controller.overlay).toBeNull();
  } finally { await form.dispose(); }
});

test("refresh failures retain selection and late responses cannot reopen a closed panel", async () => {
  const form = await panel(), original = form.controller.control.bind(form.controller);
  const controls = spyOn(form.controller, "control").mockImplementationOnce(async () => { throw new Error("Store unavailable"); });
  try {
    expect(await form.key("F5")).toContain("Store unavailable"); expect(await finalFrame(form.setup)).toContain("test-pending-1");
    controls.mockImplementation(original); expect(await form.key("F5")).not.toContain("Store unavailable");
    let finish!: (value: Record<string, unknown>) => void;
    controls.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    await form.key("F5"); await form.key("ESCAPE");
    await act(async () => { finish({ status: "pending", lessons: experienceSamples }); }); expect(form.controller.overlay).toBeNull();
  } finally { controls.mockRestore(); await form.dispose(); }
});

test("active sessions disable review and editing", async () => {
  const form = await panel(), controls = spyOn(form.controller, "control");
  try {
    await act(async () => { form.controller.receive({ protocol_version: 2, type: "state", state: { ...form.controller.state!, task: { active: true, task_id: "busy" } } }); });
    expect(await finalFrame(form.setup)).toContain("requires an idle session");
    await form.key("a"); await form.key("e"); await form.key("r"); expect(controls.mock.calls).toHaveLength(0);
  } finally { controls.mockRestore(); await form.dispose(); }
});

test("cancelled initial requests cannot reopen the browser, including /exp", async () => {
  for (const text of ["/experience", "/exp show test-approved"]) {
    const controller = new SessionController(), requests: ClientMessage[] = [];
    controller.attach({ send(request) { requests.push(request); }, async close() {} }); controller.connected = true;
    const pending = controller.submit(text), request = requests.at(-1)!; expect(controller.overlay?.kind).toBe("experience");
    expect(request.type === "submit_input" && request.payload?.text).toBe(text); controller.dismiss();
    controller.receive({ protocol_version: 2, type: "control_result", request_id: request.request_id, operation: "experience.list", result: { status: "pending", lessons: experienceSamples } });
    await pending; expect(controller.overlay).toBeNull();
  }
});

test("/exp show opens the selected status, failed reviews retain the record, and mouse close returns to the session", async () => {
  const form = await panel();
  try {
    await act(async () => { form.controller.dismiss(); await form.controller.submit("/exp show test-approved"); });
    expect(await finalFrame(form.setup)).toContain("Status: Approved");
    expect(form.setup.renderer.root.findDescendantById("experience-row-test-pending-1")).toBeUndefined();
    const original = form.controller.control.bind(form.controller);
    const controls = spyOn(form.controller, "control").mockImplementationOnce(async () => { throw new Error("Write failed"); });
    try {
      await form.click("experience-reject");
      expect(await finalFrame(form.setup)).toContain("Write failed");
      expect(await finalFrame(form.setup)).toContain("test-approved");
      expect([...form.controller.cards.values()].filter((card) => card.title === "Experience reviewed")).toHaveLength(0);
      controls.mockImplementation(original);
      await form.click("experience-reject");
      expect((await form.read("test-approved")).status).toBe("rejected");
      expect([...form.controller.cards.values()].filter((card) => card.title === "Experience reviewed")).toHaveLength(1);
      await form.click("experience-close"); expect(form.controller.overlay).toBeNull();
    } finally { controls.mockRestore(); }
  } finally { await form.dispose(); }
});

test("changing language updates experience controls while leaving lesson text unchanged", async () => {
  const form = await panel();
  try {
    await act(async () => { setLanguage("zh"); });
    const frame = await finalFrame(form.setup);
    for (const text of ["经验审核", "待审核", "来源任务", "证据引用", "test-run", "e101.json"]) expect(frame).toContain(text);
    expect((await form.read("test-pending-1")).lesson).toBe(experienceSamples[0]!.lesson);
  } finally { await form.dispose(); }
});

for (const mode of ["tui", "repl"] as const) test(`${mode}: filters have initial focus and statuses cycle with the keyboard`, async () => {
  const form = await panel(mode), controls = spyOn(form.controller, "control");
  try {
    expect(await finalFrame(form.setup)).toContain("› Pending review");
    for (const status of ["approved", "rejected", "all"]) {
      await act(async () => { form.setup.mockInput.pressArrow("right"); }); await finalFrame(form.setup);
      expect(controls.mock.calls.at(-1)).toEqual(["experience.list", { status }]);
    }
  } finally { controls.mockRestore(); await form.dispose(); }
});

for (const [mode, width] of [["tui", 120], ["repl", 80]] as const) test(`${mode}: an empty filter retains keyboard focus for the next category`, async () => {
  const form = await panel(mode, width), original = form.controller.control.bind(form.controller);
  const controls = spyOn(form.controller, "control").mockImplementation(async (operation, args) =>
    operation === "experience.list" && args?.status === "approved" ? { status: "approved", lessons: [] } : original(operation, args));
  try {
    await act(async () => { form.setup.mockInput.pressArrow("right"); });
    await finalFrame(form.setup);
    expect(controls.mock.calls.filter(([op]) => op === "experience.list")).toEqual([["experience.list", { status: "approved" }]]);
    expect(await form.key("TAB")).toContain("›[F5] Refresh");
    await act(async () => { form.setup.mockInput.pressTab({ shift: true }); });
    await act(async () => { form.setup.mockInput.pressArrow("right"); }); await finalFrame(form.setup);
    expect(controls.mock.calls.filter(([op]) => op === "experience.list")).toEqual([
      ["experience.list", { status: "approved" }], ["experience.list", { status: "rejected" }],
    ]);
  } finally { controls.mockRestore(); await form.dispose(); }
});

test("keyboard actions skip an applied decision and still allow closing a busy session", async () => {
  const form = await panel();
  try {
    await act(async () => { form.controller.dismiss(); await form.controller.submit("/exp show test-approved"); });
    expect(await form.key("TAB")).toContain("›[E] Edit");
    await act(async () => { form.setup.mockInput.pressArrow("right"); });
    expect(await finalFrame(form.setup)).toContain("›[R] Reject");
    await act(async () => { form.controller.receive({ protocol_version: 2, type: "state", state: { ...form.controller.state!, task: { active: true, task_id: "busy" } } }); });
    expect(await finalFrame(form.setup)).toContain("›[Esc] Close");
    await form.key("RETURN"); expect(form.controller.overlay).toBeNull();
  } finally { await form.dispose(); }
});

test("moving keyboard focus from the lesson list to details visibly changes the focus indicator", async () => {
  const form = await panel();
  try {
    await form.key("DOWN");
    const before = form.setup.captureSpans();
    expect(form.setup.renderer.root.findDescendantById("experience-list")!.focused).toBe(true);
    await form.key("TAB");
    expect(form.setup.renderer.root.findDescendantById("experience-list")!.focused).toBe(false);
    expect(form.setup.renderer.root.findDescendantById("experience-detail")!.focused).toBe(true);
    expect(form.setup.captureSpans()).not.toEqual(before);
  } finally { await form.dispose(); }
});

test("resizing the detail page releases focus from hidden filters", async () => {
  const form = await panel(), controls = spyOn(form.controller, "control");
  try {
    await form.key("DOWN"); await form.key("RETURN");
    await act(async () => { form.setup.mockInput.pressTab({ shift: true }); form.setup.mockInput.pressTab({ shift: true }); });
    await finalFrame(form.setup);
    await act(async () => { form.setup.resize(80, 26); }); await finalFrame(form.setup);
    expect(form.setup.renderer.root.findDescendantById("experience-filter-pending")).toBeUndefined();
    expect(form.setup.renderer.root.findDescendantById("experience-detail")!.focused).toBe(true);
    await act(async () => { form.setup.mockInput.pressArrow("right"); }); await finalFrame(form.setup);
    expect(controls.mock.calls.filter(([op]) => op === "experience.list")).toHaveLength(0);
  } finally { controls.mockRestore(); await form.dispose(); }
});
