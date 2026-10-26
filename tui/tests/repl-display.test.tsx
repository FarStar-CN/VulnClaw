import { expect, test } from "bun:test";
import { act } from "react";
import { Writable } from "node:stream";
import { NativeSession, RGBA } from "@opentui/core";
import { testRender } from "@opentui/react/test-utils";
import { SessionController } from "../src/session/controller";
import { ReplShell } from "../src/shells/ReplShell";
import { configureThemes, defaultThemeConfig, palettes } from "../src/theme/palette";
import { syncReplBackground } from "../src/platform/repl-background";
import { version } from "../package.json";
import { finalFrame } from "./render-helpers";

test("REPL shows a themed VulnClaw wordmark and collapses it after the first reply", async () => {
  const controller = new SessionController(); controller.connected = true;
  controller.runtime = { provider: "fixture", model: "fixture-model", config_ready: true,
    mcp_started: 0, skills: [] };
  const setup = await testRender(<ReplShell controller={controller} />, {
    width: 120, height: 30, screenMode: "alternate-screen",
  });
  try {
    const frame = await finalFrame(setup);
    expect(frame).toContain(`VulnClaw v${version}`);
    expect(frame).toContain("fixture-model · fixture");
    expect(frame).toContain(process.cwd());
    expect(frame).toContain("__     __          _            ____   _");
    const mark = setup.renderer.root.findDescendantById("repl-welcome-wordmark")!;
    const topRule = setup.renderer.root.findDescendantById("repl-welcome-top-rule")!;
    const bottomRule = setup.renderer.root.findDescendantById("repl-welcome-bottom-rule")!;
    expect(topRule.y).toBe(mark.y);
    expect(bottomRule.y).toBe(mark.y + 4);
    expect(frame.split("\n")[topRule.y]).toContain("─".repeat(44));
    expect(frame.split("\n")[bottomRule.y]).toContain("─".repeat(44));
    const colorAt = (x: number, y: number) => {
      let end = 0;
      return setup.captureSpans().lines[y].spans.find((span) => {
        end += span.width; return x < end;
      })!.fg;
    };
    const action = RGBA.fromHex(palettes[controller.theme].action);
    expect(colorAt(mark.x, mark.y)).toEqual(action);
    expect(colorAt(topRule.x, topRule.y)).toEqual(action);
    expect(colorAt(bottomRule.x, bottomRule.y)).toEqual(action);
    expect(frame.match(/Ask a question/g)).toHaveLength(1);

    await act(async () => { setup.resize(30, 16); });
    const compactFrame = await finalFrame(setup);
    expect(compactFrame).toContain(`VulnClaw v${version}`);
    const compactTop = setup.renderer.root.findDescendantById("repl-welcome-top-rule")!;
    const compactBottom = setup.renderer.root.findDescendantById("repl-welcome-bottom-rule")!;
    expect(compactBottom.y).toBe(compactTop.y + 2);
    expect(compactFrame.split("\n")[compactTop.y]).toContain("─".repeat(24));
    expect(compactFrame.split("\n")[compactBottom.y]).toContain("─".repeat(24));
    expect(setup.renderer.root.findDescendantById("repl-welcome-wordmark")).toBeUndefined();

    await act(async () => { controller.receive({ protocol_version: 2, type: "message", task_id: "fixture",
      message_id: "reply", source: "main", role: "assistant", format: "text",
      operation: "append", text: "First reply" }); });
    const reply = await finalFrame(setup);
    expect(reply).toContain("First reply");
    expect(setup.renderer.root.findDescendantById("repl-welcome")).toBeUndefined();
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("REPL keeps streaming and completed replies in one full-height transcript", async () => {
  const controller = new SessionController(); controller.connected = true;
  const setup = await testRender(<ReplShell controller={controller} />, {
    width: 80, height: 40, screenMode: "alternate-screen",
  });
  const event = { protocol_version: 2, type: "message", task_id: "fixture", source: "main",
    role: "assistant", format: "text", message_id: "reply" } as const;
  try {
    await act(async () => { controller.receive({ ...event, operation: "append", text: "First reply" }); });
    expect(await finalFrame(setup)).toContain("First reply");
    const entry = setup.renderer.root.findDescendantById("entry-reply")!;
    const transcript = setup.renderer.root.findDescendantById("transcript-scroll")!;
    expect(transcript.height).toBe(35);
    await act(async () => { controller.receive({ ...event, operation: "end", status: "completed" }); });
    expect(await finalFrame(setup)).toContain("First reply");
    expect(setup.renderer.root.findDescendantById("entry-reply")).toBe(entry);
    await act(async () => {
      controller.receive({ ...event, message_id: "next", operation: "append", text: "Next reply" });
    });
    const frame = await finalFrame(setup);
    expect(frame).toContain("First reply"); expect(frame).toContain("Next reply");
    expect(frame.match(/VulnClaw/g)).toHaveLength(1);
    expect(setup.externalOutput.take()).toHaveLength(0);
  } finally { await act(async () => { await setup.dispose(); }); }
});

for (const backgroundColor of ["transparent", "#f1e7d0"]) {
  test(`REPL blends the live global background with a ${backgroundColor} terminal background`, async () => {
    const controller = new SessionController(); controller.connected = true;
    const chunks: Buffer[] = [];
    const stdout = new Writable({ write(chunk, _encoding, callback) { chunks.push(Buffer.from(chunk)); callback(); } }) as NodeJS.WriteStream;
    stdout.isTTY = true; stdout.columns = 80; stdout.rows = 24; stdout.getColorDepth = () => 24;
    const output = new NativeSession(stdout);
    const themes = {
      blue: { ...defaultThemeConfig.themes.blue, background: "#101112", transcript: "#234567" },
      orange: { ...defaultThemeConfig.themes.orange, background: "#111213", transcript: "#654321" },
    };
    controller.applyConfigDocument({ config: { terminal: { theme: "blue", themes } } });
    const setup = await testRender(<ReplShell controller={controller} />, {
      width: 80, height: 24, screenMode: "alternate-screen",
      externalOutputMode: "passthrough", backgroundColor, stdout, nativeSession: output,
      bufferedOutput: "stdout",
    });
    await setup.renderer.setupTerminal();
    syncReplBackground(controller, setup.renderer, output);
    const terminalColors = () => [...Buffer.concat(chunks).toString("utf8")
      .matchAll(/\x1b\]11;(#[0-9a-f]{6})\x07/g)].map((match) => match[1]);
    const checkBackground = async () => {
      await finalFrame(setup);
      await setup.renderer.idle();
      expect(terminalColors().at(-1)).toBe(palettes[controller.theme].background);
      const expected = RGBA.defaultBackground(themes[controller.theme as keyof typeof themes].background);
      for (const line of setup.captureSpans().lines) {
        for (const span of line.spans) expect(span.bg).toEqual(expected);
      }
    };
    const checkCompletion = async (selected: number) => {
      await checkBackground();
      const menu = setup.renderer.root.findDescendantById("completion-menu")!;
      expect(menu).toBeDefined();
      expect(menu.height).toBe(3);
      const lines = setup.captureCharFrame().split("\n");
      expect(lines[menu.y]).toContain(`Commands · ${selected + 1}/2`);
      for (const [index, name] of ["blue", "orange"].entries()) {
        const row = menu.y + index + 1;
        expect(lines[row].trimStart()).toStartWith(`${index === selected ? "›" : ""} /theme ${name}`.trimStart());
        const span = setup.captureSpans().lines[row].spans.find((span) => span.text.includes(`/theme ${name}`))!;
        expect(span.fg).toEqual(RGBA.fromHex(palettes[controller.theme][index === selected ? "action" : "muted"]));
      }
    };
    try {
      await checkBackground();
      await act(async () => { await setup.mockInput.typeText("draft 中文"); });
      await checkBackground();
      await act(async () => { controller.draft = "/theme "; controller.changed(); });
      await checkCompletion(0);
      await act(async () => { setup.mockInput.pressArrow("down"); });
      await checkCompletion(1);
      await act(async () => { controller.inputFocused = false; controller.changed(); });
      await checkCompletion(1);
      await act(async () => {
        controller.applyConfigDocument({ config: { terminal: { theme: "orange", themes } } });
      });
      await checkCompletion(1);
      await act(async () => {
        themes.orange.background = "#345678";
        controller.applyConfigDocument({ config: { terminal: { theme: "orange", themes } } });
      });
      await checkCompletion(1);
      await act(async () => {
        controller.overlay = { kind: "data", title: "Background fixture", data: "Dialog content" };
        controller.changed();
      });
      expect(await finalFrame(setup)).toContain("Dialog content");
      const pane = setup.renderer.root.findDescendantById("data-pane")!;
      let end = 0;
      const corner = setup.captureSpans().lines[pane.y].spans.find((span) => {
        end += span.width; return pane.x < end;
      })!;
      expect(corner.bg).toEqual(RGBA.fromHex(palettes[controller.theme].panel));
      expect(terminalColors().at(-1)).toBe(themes.orange.background);
      await act(async () => { setup.resize(60, 32); });
      expect(await finalFrame(setup)).toContain("Dialog content");
      await act(async () => { controller.dismiss(); });
      await checkCompletion(1);
      expect(controller.draft).toBe("/theme ");
      for (const [width, height] of [[100, 40], [60, 16]]) {
        await act(async () => { setup.resize(width, height); });
        await checkCompletion(1);
      }
      await act(async () => {
        controller.draft = "draft 中文"; controller.inputFocused = true; controller.changed();
      });
      await checkBackground();
      await act(async () => { await setup.mockInput.typeText(" edited"); });
      await checkBackground();
      expect(controller.draft).toBe("draft 中文 edited");
      expect(terminalColors()).toEqual(["#101112", "#111213", "#345678"]);
    } finally {
      await act(async () => { await setup.dispose(); });
      configureThemes(defaultThemeConfig);
    }
    const bytes = Buffer.concat(chunks).toString("utf8");
    expect(bytes).toContain("\x1b]111\x07");
    expect(bytes.indexOf("\x1b]111\x07")).toBeLessThan(bytes.lastIndexOf("\x1b[?1049l"));
  });
}

for (const kind of ["data", "approval"] as const) {
  test(`REPL resizes an open ${kind} dialog and restores the input afterward`, async () => {
    const controller = new SessionController(); controller.connected = true; controller.draft = "saved draft";
    const setup = await testRender(<ReplShell controller={controller} />, {
      width: 80, height: 24, screenMode: "alternate-screen",
      externalOutputMode: "passthrough",
    });
    try {
      await finalFrame(setup);
      await act(async () => {
        if (kind === "data") {
          controller.overlay = { kind, title: "Resize fixture", data: "Dialog content" };
          controller.changed();
        } else controller.receive({ protocol_version: 2, type: "approval_required", task_id: "fixture",
          request_hash: "a".repeat(64), question: "synthetic approval" });
      });
      for (const [width, height] of [[80, 40], [60, 16], [100, 48]]) {
        await act(async () => { setup.resize(width, height); });
        const frame = await finalFrame(setup);
        expect(setup.renderer.height).toBe(height);
        const pane = setup.renderer.root.findDescendantById(kind === "data" ? "data-pane" : "approval-pane")!;
        expect(pane.y).toBeGreaterThanOrEqual(0);
        expect(pane.y + pane.height).toBeLessThanOrEqual(setup.renderer.height);
        expect(frame).toContain(kind === "data" ? "Esc close" : "[N/Esc] Deny");
      }
      await act(async () => {
        if (kind === "data") controller.dismiss();
        else controller.receive({ protocol_version: 2, type: "approval_closed", task_id: "fixture",
          request_hash: "a".repeat(64), status: "denied" });
      });
      expect(await finalFrame(setup)).toContain("saved draft");
      expect(setup.renderer.height).toBe(48);
      await act(async () => { setup.resize(100, 24); });
      await finalFrame(setup);
      expect(setup.renderer.height).toBe(24);
    } finally { await act(async () => { await setup.dispose(); }); }
  });
}

test("REPL keeps multiline input, completion, permission and notice on separate rows", async () => {
  const controller = new SessionController(); controller.connected = true;
  controller.draft = "one\ntwo\nthree\nfour\nfive";
  controller.commands = { alpha: "One", beta: "Two", gamma: "Three", delta: "Four", epsilon: "Five" };
  const setup = await testRender(<ReplShell controller={controller} />, {
    width: 80, height: 24, screenMode: "alternate-screen",
    externalOutputMode: "passthrough",
  });
  const checkRows = async () => {
    const frame = await finalFrame(setup), lines = frame.split("\n");
    const input = setup.renderer.root.findDescendantById("composer-input")!;
    const permission = setup.renderer.root.findDescendantById("permission-label")!;
    const notice = setup.renderer.root.findDescendantById("repl-notice")!;
    expect(input.y + input.height).toBe(permission.y);
    expect(permission.y + permission.height).toBe(notice.y);
    expect(notice.y + notice.height).toBe(setup.renderer.height);
    expect(lines[permission.y]).toContain("Ask");
    expect(lines[notice.y]).toContain("Ready");
    return lines;
  };
  try {
    for (const height of [24, 16, 40]) {
      await act(async () => { setup.resize(80, height); });
      expect((await checkRows()).join("\n")).toContain("five");
    }
    await act(async () => { controller.draft = "/"; controller.changed(); });
    for (const height of [16, 12, 24]) {
      await act(async () => { setup.resize(80, height); });
      const lines = await checkRows();
      const menu = setup.renderer.root.findDescendantById("completion-menu")!;
      const input = setup.renderer.root.findDescendantById("composer-input")!;
      expect(menu.y).toBeGreaterThanOrEqual(0);
      expect(menu.y + menu.height).toBe(input.y);
      expect(lines.join("\n")).toContain("/epsilon");
      expect(lines[input.y + 1]).toContain("> /");
    }
  } finally { await act(async () => { await setup.dispose(); }); }
});
