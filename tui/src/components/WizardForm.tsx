import { useState } from "react";
import { record, type SessionController } from "../session/controller";
import { FormPanel, type FormRow } from "./FormPanel";
import { ConfigForm } from "./ConfigForm";

export function WizardForm({ controller, document, active }: {
  controller: SessionController; document: Record<string, unknown>; active: boolean;
}) {
  const [current, setCurrent] = useState(document);
  const initial = record(document.status);
  const [values, setValues] = useState<Record<string, unknown>>({
    language: initial.language ?? "auto", chrome: initial.chrome_path ?? "",
    port: initial.debug_port ?? 9222, profile: "", jar: initial.burp_jar ?? "",
    devtools: initial.chrome_devtools_url ?? "http://127.0.0.1:9222",
    burp_url: initial.burp_sse_url ?? "http://127.0.0.1:9876",
  });
  const [config, setConfig] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  async function refresh() {
    setCurrent(await controller.control("setup.status"));
  }
  async function run(operation: string, args: Record<string, unknown> = {}) {
    setBusy(true);
    try {
      const result = await controller.control(operation, args);
      if (result.jar) setValues((values) => ({ ...values, jar: result.jar }));
      controller.notify(String(result.message ?? "Done"));
      await refresh();
    } catch (error) { controller.notify(String(error)); }
    finally { setBusy(false); }
  }
  if (config) return <ConfigForm controller={controller} document={config} active={active}
    onDone={() => { setConfig(null); void refresh().catch((error: unknown) => controller.notify(String(error))); }} />;
  const status = record(current.status);
  const plan = record(current.plan);
  const rows: FormRow[] = [
    { key: "ready", label: `Ready: ${Array.isArray(plan.ready) ? plan.ready.join(", ") : ""}`, action: () => {} },
    { key: "todo", label: `Still needed: ${Array.isArray(plan.todo) ? plan.todo.join(", ") : ""}`, action: () => {} },
    { key: "config", label: "1 · Configure LLM and other settings", action: () => {
      setBusy(true);
      void controller.control("config.document").then(setConfig).catch((error: unknown) => controller.notify(String(error))).finally(() => setBusy(false));
    } },
    { key: "language", label: "2 · Display language", value: values.language, choices: ["en", "zh", "auto"] },
    { key: "language-save", label: "Apply language", action: () => { void run("language.set", { language: values.language }); } },
    { key: "chrome", label: "3 · Chrome path", value: values.chrome },
    { key: "port", label: "Debug port", value: values.port, type: "number" },
    { key: "profile", label: "Profile directory (empty uses existing default)", value: values.profile },
    { key: "chrome-start", label: `Launch Chrome (${status.chrome_debug_live ? "already running" : "not detected"})`, action: () => {
      void run("setup.chrome", { path: values.chrome, port: values.port, profile: values.profile || undefined });
    } },
    { key: "devtools", label: "4 · Chrome DevTools browser URL", value: values.devtools },
    { key: "devtools-save", label: "Enable Chrome DevTools MCP", action: () => { void run("setup.devtools", { url: values.devtools }); } },
    { key: "java", label: `5 · Java: ${status.java ?? "Install Temurin JDK 17 via winget, or show installation URL"}`, action: () => { void run("setup.java"); } },
    { key: "build", label: "Build Burp MCP (clone PortSwigger repository and run Gradle)", action: () => { void run("setup.build_burp"); } },
    { key: "jar", label: "6 · Burp MCP JAR", value: values.jar },
    { key: "burp_url", label: "Burp SSE URL", value: values.burp_url },
    { key: "burp-save", label: "Enable Burp MCP; then load the JAR in Burp → Extensions → Add → Java", action: () => { void run("setup.burp", { jar: values.jar, url: values.burp_url }); } },
    { key: "reload", label: "7 · Reload MCP services", action: () => { void run("setup.reload"); } },
    { key: "refresh", label: "Re-scan setup status", action: () => { void run("setup.status"); } },
    { key: "finish", label: "Finish", action: () => controller.dismiss() },
  ];
  return <FormPanel title="Setup wizard" rows={rows} theme={controller.theme} active={active} busy={busy}
    onChange={(key, value) => setValues((values) => ({ ...values, [key]: value }))}
    onSave={() => controller.dismiss()} onCancel={() => controller.dismiss()} />;
}
