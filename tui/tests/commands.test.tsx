import { expect, test } from "bun:test";
import { act } from "react";
import { testRender } from "@opentui/react/test-utils";
import { SessionController } from "../src/session/controller";
import { completionDescription, completions } from "../src/session/completions";
import { helpText, type Command } from "../src/session/commands";
import { currentLanguage, setLanguage } from "../src/i18n";
import { TuiShell } from "../src/shells/TuiShell";
import { ReplShell } from "../src/shells/ReplShell";
import { WorkbenchState } from "../src/workbench/layout";
import type { ClientMessage } from "../src/transport/protocol.generated";
import { finalFrame } from "./render-helpers";

const backend: Command[] = [
  { name: "report", category: "Tasks", description: "Export existing results; --kind security | replay; --output PATH", syntax: "/report [--kind security|replay] [--output PATH]", examples: ["/report --kind replay"] },
  { name: "scope", category: "Session", description: "Backend scope", syntax: "/scope [--allowed-ports PORTS]", examples: ["/scope"] },
  { name: "config", category: "Configuration", description: "Wrong description", syntax: "/wrong", examples: [] },
];
function connected() {
  const controller = new SessionController(), requests: ClientMessage[] = [];
  controller.attach({ send(request) {
    requests.push(request);
    if (request.type === "control" && request.payload?.operation === "session.help") controller.receive({ protocol_version: 2,
      type: "control_result", request_id: request.request_id, operation: "session.help", result: { commands: backend } });
  }, async close() {} });
  controller.connected = true;
  controller.commands = Object.fromEntries(backend.map((command) => [command.name, command.description]));
  return { controller, requests };
}

for (const mode of ["tui", "repl"] as const) test(`${mode}: report paths and help are transcript output without an overlay`, async () => {
  const { controller, requests } = connected();
  const setup = await testRender(mode === "tui" ? <TuiShell controller={controller} workbench={new WorkbenchState()} /> : <ReplShell controller={controller} />,
    { width: 100, height: 32, kittyKeyboard: true });
  try {
    await act(async () => {
      await controller.submit("/report");
      controller.receive({ protocol_version: 2, type: "control_result", request_id: requests.at(-1)!.request_id, operation: "report.export",
        result: { report: { kind: "security", path: "/reports/security.md", findings_json: "/reports/findings.json", findings_sarif: "/reports/findings.sarif" }, message: "Report saved: /reports/security.md" } });
    });
    const frame = await finalFrame(setup);
    expect(controller.overlay).toBeNull();
    expect(frame).toContain("/reports/security.md"); expect(frame).toContain("findings.sarif");
    expect(frame).not.toContain('"findings_json"');
    expect([...controller.cards.values()].filter((card) => card.text.includes("/reports/security.md"))).toHaveLength(1);
    await act(async () => { await controller.submit("/help report"); });
    expect(controller.overlay).toBeNull();
    expect([...controller.cards.values()].at(-1)?.text).toContain("/report --kind replay");
    expect(requests.filter((request) => request.type === "submit_input")).toHaveLength(1);
  } finally { await act(async () => { await setup.dispose(); }); }
});

test("report errors never create success receipts", async () => {
  const { controller, requests } = connected();
  await controller.submit("/report");
  controller.receive({ protocol_version: 2, type: "error", request_id: requests.at(-1)!.request_id, code: "invalid_control", message: "Cannot write report" });
  expect([...controller.cards.values()].map((card) => [card.kind, card.text])).toEqual([["error", "Cannot write report"]]);
  expect(controller.overlay).toBeNull();
});

test("help and completion share metadata, local overrides, aliases and both languages", async () => {
  const language = currentLanguage();
  const { controller, requests } = connected();
  try {
    for (const lang of ["zh", "en"]) {
      setLanguage(lang);
      await controller.submit("/help");
      const help = [...controller.cards.values()].at(-1)!.text;
      controller.draft = "/";
      const choices = completions(controller);
      for (const command of controller.commandList()) {
        expect(choices).toContain(`/${command.name}`);
        expect(help).toContain(command.syntax);
        expect(help).toContain(completionDescription(controller, `/${command.name}`));
      }
      expect(choices).not.toContain("/tools");
      expect(help).not.toContain("Wrong description"); expect(help).not.toContain("/wrong");
      expect(help).toContain(lang === "zh" ? "配置" : "Configuration");
      expect(helpText(controller.commandList(), "cfg")).toContain("/config");
      await controller.submit("/help unknown");
      const unknown = [...controller.cards.values()].at(-1)!.text;
      expect(unknown).toContain(lang === "zh" ? "未知帮助主题" : "Unknown help topic");
      expect(unknown).toContain("/scope");
    }
    expect(requests.every((request) => request.type !== "submit_input")).toBe(true);
  } finally { setLanguage(language); }
});

test("cancelled initial scope preview cannot reopen the form", async () => {
  const { controller, requests } = connected();
  const pending = controller.submit("/scope reset");
  expect(controller.overlay?.kind).toBe("scope");
  const request = requests.at(-1)!;
  controller.dismiss();
  controller.receive({ protocol_version: 2, type: "control_result", request_id: request.request_id, operation: "session.scope.preview", result: { scope: {}, errors: {} } });
  await pending;
  expect(controller.overlay).toBeNull();
  expect(request.type === "control" && request.payload?.arguments).toEqual({ text: "/scope reset" });
});
