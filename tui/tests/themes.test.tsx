import { expect, spyOn, test } from "bun:test";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { act } from "react";
import { RGBA } from "@opentui/core";
import { MarkdownBody } from "../src/components/MarkdownBody";
import { testRender } from "@opentui/react/test-utils";
import { configureThemes, defaultThemeConfig, palettes } from "../src/theme/palette";
import { legacyThemeConfig, writeJson } from "../src/platform/preferences";
import { SessionController } from "../src/session/controller";
import { completions } from "../src/session/completions";
import { TuiShell } from "../src/shells/TuiShell";
import { defaultLayout, WorkbenchState } from "../src/workbench/layout";
import { finalFrame } from "./render-helpers";

test("legacy colors and selection migrate into complete independent themes without rewriting old files", async () => {
  const directory = await mkdtemp(join(tmpdir(), "vulnclaw-themes-"));
  const path = join(directory, "themes.json");
  try {
    expect(await legacyThemeConfig(directory)).toBeNull();
    const config = { shared: { transcript: "#1f242a" }, themes: {
      orange: { action: "#ff8800" }, forest: { action: "#88cc99", composerBorder: "#88cc99" },
    } };
    await writeJson(path, config);
    await writeJson(join(directory, "layout.json"), { format_version: 2, theme: "forest", layout: defaultLayout() });
    const migrated = (await legacyThemeConfig(directory))!;
    expect(migrated.theme).toBe("forest");
    expect(migrated.themes.forest.transcript).toBe("#1f242a");
    expect(migrated.themes.forest.text).toBe(defaultThemeConfig.themes.blue.text);
    expect(migrated.themes.orange.action).toBe("#ff8800");
    expect(migrated.themes.orange.accent).toBe(defaultThemeConfig.themes.orange.accent);
    expect(Object.keys(migrated.themes.forest)).toHaveLength(24);
    expect(JSON.parse(await readFile(path, "utf-8"))).toEqual(config);
    await writeFile(path, "{ invalid json");
    await expect(legacyThemeConfig(directory)).rejects.toThrow();
    expect(await readFile(path, "utf-8")).toBe("{ invalid json");
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test("theme selection and completion use the main configuration and preserve selection on failed saves", async () => {
  const terminal = structuredClone({ ...defaultThemeConfig, themes: {
    ...defaultThemeConfig.themes, forest: { ...defaultThemeConfig.themes.blue, action: "#88cc99" },
  } });
  configureThemes(terminal);
  const controller = new SessionController();
  const control = spyOn(controller, "control").mockImplementation(async (_operation, args) => {
    terminal.theme = String((args?.changes as Record<string, unknown>)["terminal.theme"]);
    return { config: { terminal: structuredClone(terminal) } };
  });
  try {
    controller.draft = "/theme ";
    expect(completions(controller)).toEqual(["/theme blue", "/theme orange", "/theme forest"]);
    controller.draft = "/theme fo";
    expect(completions(controller)).toEqual(["/theme forest"]);
    await controller.submit("/theme forest"); expect(controller.theme).toBe("forest");
    await controller.submit("/theme"); expect(controller.theme).toBe("blue");
    await controller.submit("/theme"); expect(controller.theme).toBe("orange");
    await controller.submit("/theme"); expect(controller.theme).toBe("forest");
    await controller.submit("/theme missing");
    expect(controller.theme).toBe("forest"); expect(controller.notice).toContain("blue, orange, forest");
    expect(control).toHaveBeenCalledTimes(4);
    expect(control).toHaveBeenLastCalledWith("config.update", { changes: { "terminal.theme": "forest" } });
    control.mockRejectedValueOnce(new Error("save failed"));
    await controller.submit("/theme blue");
    expect(controller.theme).toBe("forest"); expect(controller.notice).toContain("save failed");
    expect(controller.draft).toBe("/theme blue");
  } finally { control.mockRestore(); configureThemes(defaultThemeConfig); }
});

test.each([
  [[], "must be an object"],
  [{ shared: {} }, "Unknown theme configuration field: shared"],
  [{ theme: "bad name", themes: { "bad name": {} } }, "Invalid theme name"],
  [{ theme: "blue", themes: { blue: { ...defaultThemeConfig.themes.blue, typo: "#123456" } } }, "Unknown color"],
  [{ theme: "blue", themes: { blue: { ...defaultThemeConfig.themes.blue, border: "blue" } } }, "terminal.themes.blue.border must be a #RRGGBB color"],
  [{ theme: "forest", themes: { forest: { action: "#88cc99" } } }, "Missing color"],
  [{ theme: "missing", themes: defaultThemeConfig.themes }, "Unknown terminal theme"],
] as const)("invalid theme configuration leaves the current themes intact", (config, message) => {
  const before = palettes;
  expect(() => configureThemes(config)).toThrow(message); expect(palettes).toBe(before);
});

test("custom theme colors reach the native transcript, title, logo, composer, permission and Markdown", async () => {
  const forest = { ...defaultThemeConfig.themes.blue, transcript: "#233029", action: "#88cc99",
    composerBorder: "#66ddaa", permissionAsk: "#ddaa66", codeString: "#cc88aa" };
  configureThemes({ theme: "forest", themes: { forest } });
  const controller = new SessionController(); controller.connected = true; controller.notice = "";
  controller.setTheme("forest");
  controller.receive({ protocol_version: 2, type: "message", task_id: "fixture", source: "main", role: "assistant",
    message_id: "fixture", operation: "append", format: "markdown", text: '```python\nprint("configured")\n```' });
  const setup = await testRender(<TuiShell controller={controller} workbench={new WorkbenchState()} />, { width: 120, height: 36 });
  try {
    await finalFrame(setup);
    const frame = setup.captureSpans();
    const node = (id: string) => setup.renderer.root.findDescendantById(id)!;
    const cell = (x: number, y: number) => {
      let end = 0;
      return frame.lines[y].spans.find((span) => { end += span.width; return x < end; })!;
    };
    const output = node("output-pane"), title = node("title-output"), input = node("composer-input"), header = node("terminal-header");
    expect(cell(output.x + 2, output.y + 10).bg).toEqual(RGBA.fromHex("#233029"));
    expect(cell(title.x + 2, title.y).bg).toEqual(RGBA.fromHex("#233029"));
    expect(cell(output.x, title.y).bg).toEqual(RGBA.fromHex("#181a1f"));
    expect(cell(header.x + 2, header.y + 1).bg).toEqual(RGBA.fromHex("#88cc99"));
    expect(cell(input.x + 2, input.y).fg).toEqual(RGBA.fromHex("#66ddaa"));
    expect(cell(input.x + 2, input.y + 1).bg).toEqual(RGBA.fromHex("#2a2e36"));
    const permission = node("permission-label");
    expect(cell(permission.x, permission.y).fg).toEqual(RGBA.fromHex("#ddaa66"));
    const code = frame.lines.flatMap((line) => line.spans).find((span) => span.text.includes("configured"));
    expect(code?.fg).toEqual(RGBA.fromHex("#cc88aa"));
    await act(async () => { controller.applyConfigDocument({ config: { terminal: {
      theme: "forest", themes: { forest: { ...forest, codeString: "#66aaff" } },
    } } }); });
    await finalFrame(setup);
    expect(setup.captureSpans().lines.flatMap((line) => line.spans).find((span) => span.text.includes("configured"))?.fg)
      .toEqual(RGBA.fromHex("#66aaff"));
  } finally { await act(async () => { await setup.dispose(); }); configureThemes(defaultThemeConfig); }
});
const cases = [
  ["plain Markdown", "themevalue", 60],
  ["narrow table", "| Name | Value |\n| --- | --- |\n| sample | themevalue |", 20],
  ["wide table", "| Name | Value |\n| --- | --- |\n| sample | themevalue |", 60],
  ["untyped code", "```\nthemevalue\n```", 60],
  ["unknown code", "```unknown\nthemevalue\n```", 60],
  ["Python identifiers", "```python\nthemevalue = 1\n```", 60],
] as const;
for (const [name, text, width] of cases) {
  test(`${name} adopts saved body text color`, async () => {
    configureThemes(defaultThemeConfig);
    const controller = new SessionController();
    const setup = await testRender(<MarkdownBody id="theme-audit" text={text} width={width} theme="blue" />, { width, height: 18 });
    try {
      expect(await finalFrame(setup)).toContain("themevalue");
      await act(async () => { controller.applyConfigDocument({ config: { terminal: {
        theme: "blue", themes: { ...defaultThemeConfig.themes, blue: { ...defaultThemeConfig.themes.blue, text: "#123456" } },
      } } }); });
      await finalFrame(setup);
      const span = setup.captureSpans().lines.flatMap((line) => line.spans).find((span) => span.text.includes("themevalue"));
      expect(span?.fg).toEqual(RGBA.fromHex("#123456"));
    } finally { await act(async () => { await setup.dispose(); }); configureThemes(defaultThemeConfig); }
  });
}

test("replacing the active theme with a disjoint theme map refreshes mounted content", async () => {
  configureThemes(defaultThemeConfig);
  const controller = new SessionController(); controller.connected = true; controller.notice = "";
  controller.receive({ protocol_version: 2, type: "message", task_id: "fixture", source: "main", role: "assistant",
    message_id: "fixture", operation: "append", format: "markdown", text: "themevalue" });
  const setup = await testRender(<TuiShell controller={controller} workbench={new WorkbenchState()} />, { width: 120, height: 36 });
  try {
    await finalFrame(setup);
    await act(async () => { controller.applyConfigDocument({ config: { terminal: {
      theme: "forest", themes: { forest: { ...defaultThemeConfig.themes.blue, text: "#123456", action: "#abcdef" } },
    } } }); });
    expect(await finalFrame(setup)).toContain("themevalue");
    expect(controller.theme).toBe("forest");
    const span = setup.captureSpans().lines.flatMap(line => line.spans).find(span => span.text.includes("themevalue"));
    expect(span?.fg).toEqual(RGBA.fromHex("#123456"));
  } finally { await act(async () => { await setup.dispose(); }); configureThemes(defaultThemeConfig); }
});
for (const kind of ["config", "llm", "task", "reader", "data", "approval"] as const) test(`${kind} dialog adopts saved colors while open`, async () => {
  configureThemes(defaultThemeConfig);
  const controller = new SessionController(); controller.connected = true; controller.notice = "";
  if (kind === "config") controller.overlay = { kind, document: { config: { terminal: structuredClone(defaultThemeConfig), session: { language: "en" } } } };
  if (kind === "llm") controller.overlay = { kind, document: { provider: "fixture", model: "fixture", providers: [] } };
  if (kind === "task") controller.overlay = { kind, preview: { task: { command: "run", target: "fixture.invalid", options: {} } }, original: "/run fixture.invalid" };
  if (kind === "reader") {
    controller.receive({ protocol_version: 2, type: "message", task_id: "fixture", source: "main", role: "assistant",
      message_id: "read", operation: "append", format: "markdown", text: "reader content" });
    controller.readMessage("read");
  }
  if (kind === "data") controller.overlay = { kind, title: "Data", format: "text", data: "plain data" };
  if (kind === "approval") controller.receive({ protocol_version: 2, type: "approval_required", task_id: "fixture",
    request_hash: "a".repeat(64), question: "synthetic approval" });
  const setup = await testRender(<TuiShell controller={controller} workbench={new WorkbenchState()} />, { width: 120, height: 40 });
  try {
    await finalFrame(setup);
    const next = { ...defaultThemeConfig.themes.blue, panel: "#102030", action: "#abcdef", accent: "#aabbcc", error: "#ff1234" };
    await act(async () => { controller.applyConfigDocument({ config: { terminal: {
      theme: "blue", themes: { ...defaultThemeConfig.themes, blue: next },
    } } }); });
    await finalFrame(setup);
    const id = kind === "config" ? "settings-pane" : kind === "task" ? "form-pane" : kind === "reader" || kind === "data" ? "reader-pane" : kind + "-pane";
    const pane = setup.renderer.root.findDescendantById(id)!;
    expect(pane).toBeDefined();
    let end = 0;
    const corner = setup.captureSpans().lines[pane.y].spans.find(span => { end += span.width; return pane.x < end; })!;
    expect(corner.bg).toEqual(RGBA.fromHex(next.panel));
    expect(corner.fg).toEqual(RGBA.fromHex(kind === "approval" ? next.error : kind === "reader" || kind === "data" ? next.accent : next.action));
  } finally { await act(async () => { await setup.dispose(); }); configureThemes(defaultThemeConfig); }
});
