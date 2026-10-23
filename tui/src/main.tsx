import { createCliRenderer, destroyTreeSitterClient } from "@opentui/core";
import { createRoot, flushSync } from "@opentui/react";
import { parseArgs } from "node:util";
import { join } from "node:path";
import { SessionController } from "./session/controller";
import { PythonBackend } from "./transport/backend";
import { ReplShell } from "./shells/ReplShell";
import { TuiShell } from "./shells/TuiShell";
import { WorkbenchState } from "./workbench/layout";
import { clientDirectory, legacyThemeConfig, loadPreferences, restoreClientSession, saveClientSession, writeJson } from "./platform/preferences";
import type { Bootstrap } from "./transport/protocol.generated";
import { t } from "./i18n";

const { values } = parseArgs({ args: Bun.argv.slice(2), options: {
  mode: { type: "string", default: "tui" },
}, strict: true });
if (values.mode !== "tui" && values.mode !== "repl") throw new Error("--mode must be tui or repl");
if (!process.stdin.isTTY || !process.stdout.isTTY) {
  process.stderr.write(t("An interactive terminal is required. Use headless commands for Agent/CI calls.") + "\n");
  process.exit(1);
}
const controller = new SessionController();
const directory = clientDirectory();
let writes = Promise.resolve();
const workbench = new WorkbenchState(() => {
  writes = writes.then(() => writeJson(join(directory, "layout.json"), { format_version: 2, layout: workbench.layout }))
    .catch((error: unknown) => controller.notify(t("Preferences save failed: {error}", { error: String(error) })));
});
try {
  const preferences = await loadPreferences(directory);
  workbench.layout = preferences.layout;
  await restoreClientSession(controller, directory);
} catch (error) { controller.notice = t("Preferences load failed: {error}", { error: String(error) }); }
const renderer = await createCliRenderer({
  screenMode: values.mode === "repl" ? "split-footer" : "alternate-screen",
  footerHeight: 12, exitOnCtrlC: false, exitSignals: [], consoleMode: "disabled", useMouse: true,
});
const root = createRoot(renderer);
let closing: Promise<void> | null = null;
let backend: PythonBackend | null = null;
const close = (): Promise<void> => closing ??= (async () => {
  try { await backend?.close(); await writes; await saveClientSession(controller, directory); }
  finally { root.unmount(); await destroyTreeSitterClient(); renderer.destroy(); await renderer.closed; }
})();
controller.onExit = () => { void close().catch((error: unknown) => { process.stderr.write(String(error) + "\n"); process.exitCode = 1; }); };
try {
  flushSync(() => root.render(values.mode === "repl" ? <ReplShell controller={controller} /> : <TuiShell controller={controller} workbench={workbench} />));
  backend = new PythonBackend((event) => {
    controller.receive(event);
    if (event.type === "ready") void (async () => {
      let document = await controller.control("config.document");
      if (document.terminal_configured !== true) {
        const legacy = await legacyThemeConfig(directory);
        if (legacy) document = await controller.control("config.update", { changes: { terminal: legacy } });
      }
      controller.applyConfigDocument(document);
    })().catch((error: unknown) => {
      const message = t("Theme configuration load failed: {error}", { error: String(error) });
      controller.receive({ protocol_version: 2, type: "log", task_id: "theme", message }); controller.notify(message);
    });
  }, controller.disconnect, { onDiagnostic: (message) => controller.receive({
    protocol_version: 2, type: "log", task_id: "backend", message,
  }) });
  const bootstrap = JSON.parse(process.env.VULNCLAW_TERMINAL_BOOTSTRAP ?? "{}") as Bootstrap;
  controller.attach(backend, bootstrap);
  for (const signal of ["SIGINT", "SIGTERM"] as const) process.on(signal, () => controller.onExit());
} catch (error) {
  await close();
  process.stderr.write(String(error) + "\n"); process.exitCode = 1;
}
