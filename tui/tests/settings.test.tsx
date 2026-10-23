import { expect, spyOn, test } from "bun:test";
import { act } from "react";
import type { ScrollBoxRenderable, TextareaRenderable } from "@opentui/core";
import { testRender } from "@opentui/react/test-utils";
import { ConfigForm } from "../src/components/ConfigForm";
import { record, SessionController } from "../src/session/controller";
import { withValue } from "../src/components/config-fields";
import { finalFrame } from "./render-helpers";
import { configureThemes, defaultThemeConfig, palettes } from "../src/theme/palette";

function fixture() {
  return {
    config: {
      terminal: structuredClone(defaultThemeConfig),
      llm: { provider: "old", website_url: "", base_url: "https://old.test", model: "old-model", api_key: "***",
        api_keys: ["***", "***"], temperature: 0.1, max_tokens: 4096 },
      session: { language: "auto", show_thinking: false, auto_save: true, output_dir: "./output", runs_dir: null,
        report_format: "markdown", engine: "solve", solve_max_steps: 240, solve_max_tool_rounds: 6,
        solve_max_parallel: 1, max_rounds: 15, context_auto_compact: true, context_compact_trigger_ratio: 0.7,
        solve_auto_compact: false, experience_enabled: false },
      safety: { permission_mode: "ask", trusted_commands: ["git diff"], approval_timeout_seconds: 300 },
      mcp: { servers: { existing: { name: "existing", enabled: true, priority: 1, description: "",
        transport: { type: "stdio", command: "fixture", args: [], env: { TOKEN: "***" }, startup_timeout: 30000, tool_timeout: 300000 } } } },
    },
    schema: { type: "object", properties: {
      llm: { type: "object", properties: { api_keys: { type: "array", items: { type: "string" } } } },
      session: { $ref: "#/$defs/Session" }, safety: { type: "object", properties: {
        permission_mode: { type: "string", enum: ["read_only", "ask", "auto_review", "full_access"] },
      } },
    }, $defs: {
      TerminalTheme: { type: "object", properties: Object.fromEntries(Object.keys(defaultThemeConfig.themes.blue)
        .map((key) => [key, { type: "string", pattern: "^#[0-9a-fA-F]{6}$" }])) },
      Session: { type: "object", properties: {
        engine: { type: "string", enum: ["solve", "team", "rounds"] },
        runs_dir: { anyOf: [{ type: "string" }, { type: "null" }] },
        solve_max_steps: { type: "integer", description: "Execution step budget." },
        context_compact_trigger_ratio: { type: "number", minimum: 0.1, maximum: 0.95, description: "Compact before the context fills." },
        solve_auto_compact: { type: "boolean", description: "Deprecated alias." },
        experience_enabled: { type: "boolean", default: false },
      } },
      MCPServerConfig: { type: "object", properties: { transport: { $ref: "#/$defs/Transport" } } },
      Transport: { type: "object", properties: { args: { type: "array", items: { type: "string" } },
        startup_timeout: { type: "integer" }, tool_timeout: { type: "integer" } } },
    } },
    providers: [{ provider: "old", label: "Old" }, { provider: "new", label: "New" }],
    secret_paths: ["llm.api_key", "llm.api_keys", "mcp.servers.existing.transport.env.TOKEN"],
    builtin_servers: [],
  };
}

async function settings() {
  const controller = new SessionController(); controller.draft = "composer draft";
  const document = fixture(); let saved: Record<string, unknown> = structuredClone(document.config);
  const requests: Record<string, unknown>[] = [];
  const control = spyOn(controller, "control").mockImplementation(async (operation, args) => {
    if (operation === "config.models") return { models: args?.base_url === "https://old.test" ? ["old-model", "old-model-extra"] : [] };
    if (operation === "config.preset") return { provider: "new", base_url: "https://new.test", model: "new-default", website_url: "" };
    expect(operation).toBe("config.update");
    const changes = record(args?.changes); requests.push(changes);
    for (const [path, value] of Object.entries(changes)) saved = withValue(saved, path, value);
    // config_document returns masked credentials and rebases saved-item references.
    const keys = record(saved.llm).api_keys;
    if (Array.isArray(keys)) saved = withValue(saved, "llm.api_keys", keys.map(() => "***"));
    return { ...document, config: saved };
  });
  const setup = await testRender(<ConfigForm controller={controller} active document={document} />, { width: 104, height: 32, kittyKeyboard: true });
  async function click(id: string) {
    const node = setup.renderer.root.findDescendantById(id)!; expect(node).toBeDefined();
    const scroll = setup.renderer.root.findDescendantById("settings-scroll") as ScrollBoxRenderable;
    if (id.startsWith("settings-field-")) {
      await act(async () => { scroll.scrollChildIntoView(node.parent!.id); }); await finalFrame(setup);
    }
    await act(async () => { await setup.mockMouse.click(node.x + 1, node.y); }); await finalFrame(setup);
  }
  async function activate(key: string) {
    await click("settings-field-" + key);
    await act(async () => { setup.mockInput.pressEnter(); }); await finalFrame(setup);
  }
  async function edit(key: string, value: string, submit = true) {
    await activate(key);
    await act(async () => {
      const editor = setup.renderer.root.findDescendantById("settings-editor") as TextareaRenderable;
      expect(editor).toBeDefined(); editor.setText(""); await setup.mockInput.typeText(value);
      if (submit) setup.mockInput.pressEnter();
    }); await finalFrame(setup);
  }
  async function save() {
    await act(async () => { setup.mockInput.pressKey("s", { ctrl: true }); }); return finalFrame(setup);
  }
  async function dispose() { control.mockRestore(); await act(async () => { await setup.dispose(); }); configureThemes(defaultThemeConfig); }
  await finalFrame(setup);
  return { ...setup, controller, control, requests, click, activate, edit, save, dispose };
}

test("settings keeps categories and footer visible, uses engine-specific fields and schema limits", async () => {
  const setup = await settings();
  try {
    let frame = await finalFrame(setup);
    for (const name of ["General", "Model", "Execution", "Approval & tools", "MCP servers", "Advanced", "0 unsaved changes"]) expect(frame).toContain(name);
    expect(frame).not.toContain("Step limit");
    await setup.click("settings-category-execution");
    frame = await finalFrame(setup); expect(frame).toContain("Step limit"); expect(frame).not.toContain("Round limit");
    await setup.activate("session.engine");
    expect(await finalFrame(setup)).toContain("rounds");
    await act(async () => { setup.mockInput.pressArrow("down"); setup.mockInput.pressArrow("down"); setup.mockInput.pressEnter(); });
    frame = await finalFrame(setup); expect(frame).toContain("Round limit"); expect(frame).not.toContain("Step limit");
    await setup.click("settings-category-advanced");
    expect(await finalFrame(setup)).not.toContain("Solve auto compact");
    await setup.edit("session.context_compact_trigger_ratio", "1.2");
    frame = await setup.save(); expect(frame).toContain("at most 0.95"); expect(setup.requests).toHaveLength(0);
    await act(async () => { setup.mockInput.pressEscape(); });
    await setup.edit("session.context_compact_trigger_ratio", "0.8");
    frame = await setup.save(); expect(setup.requests.at(-1)).toEqual({ "session.engine": "rounds", "session.context_compact_trigger_ratio": 0.8 });
    expect(frame).toContain("Configuration saved."); expect(setup.controller.draft).toBe("composer draft");
    await act(async () => { setup.resize(64, 22); });
    const footer = setup.renderer.root.findDescendantById("settings-footer")!;
    await finalFrame(setup); expect(footer.y + footer.height).toBeLessThanOrEqual(22);
  } finally { await setup.dispose(); }
});

test("automatic experience learning is off by default and saves explicit opt-in and opt-out", async () => {
  const setup = await settings();
  try {
    await setup.click("settings-category-advanced");
    expect(await finalFrame(setup)).toContain("Automatic experience");
    await setup.activate("session.experience_enabled"); await setup.save();
    expect(setup.requests.at(-1)).toEqual({ "session.experience_enabled": true });
    await setup.activate("session.experience_enabled"); await setup.save();
    expect(setup.requests.at(-1)).toEqual({ "session.experience_enabled": false });
  } finally { await setup.dispose(); }
});

test("Ctrl+S submits the active edit once and failed saves retain the draft and error inside settings", async () => {
  const setup = await settings();
  try {
    await setup.click("settings-category-execution"); await setup.edit("session.solve_max_steps", "87", false);
    setup.control.mockImplementationOnce(async () => { throw new Error("session.solve_max_steps: backend rejected the draft"); });
    let frame = await setup.save();
    expect(frame).toContain("backend rejected"); expect(frame).toContain("87"); expect(frame).toContain("1 unsaved changes");
    frame = await setup.save();
    expect(setup.requests).toEqual([{ "session.solve_max_steps": 87 }]); expect(frame).toContain("Configuration saved.");
    await setup.click("settings-category-general"); await setup.activate("session.auto_save");
    expect(await finalFrame(setup)).toContain("1 unsaved changes");
    const dismiss = spyOn(setup.controller, "dismiss");
    await act(async () => { setup.mockInput.pressEscape(); });
    expect(dismiss).toHaveBeenCalledTimes(1); expect(setup.requests).toHaveLength(1); dismiss.mockRestore();
  } finally { await setup.dispose(); }
});

test("provider settings reuse the five-field editor, discard old model suggestions and save other categories together", async () => {
  const setup = await settings();
  try {
    await setup.activate("session.auto_save");
    await setup.click("settings-category-model"); await setup.activate("connection");
    await act(async () => { for (let i = 0; i < 4; i++) setup.mockInput.pressArrow("down"); setup.mockInput.pressEnter(); });
    expect(await finalFrame(setup)).toContain("old-model-extra");
    await act(async () => { setup.mockInput.pressEscape(); for (let i = 0; i < 4; i++) setup.mockInput.pressArrow("up"); setup.mockInput.pressEnter(); });
    await finalFrame(setup);
    await act(async () => { setup.mockInput.pressArrow("down"); setup.mockInput.pressEnter(); });
    let frame = await finalFrame(setup); expect(frame).toContain("new-default"); expect(frame).not.toContain("old-model-extra");
    frame = await setup.save();
    expect(setup.requests.at(-1)).toEqual({ "session.auto_save": false, "llm.provider": "new", "llm.base_url": "https://new.test", "llm.model": "new-default" });
    expect(frame).toContain("Settings"); expect(frame).toContain("Configuration saved.");
  } finally { await setup.dispose(); }
});

test("secret list removal retains saved item identity, and clearing a key is explicit", async () => {
  const setup = await settings();
  try {
    await setup.click("settings-category-model"); await setup.activate("model-advanced");
    await setup.activate("llm.api_keys");
    let frame = await finalFrame(setup); expect(frame).toContain("Configured"); expect(frame).not.toContain("$keep");
    await setup.click("settings-field-item-0");
    await setup.click("settings-remove");
    await setup.save(); expect(setup.requests.at(-1)).toEqual({ "llm.api_keys": [{ $keep: 1 }] });
    await setup.click("settings-category-model");
    await setup.click("settings-field-llm.api_key"); await setup.click("settings-clear");
    await setup.save(); expect(setup.requests.at(-1)).toEqual({ "llm.api_key": "" });
    expect(await finalFrame(setup)).toContain("Not configured");
  } finally { await setup.dispose(); }
});

test("MCP services use typed fields and item editors, preserve other services and reject duplicate names", async () => {
  const setup = await settings();
  try {
    await setup.click("settings-category-mcp"); await setup.activate("add-server");
    await setup.edit("name", "existing"); await setup.edit("transport.command", "fixture-new");
    await setup.activate("apply-server");
    expect(await finalFrame(setup)).toContain("already exists"); expect(setup.requests).toHaveLength(0);
    await setup.edit("name", "custom"); await setup.activate("transport.args"); await setup.activate("add-item");
    await setup.edit("item-0", "--stdio"); await setup.activate("back");
    await setup.activate("transport.env"); await setup.activate("add-item"); await setup.edit("name-0", "TOKEN");
    await setup.edit("value-0", "synthetic-secret");
    expect(await finalFrame(setup)).not.toContain("synthetic-secret");
    await setup.activate("back"); await setup.activate("apply-server");
    let frame = await setup.save(); expect(frame).toContain("Configuration saved.");
    const servers = record(setup.requests.at(-1)?.["mcp.servers"]);
    expect(Object.keys(servers)).toEqual(["existing", "custom"]);
    expect(record(record(servers.custom).transport).args).toEqual(["--stdio"]);
    expect(record(record(servers.custom).transport).env).toEqual({ TOKEN: "synthetic-secret" });
    await setup.activate("custom"); await setup.activate("delete-server"); await setup.save();
    expect(Object.keys(record(setup.requests.at(-1)?.["mcp.servers"]))).toEqual(["existing"]);
  } finally { await setup.dispose(); }
});

test("theme edits stay independent, retain failed drafts and take effect only after saving", async () => {
  const setup = await settings();
  try {
    await setup.click("settings-category-themes");
    expect(await finalFrame(setup)).not.toContain("Shared colors");
    await setup.activate("blue");
    await setup.edit("color-transcript", "#223344");
    expect(palettes.blue.transcript).toBe("#1e2127");
    await setup.activate("apply-theme");
    await setup.activate("orange");
    expect(await finalFrame(setup)).toContain("#1e2127");
    await act(async () => { setup.mockInput.pressEscape(); }); await finalFrame(setup);
    setup.control.mockRejectedValueOnce(new Error("theme write failed"));
    expect(await setup.save()).toContain("theme write failed");
    expect(palettes.blue.transcript).toBe("#1e2127");
    expect(await setup.save()).toContain("Configuration saved.");
    const themes = record(setup.requests.at(-1)?.["terminal.themes"]);
    expect(record(themes.blue).transcript).toBe("#223344");
    expect(record(themes.orange).transcript).toBe("#1e2127");
    expect(Object.keys(record(themes.blue))).toHaveLength(24);
    expect(palettes.blue.transcript).toBe("#223344");
    expect(palettes.orange.transcript).toBe("#1e2127");
    expect(setup.controller.draft).toBe("composer draft");
  } finally { await setup.dispose(); }
});

test("new themes copy the current palette, validate names and colors and save the active edit once", async () => {
  const setup = await settings();
  try {
    await setup.click("settings-category-themes"); await setup.activate("add-theme");
    await setup.edit("theme-name", "blue"); await setup.activate("apply-theme");
    expect(await finalFrame(setup)).toContain("already exists"); expect(setup.requests).toHaveLength(0);
    await setup.edit("theme-name", "forest");
    await setup.edit("color-action", "#88cc99");
    await setup.edit("color-transcript", "invalid", false);
    expect(await setup.save()).toContain("must match"); expect(setup.requests).toHaveLength(0);
    await act(async () => { setup.mockInput.pressEscape(); }); await finalFrame(setup);
    await setup.edit("color-transcript", "#233029", false);
    expect(await setup.save()).toContain("Configuration saved.");
    expect(setup.requests).toHaveLength(1);
    expect(record(record(setup.requests[0]["terminal.themes"]).forest)).toEqual({
      ...defaultThemeConfig.themes.blue, action: "#88cc99", transcript: "#233029",
    });
    expect(palettes.blue.action).toBe("#7aa2f7");
    await setup.activate("forest"); await setup.activate("use-theme");
    expect(setup.controller.theme).toBe("blue");
    await setup.save(); expect(setup.controller.theme).toBe("forest");
    expect(setup.requests.at(-1)).toEqual({ "terminal.theme": "forest" });
    await setup.activate("forest");
    await act(async () => { setup.resize(64, 22); }); await finalFrame(setup);
    const footer = setup.renderer.root.findDescendantById("settings-footer")!;
    expect(footer.y + footer.height).toBeLessThanOrEqual(22);
  } finally { await setup.dispose(); }
});

test("rapid theme list navigation opens the focused row", async () => {
  const setup = await settings();
  try {
    await setup.click("settings-category-themes");
    await act(async () => { setup.mockInput.pressKey("\x1b[B\x1b[B\r"); });
    expect(await finalFrame(setup)).toContain("New theme");
  } finally { await setup.dispose(); }
});

test("settings cannot edit or save while an approval owns input", async () => {
  const controller = new SessionController();
  const control = spyOn(controller, "control");
  const setup = await testRender(<ConfigForm controller={controller} document={fixture()} active={false} />, { width: 104, height: 30, kittyKeyboard: true });
  try {
    await finalFrame(setup);
    await act(async () => { setup.mockInput.pressEnter(); setup.mockInput.pressKey("s", { ctrl: true }); });
    expect(control).not.toHaveBeenCalled(); expect(setup.renderer.root.findDescendantById("settings-editor")).toBeUndefined();
  } finally { control.mockRestore(); await act(async () => { await setup.dispose(); }); }
});

test("saved key indices remain correct after removing a key, saving and reopening", async () => {
  const setup = await settings();
  try {
    await setup.click("settings-category-model"); await setup.activate("model-advanced");
    await setup.activate("llm.api_keys"); await setup.click("settings-field-item-0");
    await setup.click("settings-remove");
    expect(await setup.save()).toContain("Configuration saved.");
    expect(setup.requests.at(-1)).toEqual({ "llm.api_keys": [{ $keep: 1 }] });
    await setup.activate("llm.api_keys"); await setup.activate("add-item");
    await setup.edit("item-1", "replacement-key");
    expect(await setup.save()).toContain("Configuration saved.");
    expect(setup.requests.at(-1)).toEqual({ "llm.api_keys": [{ $keep: 0 }, "replacement-key"] });
  } finally { await setup.dispose(); }
});

test("MCP argument edits survive saving and reopening the server", async () => {
  const setup = await settings();
  try {
    await setup.click("settings-category-mcp"); await setup.activate("existing");
    await setup.activate("transport.args"); await setup.activate("add-item");
    await setup.edit("item-0", "--first");
    expect(await setup.save()).toContain("Configuration saved.");
    await setup.activate("existing"); await setup.activate("transport.args");
    await setup.activate("add-item"); await setup.edit("item-1", "--second");
    expect(await setup.save()).toContain("Configuration saved.");
    const servers = record(setup.requests.at(-1)?.["mcp.servers"]);
    expect(record(record(servers.existing).transport).args).toEqual(["--first", "--second"]);
  } finally { await setup.dispose(); }
});
