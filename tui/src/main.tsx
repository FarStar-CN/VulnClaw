import { createCliRenderer, destroyTreeSitterClient, NativeSession } from "@opentui/core";
import { createRoot, flushSync } from "@opentui/react";
import { parseArgs } from "node:util";
import { join } from "node:path";
import { SessionController } from "./session/controller";
import { PythonBackend } from "./transport/backend";
import { ReplShell } from "./shells/ReplShell";
import { TuiShell } from "./shells/TuiShell";
import { WorkbenchState } from "./workbench/layout";
import { clientDirectory, loadStartupAppearance, loadPreferences, restoreClientSession, saveClientSession, writeJson } from "./platform/preferences";
import { syncReplBackground } from "./platform/repl-background";
import type { Bootstrap } from "./transport/protocol.generated";
import type { ThemeConfig } from "./theme/palette";
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
let renderer: Awaited<ReturnType<typeof createCliRenderer>> | null = null;
let rendererCreation: Promise<void> | null = null;
let root: ReturnType<typeof createRoot> | null = null;
let closing: Promise<void> | null = null;
let backend: PythonBackend | null = null;
let migration: ThemeConfig | null = null;
const close = (): Promise<void> => closing ??= (async () => {
  try { await backend?.close(); await writes; await saveClientSession(controller, directory); }
  finally {
    await rendererCreation?.catch(() => {});
    root?.unmount(); await destroyTreeSitterClient(); renderer?.destroy(); await renderer?.closed;
  }
})();
controller.onExit = () => { void close().catch((error: unknown) => { process.stderr.write(String(error) + "\n"); process.exitCode = 1; }); };
function themeError(error: unknown): void {
  const message = t("Theme configuration load failed: {error}", { error: String(error) });
  controller.receive({ protocol_version: 2, type: "log", task_id: "theme", message }); controller.notify(message);
}
function migrateTheme(): void {
  if (!controller.connected || !migration || closing) return;
  const terminal = migration; migration = null;
  void controller.control("config.update", { changes: { terminal } }).catch(themeError);
}
try {
  const bootstrap = JSON.parse(process.env.VULNCLAW_TERMINAL_BOOTSTRAP ?? "{}") as Bootstrap;
  backend = new PythonBackend((event) => {
    controller.receive(event);
    if (event.type === "ready") migrateTheme();
  }, controller.disconnect, { onDiagnostic: (message) => controller.receive({
    protocol_version: 2, type: "log", task_id: "backend", message,
  }) });
  controller.attach(backend, bootstrap);
  for (const signal of ["SIGINT", "SIGTERM"] as const) process.on(signal, () => controller.onExit());
  const appearance = loadStartupAppearance(undefined, directory).then(({ terminal, language, migration: pending }) => {
    controller.applyConfigDocument({ config: { terminal, session: { language } } });
    migration = pending; migrateTheme();
  }).catch(themeError);
  const preferences = (async () => {
    const saved = await loadPreferences(directory);
    workbench.layout = saved.layout;
    await restoreClientSession(controller, directory);
  })().catch((error: unknown) => controller.notify(t("Preferences load failed: {error}", { error: String(error) })));
  const output = new NativeSession(process.stdout);
  rendererCreation = appearance.then(() => createCliRenderer({
    nativeSession: output,
    screenMode: "alternate-screen",
    exitOnCtrlC: false, exitSignals: [], consoleMode: "disabled", useMouse: true,
  })).then((created) => { renderer = created; }).catch(async (error: unknown) => {
    await output.close(); throw error;
  });
  await Promise.all([appearance, preferences, rendererCreation]);
  if (!closing) {
    if (values.mode === "repl") syncReplBackground(controller, renderer!, output);
    root = createRoot(renderer!);
    const mounted = root;
    flushSync(() => mounted.render(values.mode === "repl" ? <ReplShell controller={controller} /> : <TuiShell controller={controller} workbench={workbench} />));
  }
} catch (error) {
  await close();
  process.stderr.write(String(error) + "\n"); process.exitCode = 1;
}
