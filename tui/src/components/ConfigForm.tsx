import { useState } from "react";
import { type SessionController, record } from "../session/controller";
import { FormPanel, type FormRow } from "./FormPanel";

function fields(value: Record<string, unknown>, secrets: Set<string>, prefix = ""): FormRow[] {
  return Object.entries(value).flatMap(([key, item]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    if (item && typeof item === "object" && !Array.isArray(item)) return fields(record(item), secrets, path);
    const secret = secrets.has(path);
    return [{ key: path, label: path, value: item, type: secret ? "secret" : Array.isArray(item) || item === null ? "json" : undefined } satisfies FormRow];
  });
}

export function ConfigForm({ controller, document, active, onDone }: {
  controller: SessionController; document: Record<string, unknown>; active: boolean; onDone?: () => void;
}) {
  const [config, setConfig] = useState(record(document.config));
  const [changes, setChanges] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const [models, setModels] = useState<unknown[]>([]);
  const providers = (Array.isArray(document.providers) ? document.providers : []).map(record).map((provider) => provider.provider);
  const done = onDone ?? (() => controller.dismiss());
  const secrets = new Set(Array.isArray(document.secret_paths) ? document.secret_paths.map(String) : []);
  const rows: FormRow[] = fields(config, secrets).map((field) => ({ ...field,
    value: Object.hasOwn(changes, field.key) ? changes[field.key] : field.value,
    choices: field.key === "llm.model" && models.length ? models : field.key === "llm.provider" ? providers : undefined }));
  async function save() {
    if (!Object.keys(changes).length) { done(); return; }
    setBusy(true);
    try { await controller.control("config.update", { changes }); done(); }
    catch (error) { controller.notify(String(error)); }
    finally { setBusy(false); }
  }
  async function fetchModels() {
    setBusy(true);
    try {
      const llm = record(config.llm);
      const result = await controller.control("config.models", {
        base_url: changes["llm.base_url"] ?? llm.base_url,
        api_key: changes["llm.api_key"] ?? "",
      });
      setModels(Array.isArray(result.models) ? result.models : []);
    } catch (error) { controller.notify(String(error)); }
    finally { setBusy(false); }
  }
  rows.unshift({ key: "fetch-models", label: "Fetch provider models", action: () => { void fetchModels(); } });
  rows.push({ key: "mcp-add", label: "Add MCP server (JSON with name and transport)", value: { name: "custom", enabled: false, transport: { type: "stdio", command: "", args: [] } }, type: "json" });
  for (const name of Object.keys(record(record(config.mcp).servers)).filter((name) =>
    !Array.isArray(document.builtin_servers) || !document.builtin_servers.includes(name))) rows.push({
    key: `delete-${name}`, label: `Delete MCP server ${name}`, action: () => {
      const next = structuredClone(config);
      const servers = record(record(next.mcp).servers);
      delete servers[name]; setConfig(next);
      setChanges((changes) => ({ ...changes, "mcp.servers": servers }));
    },
  });
  rows.push({ key: "save", label: "Save configuration (Ctrl+S)", action: () => { void save(); } },
    { key: "cancel", label: "Discard changes", action: done });
  return <FormPanel title="Configuration" theme={controller.theme} active={active} busy={busy} rows={rows}
    onChange={(key, value) => {
      if (key === "mcp-add") {
        const server = record(value);
        if (typeof server.name !== "string" || !server.name.trim()) { controller.notify("A server name is required"); return; }
        setChanges((changes) => ({ ...changes, [`mcp.servers.${server.name}`]: server }));
        const next = structuredClone(config);
        record(record(next.mcp).servers)[server.name] = server; setConfig(next);
      } else if (key === "llm.provider") {
        setModels([]);
        setBusy(true);
        void controller.control("config.preset", { provider: value }).then((preset) => {
          setChanges((changes) => ({ ...changes, ...Object.fromEntries(Object.entries(preset).map(([name, value]) => [`llm.${name}`, value])) }));
        }).catch((error: unknown) => controller.notify(String(error))).finally(() => setBusy(false));
      } else {
        if (["llm.base_url", "llm.api_key", "llm.api_keys"].includes(key)) setModels([]);
        setChanges((changes) => ({ ...changes, [key]: value }));
      }
    }} onSave={() => { void save(); }} onCancel={done} />;
}
