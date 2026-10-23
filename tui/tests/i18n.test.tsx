import { afterEach, expect, spyOn, test } from "bun:test";
import { act } from "react";
import { Writable } from "node:stream";
import { testRender } from "@opentui/react/test-utils";
import { currentLanguage, detectLanguage, setLanguage, t } from "../src/i18n";
import { record, SessionController } from "../src/session/controller";
import { completions, completionDescription } from "../src/session/completions";
import { TuiShell } from "../src/shells/TuiShell";
import { ReplShell } from "../src/shells/ReplShell";
import { WorkbenchState } from "../src/workbench/layout";
import { defaultThemeConfig } from "../src/theme/palette";
import type { ClientMessage } from "../src/transport/protocol.generated";
import english from "../../vulnclaw/i18n/en.json";
import chinese from "../../vulnclaw/i18n/zh.json";
import { finalFrame } from "./render-helpers";

afterEach(() => { setLanguage("en"); });

test("auto language is resolved locally from the terminal environment", () => {
  expect(detectLanguage({ VULNCLAW_LANG: "en", LANG: "zh_CN.UTF-8" })).toBe("en");
  expect(detectLanguage({ VULNCLAW_LANG: "ZH", LANG: "en_US.UTF-8" })).toBe("zh");
  expect(detectLanguage({ LANG: "zh_TW.UTF-8" })).toBe("zh");
  expect(detectLanguage({ LANG: "C.UTF-8" })).toBe("en");
  const controller = new SessionController();
  const previous = process.env.VULNCLAW_LANG;
  try {
    process.env.VULNCLAW_LANG = "zh";
    controller.applyConfigDocument({ config: { session: { language: "auto" } } });
    expect(currentLanguage()).toBe("zh");
    process.env.VULNCLAW_LANG = "en";
    controller.applyConfigDocument({ config: { session: { language: "auto" } } });
    expect(currentLanguage()).toBe("en");
    controller.applyConfigDocument({ config: { session: { language: "zh" } } });
    expect(currentLanguage()).toBe("zh");
  } finally {
    if (previous === undefined) delete process.env.VULNCLAW_LANG; else process.env.VULNCLAW_LANG = previous;
  }
  expect(t("Commands · {selected}/{count}", { selected: 1, count: 5 })).toBe("命令 · 1/5");
  expect(t("unregistered {value}", { value: "$&<literal>" })).toBe("unregistered $&<literal>");
  controller.receive({ protocol_version: 2, type: "control_result", operation: "language.set",
    result: { language: "en", message: "UI language switched to: English" } });
  expect(currentLanguage()).toBe("en");
  expect(controller.notice).toContain("English");
});

test("shared terminal catalogs preserve interpolation placeholders in both languages", () => {
  const en: Record<string, string> = english, zh: Record<string, string> = chinese;
  const placeholders = (value: string) => [...value.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort();
  for (const [key, value] of Object.entries(en).filter(([key]) => key.startsWith("terminal."))) {
    expect(zh[key]).toBeString(); expect(zh[key].length).toBeGreaterThan(0);
    expect(placeholders(zh[key])).toEqual(placeholders(value));
  }
});

for (const mode of ["tui", "repl"] as const) test(`${mode}: live language changes refresh controls and memoized message labels without translating content`, async () => {
  setLanguage("en");
  const controller = new SessionController(); controller.connected = true; controller.notice = "";
  controller.receive({ protocol_version: 2, type: "message", task_id: "fixture", message_id: "message",
    source: "main", role: "user", format: "text", operation: "append", text: "Settings /language zh #88CC99" });
  controller.draft = "draft untouched";
  const shell = mode === "tui" ? <TuiShell controller={controller} workbench={new WorkbenchState()} /> : <ReplShell controller={controller} />;
  const setup = await testRender(shell, { width: 120, height: 40, kittyKeyboard: true,
    screenMode: mode === "repl" ? "split-footer" : "alternate-screen", footerHeight: 12,
    externalOutputMode: mode === "repl" ? "capture-stdout" : "passthrough" });
  try {
    expect(await finalFrame(setup)).toContain("You");
    await act(async () => { controller.applyConfigDocument({ config: { session: { language: "zh" } } }); });
    const frame = await finalFrame(setup);
    expect(frame).toContain("你"); expect(frame).toContain("询问");
    expect(frame).toContain("Settings /language zh #88CC99");
    if (mode === "tui") for (const label of ["工作区", "会话记录", chinese["terminal.Capabilities"], "发现详情", "子代理"]) expect(frame).toContain(label);
    expect(controller.draft).toBe("draft untouched");
    await act(async () => { controller.draft = ""; controller.changed(); });
    expect(await finalFrame(setup)).toContain("输入问题，/ 查看命令，@ 选择技能");
    await act(async () => { setLanguage("en"); });
    expect(await finalFrame(setup)).toContain("You");
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("language and alias completions retain command tokens while translating descriptions", () => {
  setLanguage("zh"); const controller = new SessionController();
  controller.commands = { language: "Set display language" };
  controller.draft = "/language "; expect(completions(controller)).toEqual(["/language auto", "/language zh", "/language en"]);
  controller.draft = "/lang z"; expect(completions(controller)).toEqual(["/lang zh"]);
  expect(completionDescription(controller, "/language")).toBe("设置界面语言");
  controller.commands = { codescan: "Scan local source without an LLM" };
  expect(completionDescription(controller, "/codescan")).toBe("扫描本地源码，无需模型");
});

test("Chinese composer hints emit intact UTF-8 after a language command and subsequent redraws", async () => {
  setLanguage("en");
  const chunks: Buffer[] = [];
  const stdout = new Writable({ write(chunk, _encoding, callback) { chunks.push(Buffer.from(chunk)); callback(); } }) as NodeJS.WriteStream;
  stdout.isTTY = true; stdout.columns = 120; stdout.rows = 40; stdout.getColorDepth = () => 24;
  const controller = new SessionController(); controller.connected = true; controller.notice = "";
  controller.attach({ send() {}, async close() {} });
  const setup = await testRender(<TuiShell controller={controller} workbench={new WorkbenchState()} />, {
    width: 120, height: 40, kittyKeyboard: true, stdout, bufferedOutput: "stdout",
  });
  try {
    await finalFrame(setup);
    await act(async () => { setup.mockInput.pressKey("/language zh\r"); }); await finalFrame(setup);
    await act(async () => { controller.receive({ protocol_version: 2, type: "control_result", operation: "language.set", result: { language: "zh" } }); });
    await finalFrame(setup);
    for (let frame = 0; frame < 3; frame++) { await act(async () => { controller.changed(); }); await finalFrame(setup); }
    await setup.renderer.idle();
    const output = Buffer.concat(chunks).toString("utf8");
    expect(/[\u0080-\u009f]/.test(output)).toBe(false);
    expect(setup.captureCharFrame()).toContain("输入问题，/ 查看命令，@ 选择技能");
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("settings stages language edits, retains failed saves and switches the open form only after success", async () => {
  setLanguage("en");
  const controller = new SessionController(); controller.connected = true; controller.notice = "";
  const document = { config: { session: { language: "en" }, terminal: structuredClone(defaultThemeConfig) }, schema: {} };
  controller.overlay = { kind: "config", document };
  const control = spyOn(controller, "control").mockRejectedValueOnce(new Error("synthetic save failed"))
    .mockImplementation(async (_operation, args) => {
      expect(record(args?.changes)["session.language"]).toBe("zh");
      return { ...document, config: { ...document.config, session: { language: "zh" } } };
    });
  const setup = await testRender(<TuiShell controller={controller} workbench={new WorkbenchState()} />, { width: 104, height: 34, kittyKeyboard: true });
  async function click(id: string) {
    const node = setup.renderer.root.findDescendantById(id)!;
    await act(async () => { await setup.mockMouse.click(node.x + 1, node.y); }); await finalFrame(setup);
  }
  try {
    await finalFrame(setup); await click("settings-field-session.language");
    await act(async () => { setup.mockInput.pressEnter(); setup.mockInput.pressKey("\x1b[A"); setup.mockInput.pressEnter(); });
    await finalFrame(setup); expect(currentLanguage()).toBe("en");
    await act(async () => { setup.mockInput.pressKey("s", { ctrl: true }); });
    expect(await finalFrame(setup)).toContain("synthetic save failed"); expect(currentLanguage()).toBe("en");
    await act(async () => { setup.mockInput.pressKey("s", { ctrl: true }); });
    let frame = await finalFrame(setup);
    for (const label of ["设置", "语言", "配置已保存。", chinese["terminal.{count} unsaved changes"].replace("{count}", "0")]) expect(frame).toContain(label);
    await click("settings-category-themes"); await click("settings-field-blue");
    await act(async () => { setup.mockInput.pressEnter(); });
    frame = await finalFrame(setup); expect(frame).toContain("主会话背景"); expect(frame).toContain("#1e2127");
    await act(async () => { setup.resize(64, 22); }); await finalFrame(setup);
    const footer = setup.renderer.root.findDescendantById("settings-footer")!;
    expect(footer.y + footer.height).toBeLessThanOrEqual(22);
    expect(currentLanguage()).toBe("zh");
  } finally { control.mockRestore(); await act(async () => { await setup.dispose(); }); }
});

test("Chinese approval keeps the command, draft and approval protocol intact", async () => {
  setLanguage("zh");
  const controller = new SessionController(); const requests: ClientMessage[] = [];
  controller.attach({ send(request) { requests.push(request); }, async close() {} });
  controller.draft = "keep draft";
  controller.receive({ protocol_version: 2, type: "approval_required", task_id: "fixture", request_hash: "a".repeat(64),
    question: "echo Settings", detail: "synthetic original reason", cwd: "/tmp/fixture" });
  const setup = await testRender(<TuiShell controller={controller} workbench={new WorkbenchState()} />, { width: 100, height: 32, kittyKeyboard: true });
  try {
    const frame = await finalFrame(setup);
    for (const label of ["执行审批", "[Y] 批准", "[N/Esc] 拒绝", "echo Settings", "synthetic original reason"]) expect(frame).toContain(label);
    await act(async () => { setup.mockInput.pressKey("n"); });
    const request = requests.at(-1);
    expect(request?.type === "control" && request.payload?.arguments).toEqual({ request_hash: "a".repeat(64), decision: "deny" });
    expect(controller.draft).toBe("keep draft");
  } finally { await act(async () => { await setup.dispose(); }); }
});
