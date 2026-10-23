import { expect, test } from "bun:test";
import { act } from "react";
import { RGBA, type ScrollBoxRenderable } from "@opentui/core";
import { testRender } from "@opentui/react/test-utils";
import { SessionController } from "../src/session/controller";
import type { ClientMessage, RuntimeInfo, StateSnapshot } from "../src/transport/protocol.generated";
import { TuiShell } from "../src/shells/TuiShell";
import { WorkbenchState } from "../src/workbench/layout";
import { palettes } from "../src/theme/palette";
import { finalFrame } from "./render-helpers";

const state = (): StateSnapshot => ({ permission_mode: "ask", target: "", phase: "idle", task_constraints: {},
  task: { active: false, task_id: null }, last_run: null, findings: [], evidence: [], constraint_violations: [] });
const runtime: RuntimeInfo = { config_ready: true, provider: "fixture", model: "fixture", mcp_started: 4,
  mcp_ready: 2, skills_loaded: 3, skills: [], mcp_servers: [
    { name: "burp", ready: false }, { name: "chrome-devtools", ready: false },
    { name: "fetch", ready: true }, { name: "memory", ready: true },
  ] };
function session() {
  const controller = new SessionController();
  const requests: ClientMessage[] = [];
  controller.attach({ send(request) { requests.push(request); }, async close() {} });
  controller.connected = true; controller.state = state(); controller.runtime = structuredClone(runtime); controller.notice = "";
  return { controller, requests };
}

test("MCP disclosure targets only its label and chevron, uses green/red names, and follows backend state", async () => {
  const { controller, requests } = session(); const workbench = new WorkbenchState();
  controller.draft = "retained draft";
  const setup = await testRender(<TuiShell controller={controller} workbench={workbench} />,
    { width: 120, height: 36, kittyKeyboard: true });
  const node = (id: string) => setup.renderer.root.findDescendantById(id)!;
  async function click(id: string, offset = 0) {
    const target = node(id);
    await act(async () => { await setup.mockMouse.click(target.x + offset, target.y); });
    return finalFrame(setup);
  }
  try {
    expect(await finalFrame(setup)).toMatch(/〉MCP\s+2\/4/);
    expect(node("capabilities-pane").height).toBe(4);
    await click("mcp-count");
    const toggle = node("mcp-toggle");
    const toggleWidth = toggle.width;
    await act(async () => { await setup.mockMouse.click(toggle.x + toggle.width + 2, toggle.y); });
    await finalFrame(setup);
    expect(setup.renderer.root.findDescendantById("capabilities-scroll")).toBeUndefined();
    expect(await click("mcp-toggle", 1)).toMatch(/﹀MCP\s+2\/4/);
    expect(node("mcp-toggle").width).toBe(toggleWidth);
    expect(node("capabilities-pane").height).toBe(8);
    expect(controller.draft).toBe("retained draft"); expect(workbench.gesture).toBeNull();
    for (const theme of ["blue", "orange"] as const) {
      await act(async () => { controller.setTheme(theme); });
      const frame = await finalFrame(setup);
      for (const server of runtime.mcp_servers!) {
        expect(frame).toContain(`● ${server.name}`);
        const row = node(`mcp-service-${server.name}`);
        const spans = setup.captureSpans().lines[row.y].spans;
        expect(spans.find((span) => span.text.includes(server.name))?.fg)
          .toEqual(RGBA.fromHex(server.ready ? palettes[theme].success : palettes[theme].error));
      }
      expect(frame).not.toContain("Not ready"); expect(frame).not.toContain("(local)");
    }
    const layout = JSON.stringify(workbench.layout);
    await act(async () => { controller.receive({ protocol_version: 2, type: "state", state: { ...state(), runtime: {
      ...runtime, mcp_ready: 3, mcp_servers: runtime.mcp_servers!.map((server) => ({ ...server, ready: server.name === "burp" || server.ready })),
    } } }); });
    expect(await finalFrame(setup)).toMatch(/MCP\s+3\/4/);
    const burp = node("mcp-service-burp");
    expect(setup.captureSpans().lines[burp.y].spans.find((span) => span.text.includes("burp"))?.fg)
      .toEqual(RGBA.fromHex(palettes.orange.success));
    expect(JSON.stringify(workbench.layout)).toBe(layout);
    await click("mcp-toggle");
    expect(node("capabilities-pane").height).toBe(4);
    expect(controller.draft).toBe("retained draft");
    await act(async () => { controller.draft = ""; controller.changed(); setup.mockInput.pressEnter(); });
    await finalFrame(setup); expect(node("capabilities-pane").height).toBe(8);
    await click("collapse-capabilities");
    expect(node("capabilities-pane").height).toBe(2);
    await click("collapse-capabilities");
    expect(await finalFrame(setup)).toContain("● burp");
    await act(async () => { setup.mockInput.pressEnter(); });
    await finalFrame(setup); expect(node("capabilities-pane").height).toBe(4);
    expect(requests).toHaveLength(1);
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("MCP list scrolls with wheel and keyboard while both counters remain visible after resizing", async () => {
  const { controller } = session(); const workbench = new WorkbenchState();
  controller.runtime = { ...runtime, mcp_ready: 0, mcp_servers: Array.from({ length: 12 }, (_, index) => ({
    name: index ? `service-${String(index).padStart(2, "0")}` : "特别长的服务名称应当显示省略号并保持单行", ready: false,
  })) };
  const setup = await testRender(<TuiShell controller={controller} workbench={workbench} />,
    { width: 120, height: 36, kittyKeyboard: true });
  try {
    await finalFrame(setup);
    const toggle = setup.renderer.root.findDescendantById("mcp-toggle")!;
    await act(async () => { await setup.mockMouse.click(toggle.x, toggle.y); });
    let frame = await finalFrame(setup);
    expect(frame).toContain("…");
    const list = setup.renderer.root.findDescendantById("capabilities-scroll") as ScrollBoxRenderable;
    expect(list.viewport.height).toBe(4);
    await act(async () => { await setup.mockMouse.scroll(list.x + 3, list.y + 1, "down"); });
    await finalFrame(setup); expect(list.scrollTop).toBeGreaterThan(0);
    list.scrollTo(0);
    await act(async () => { setup.mockInput.pressArrow("down"); });
    await finalFrame(setup); expect(list.scrollTop).toBeGreaterThan(0);
    await act(async () => { setup.mockInput.pressKey("\x1b[6~"); setup.mockInput.pressKey("\x1b[6~"); });
    await finalFrame(setup); expect(list.scrollTop).toBeGreaterThan(1);
    await act(async () => { setup.resize(64, 18); });
    frame = await finalFrame(setup);
    expect(frame).toMatch(/MCP\s+0\/12/); expect(frame).toMatch(/Skills loaded\s+3/);
    expect(list.viewport.height).toBeGreaterThan(0); expect(list.viewport.height).toBeLessThanOrEqual(4);
    const pane = setup.renderer.root.findDescendantById("capabilities-pane")!;
    const skills = setup.renderer.root.findDescendantById("skills-count")!;
    expect(skills.y).toBeLessThan(pane.y + pane.height);
    expect(frame.split("\n")[pane.y + pane.height]).toMatch(/^└─+┴/);
    expect(frame).not.toContain("Not ready");
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("MCP disclosure explains an empty enabled list", async () => {
  const { controller } = session(); controller.runtime = { ...runtime, mcp_ready: 0, mcp_servers: [] };
  const setup = await testRender(<TuiShell controller={controller} workbench={new WorkbenchState()} />,
    { width: 120, height: 36 });
  try {
    expect(await finalFrame(setup)).toMatch(/MCP\s+0\/0/);
    const toggle = setup.renderer.root.findDescendantById("mcp-toggle")!;
    await act(async () => { await setup.mockMouse.click(toggle.x, toggle.y); });
    expect(await finalFrame(setup)).toContain("No MCP services enabled");
  } finally { await act(async () => { await setup.dispose(); }); }
});
