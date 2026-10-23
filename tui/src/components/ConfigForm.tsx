import { useEffect, useMemo, useRef, useState } from "react";
import Ajv from "ajv";
import type { ScrollBoxRenderable, TextareaRenderable } from "@opentui/core";
import { useKeyboard, useTerminalDimensions } from "@opentui/react";
import { type SessionController, record } from "../session/controller";
import { palettes, permissionInfo } from "../theme/palette";
import { LlmSettings } from "./LlmSettings";
import { visibleText } from "./content";
import {
  configChanges, configFields, connectionFields, parseField, resolveSchema,
  editableConfig, sameValue, settingCategories, validationConfig, valueAt, withValue, type SettingCategory,
} from "./config-fields";

type Row = {
  key: string; label: string; value?: unknown; schema?: Record<string, unknown>;
  secret?: boolean; choices?: unknown[]; help?: string; section?: string;
  set?: (value: unknown) => void; action?: () => void; remove?: () => void;
};
type ServerDraft = { key: string | null; value: Record<string, unknown> };
type Collection = { row: Row; value: unknown; dictionary: boolean };

export function ConfigForm({ controller, document, active, onDone }: {
  controller: SessionController; document: Record<string, unknown>; active: boolean; onDone?: () => void;
}) {
  const [current, setCurrent] = useState(document);
  const [baseline, setBaseline] = useState(() => editableConfig(document));
  const [draft, setDraft] = useState(() => editableConfig(document));
  const draftRef = useRef(draft);
  const [category, setCategory] = useState<SettingCategory>("general");
  const categoryRef = useRef(category);
  const [pane, setPane] = useState<"categories" | "fields">("fields");
  const paneRef = useRef(pane);
  const [focus, setFocus] = useState(0);
  const focusRef = useRef(0);
  const [editing, setEditing] = useState<Row | null>(null);
  const [choice, setChoice] = useState<{ row: Row; index: number } | null>(null);
  const choiceRef = useRef(choice);
  const editingRef = useRef(editing);
  const [buffer, setBuffer] = useState("");
  const [collection, setCollection] = useState<Collection | null>(null);
  const [server, setServer] = useState<ServerDraft | null>(null);
  const serverRef = useRef(server);
  const [connection, setConnection] = useState(false);
  const [modelAdvanced, setModelAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const editor = useRef<TextareaRenderable>(null);
  const scroll = useRef<ScrollBoxRenderable>(null);
  const p = palettes[controller.theme];
  const { width } = useTerminalDimensions();
  const categoryWidth = width < 76 ? 15 : 19;
  const labelWidth = Math.max(12, Math.min(27, Math.floor((Math.min(width, 104) - categoryWidth - 6) * 0.45)));
  const done = onDone ?? (() => controller.dismiss());
  const rootSchema = record(current.schema);
  const validate = useMemo(() => Object.keys(record(current.schema)).length
    ? new Ajv({ allErrors: true, strict: false, validateFormats: false }).compile(record(current.schema)) : null, [current.schema]);
  const changes = configChanges(baseline, draft);
  const fields = configFields(current, draft);
  const builtins = Array.isArray(current.builtin_servers) ? current.builtin_servers.map(String) : [];
  function updateDraft(value: Record<string, unknown>) { draftRef.current = value; setDraft(value); setFeedback(""); }
  function updateServer(value: ServerDraft | null) { serverRef.current = value; setServer(value); setFeedback(""); }
  function select(index: number) { focusRef.current = index; setFocus(index); }
  function focusPane(next: "categories" | "fields") { paneRef.current = next; setPane(next); }
  function choose(next: typeof choice) { choiceRef.current = next; setChoice(next); }
  function change(path: string, value: unknown) { updateDraft(withValue(draftRef.current, path, value)); }
  function startEdit(row: Row) {
    editingRef.current = row; setEditing(row);
    setBuffer(row.secret ? "" : String(row.value ?? "")); setError("");
  }
  function commitEdit(): boolean {
    const row = editingRef.current;
    if (!row) return true;
    try {
      const text = editor.current?.plainText ?? buffer;
      if (!row.secret || text) row.set?.(parseField(text, row.schema ?? {}, row.value));
      editingRef.current = null; setEditing(null); setError(""); return true;
    } catch (error) { setError(row.label + ": " + String(error instanceof Error ? error.message : error)); return false; }
  }
  function applyServer(): boolean {
    const pending = serverRef.current;
    if (!pending) return true;
    const name = String(pending.value.name ?? "").trim();
    if (!name) { setError("Server name is required."); return false; }
    const servers = { ...record(record(draftRef.current.mcp).servers) };
    if (pending.key !== name && Object.hasOwn(servers, name)) { setError("A server named " + name + " already exists."); return false; }
    const transport = record(pending.value.transport);
    if (transport.type === "stdio" ? !String(transport.command ?? "").trim() : !String(transport.url ?? "").trim()) {
      setError(transport.type === "stdio" ? "A stdio command is required." : "A server URL is required."); return false;
    }
    if (pending.key && pending.key !== name) delete servers[pending.key];
    servers[name] = { ...pending.value, name };
    change("mcp.servers", servers); return true;
  }
  async function persist(value = draftRef.current) {
    if (validate && !validate(validationConfig(value, current))) {
      const failure = validate.errors?.[0];
      const path = failure?.instancePath.slice(1).replaceAll("/", ".") || "Configuration";
      throw new Error(path + ": " + (failure?.message ?? "Invalid value"));
    }
    const edits = configChanges(baseline, value);
    if (!Object.keys(edits).length) { setFeedback("No unsaved changes."); return; }
    const result = await controller.control("config.update", { changes: edits });
    const saved = Object.keys(record(result.config)).length ? editableConfig({ ...current, ...result }) : value;
    setCurrent((previous) => ({ ...previous, ...result }));
    setBaseline(saved); updateDraft(structuredClone(saved));
    updateServer(null); setCollection(null); setFeedback("Configuration saved."); setError("");
  }
  async function save() {
    const selected = choiceRef.current;
    if (selected) { selected.row.set?.(selected.row.choices![selected.index]); choose(null); }
    if (busy || !commitEdit() || !applyServer()) return;
    setBusy(true); setError("");
    try { await persist(); }
    catch (error) { setError(String(error instanceof Error ? error.message : error)); }
    finally { setBusy(false); }
  }
  function collectionRows(view: Collection): Row[] {
    function update(value: unknown) { view.row.set?.(value); setCollection({ ...view, value }); }
    const rows: Row[] = view.dictionary ? Object.entries(record(view.value)).flatMap(([key, value], index) => [
      { key: "name-" + index, label: "Name", value: key, set: (value) => {
        const name = String(value).trim();
        if (!name || name !== key && Object.hasOwn(record(view.value), name)) throw new Error("Use a unique, non-empty name.");
        if (name !== key && view.row.secret && record(view.value)[key] === "***") throw new Error("Replace the saved value before renaming this entry.");
        update(Object.fromEntries(Object.entries(record(view.value)).map(([old, item]) => [old === key ? name : old, item])));
      } },
      { key: "value-" + index, label: "Value", value, secret: view.row.secret, set: (value) => update({ ...record(view.value), [key]: value }),
        remove: () => update(Object.fromEntries(Object.entries(record(view.value)).filter(([name]) => name !== key))) },
    ]) : (Array.isArray(view.value) ? view.value : []).map((value, index, values) => ({
      key: "item-" + index, label: "Item " + (index + 1), value, secret: view.row.secret,
      schema: resolveSchema(view.row.schema?.items, rootSchema),
      set: (value) => update(values.map((item, i) => i === index ? value : item)),
      remove: () => { update(values.filter((_, i) => i !== index)); select(Math.max(0, index - 1)); },
    }));
    rows.push({ key: "add-item", label: "＋ Add item", action: () => {
      if (view.dictionary) {
        const values = record(view.value); let name = "KEY"; let suffix = 2;
        while (Object.hasOwn(values, name)) name = "KEY_" + suffix++;
        update({ ...values, [name]: "" }); select(Object.keys(values).length * 2);
      } else { const values = Array.isArray(view.value) ? view.value : []; update([...values, ""]); select(values.length); }
    } }, { key: "back", label: "← Back", action: () => { setCollection(null); select(0); } });
    return rows;
  }
  function serverRows(value: ServerDraft): Row[] {
    const model = resolveSchema({ $ref: "#/$defs/MCPServerConfig" }, rootSchema);
    function row(path: string, label: string, secret = false): Row {
      let schema = model;
      for (const part of path.split(".")) schema = resolveSchema(record(schema.properties)[part], rootSchema);
      return { key: path, label, value: valueAt(value.value, path), schema, secret,
        set: (next) => updateServer({ ...serverRef.current!, value: withValue(serverRef.current!.value, path, next) }) };
    }
    const transport = record(value.value.transport);
    return [
      { ...row("name", "Server name"), set: value.key ? undefined : row("name", "Server name").set },
      row("description", "Description"), row("enabled", "Enabled"),
      { ...row("priority", "Priority"), choices: [0, 1, 2] },
      { ...row("transport.type", "Connection"), choices: ["stdio", "sse", "streamable-http"] },
      ...(transport.type === "stdio" ? [row("transport.command", "Command"), row("transport.args", "Arguments")]
        : [row("transport.url", "Server URL")]),
      { ...row("transport.env", transport.type === "stdio" ? "Environment variables" : "Headers", true),
        schema: { type: "object" } },
      row("transport.startup_timeout", "Startup timeout (ms)"), row("transport.tool_timeout", "Tool timeout (ms)"),
      { key: "apply-server", label: "Apply to draft", action: () => { if (applyServer()) { updateServer(null); select(0); } } },
      ...(value.key && !builtins.includes(value.key) ? [{ key: "delete-server", label: "Delete service", action: () => {
        const servers = { ...record(record(draftRef.current.mcp).servers) }; delete servers[value.key!];
        change("mcp.servers", servers); updateServer(null); select(0);
      } }] : []),
      { key: "back", label: "← Back", action: () => { updateServer(null); select(0); } },
    ];
  }
  function categoryRows(): Row[] {
    if (category === "mcp") return [
      ...Object.entries(record(record(draft.mcp).servers)).map(([name, item]): Row => {
        const service = record(item);
        return { key: name, label: name, value: (service.enabled ? "Enabled" : "Disabled") + " · " + String(record(service.transport).type ?? ""),
          action: () => { updateServer({ key: name, value: structuredClone(service) }); select(0); } };
      }),
      { key: "add-server", label: "＋ Add MCP service", action: () => {
        updateServer({ key: null, value: { name: "", enabled: false, priority: 1, description: "",
          transport: { type: "stdio", command: "", args: [], env: {}, startup_timeout: 30000, tool_timeout: 300000 } } }); select(0);
      } },
    ];
    const rows: Row[] = fields.filter((field) => field.category === category &&
      (category !== "model" || modelAdvanced && !connectionFields.some(([key]) => field.key === "llm." + key)))
      .map((field) => ({ ...field, value: valueAt(draft, field.key), set: (value) => change(field.key, value),
        section: field.key === "subagent.enabled" ? "Subagent limits" : undefined }));
    if (category === "model") rows.unshift(
      { key: "connection", label: "Provider connection…", value: String(record(draft.llm).provider ?? "") + " / " + String(record(draft.llm).model ?? ""),
        action: () => setConnection(true), help: "Edit the same five provider fields as /config." },
      { key: "llm.api_key", label: "API key", value: record(draft.llm).api_key, secret: true,
        action: () => setConnection(true), set: (value) => change("llm.api_key", value),
        help: "Enter opens provider settings. A blank edit keeps the saved key; Clear value removes it." },
      { key: "model-advanced", label: "Advanced model options", value: modelAdvanced,
        set: (value) => { setModelAdvanced(value === true); } },
    );
    return rows;
  }
  const rows = collection ? collectionRows(collection) : server ? serverRows(server) : categoryRows();
  const row = rows[Math.min(focus, Math.max(0, rows.length - 1))];
  useEffect(() => {
    if (focusRef.current >= rows.length) select(Math.max(0, rows.length - 1));
    scroll.current?.scrollChildIntoView("settings-row-" + Math.min(focus, Math.max(0, rows.length - 1)));
  }, [focus, category, rows.length]);
  function switchCategory(next: SettingCategory) {
    if (busy || editingRef.current || choiceRef.current) return;
    if (serverRef.current && !applyServer()) return;
    categoryRef.current = next; setCategory(next); setCollection(null); updateServer(null); select(0); setError("");
  }
  function activate(item = row) {
    if (!item || !active || busy) return;
    setError("");
    if (item.action) { item.action(); return; }
    if (!item.set) return;
    if (item.choices?.length) { choose({ row: item, index: Math.max(0, item.choices.indexOf(item.value)) }); return; }
    if (typeof item.value === "boolean") { item.set(!item.value); return; }
    if (Array.isArray(item.value) || item.schema?.type === "array" || item.schema?.type === "object") {
      setCollection({ row: item, value: item.value, dictionary: item.schema?.type === "object" }); select(0); return;
    }
    startEdit(item);
  }
  function back() {
    if (choiceRef.current) choose(null);
    else if (editingRef.current) { editingRef.current = null; setEditing(null); setError(""); }
    else if (collection) { setCollection(null); select(0); }
    else if (server) { updateServer(null); select(0); }
    else done();
  }
  useKeyboard((key) => {
    if (!active || connection || key.defaultPrevented) return;
    if (busy) { key.preventDefault(); key.stopPropagation(); return; }
    if (key.name === "escape" || key.ctrl && key.name === "c") {
      key.preventDefault(); key.stopPropagation(); back(); return;
    }
    if (key.ctrl && key.name === "s") { key.preventDefault(); key.stopPropagation(); void save(); return; }
    const selected = choiceRef.current;
    if (selected) {
      key.preventDefault(); key.stopPropagation();
      if (key.name === "return") { selected.row.set?.(selected.row.choices![selected.index]); choose(null); }
      else if (["up", "down", "tab"].includes(key.name)) {
        const count = selected.row.choices!.length;
        choose({ ...selected, index: (selected.index + (key.name === "up" || key.shift ? -1 : 1) + count) % count });
      }
      return;
    }
    if (key.name === "return" && !key.shift) {
      key.preventDefault(); key.stopPropagation();
      if (editingRef.current) commitEdit(); else if (paneRef.current === "categories") focusPane("fields"); else activate(); return;
    }
    if (editingRef.current) return;
    if (key.name === "tab" || ["left", "right"].includes(key.name)) {
      key.preventDefault(); key.stopPropagation();
      focusPane(key.name === "left" ? "categories" : key.name === "right" ? "fields" : paneRef.current === "fields" ? "categories" : "fields"); return;
    }
    if (key.name === "delete" && row?.remove && paneRef.current === "fields") { key.preventDefault(); row.remove(); return; }
    if (["up", "down", "pageup", "pagedown"].includes(key.name)) {
      key.preventDefault(); key.stopPropagation();
      const delta = key.name === "up" || key.name === "pageup" ? -1 : 1;
      if (paneRef.current === "categories") {
        const index = settingCategories.findIndex(([id]) => id === categoryRef.current);
        switchCategory(settingCategories[(index + delta + settingCategories.length) % settingCategories.length][0]);
      } else select((focusRef.current + delta * (key.name.startsWith("page") ? 6 : 1) + rows.length * 6) % Math.max(1, rows.length));
    }
  });
  if (connection) {
    const llm = record(draft.llm);
    return <LlmSettings controller={controller} active={active} document={{ ...llm, api_key_set: !!llm.api_key,
      providers: (Array.isArray(current.providers) ? current.providers : []).map((item) => ({ ...record(item), id: record(item).provider })) }}
      onCancel={() => setConnection(false)} onSave={async (values) => {
        let next = draftRef.current;
        for (const [key] of connectionFields) if (key !== "api_key" || values.api_key) next = withValue(next, "llm." + key, values[key]);
        await persist(next); setConnection(false);
      }} />;
  }
  const info = editing ?? row;
  const description = String(info?.help ?? info?.schema?.description ?? "");
  const limits = [["minimum", "min"], ["maximum", "max"], ["exclusiveMinimum", ">"], ["exclusiveMaximum", "<"]]
    .filter(([key]) => info?.schema?.[key] !== undefined).map(([key, label]) => label + " " + info!.schema![key]).join(" · ");
  const heading = collection ? collection.row.label : server ? server.key ?? "New MCP service"
    : settingCategories.find(([id]) => id === category)![1];
  const pendingChanges = Object.keys(changes).length + (server && !sameValue(server.value,
    server.key ? record(record(draft.mcp).servers)[server.key] : undefined) ? 1 : 0);
  const path = collection ? collection.row.key : info?.key ?? "";
  const fieldPath = server && info?.set ? "mcp.servers." + String(server.value.name ?? "") + "." + path
    : path.includes(".") ? path : "";
  return <box id="settings-pane" title="Settings" border borderColor={p.action} backgroundColor={p.panel}
    flexDirection="column" flexGrow={1} minHeight={0} paddingX={1}>
    <box flexDirection="row" flexGrow={1} minHeight={0}>
      <box width={categoryWidth} flexShrink={0} border={["right"]} borderColor={pane === "categories" ? p.action : p.border}>
        <scrollbox scrollX={false}>
          {settingCategories.map(([id, label]) => <text key={id} id={"settings-category-" + id}
            fg={id === category ? p.action : p.muted} bg={id === category ? p.plate : p.panel}
            onMouseDown={() => { if (active && !editing && !busy) { switchCategory(id); focusPane("fields"); } }}>
            {visibleText((id === category ? "› " : "  ") + label)}
          </text>)}
        </scrollbox>
      </box>
      <box flexDirection="column" flexGrow={1} minWidth={0} paddingLeft={1}>
        <text fg={p.action} flexShrink={0}><b>{visibleText(heading)}</b></text>
        <scrollbox id="settings-scroll" ref={scroll} flexGrow={1} minHeight={0} scrollX={false}>
          {rows.map((item, index) => <box key={item.key} id={"settings-row-" + index}
            flexDirection="column" flexShrink={0} backgroundColor={index === focus && pane === "fields" ? p.plate : p.panel}
            onMouseDown={() => { if (active && !editing && !busy && !choice) { select(index); focusPane("fields"); } }}>
            {item.section && <text fg={p.action}><b>{item.section}</b></text>}
            <box id={"settings-field-" + item.key} flexDirection="row" flexShrink={0} gap={1}>
              <text width={labelWidth} wrapMode="word" fg={index === focus ? p.action : p.muted}>
                {visibleText((index === focus && pane === "fields" ? "› " : "  ") + item.label)}
              </text>
              {editing?.key === item.key ? <box flexGrow={1} minWidth={1}>
                <textarea id="settings-editor" ref={editor} focused={active} initialValue={buffer}
                  height={Math.min(4, Math.max(1, buffer.split("\n").length))} textColor={p.text} backgroundColor={p.plate}
                  onContentChange={() => setBuffer(editor.current?.plainText ?? "")} />
                {item.secret && <box position="absolute" left={0} top={0} width="100%" height="100%" backgroundColor={p.plate} zIndex={1}>
                  <text fg={p.text}>{"•".repeat(Array.from(buffer).length)}</text>
                </box>}
              </box> : <text flexGrow={1} wrapMode="word" fg={item.key === "safety.permission_mode" ? permissionInfo(String(item.value)).color
                : typeof item.value === "boolean" && item.value ? p.success : p.text}>
                {visibleText(Array.isArray(item.value) ? item.value.length + " items"
                  : item.schema?.type === "object" ? Object.keys(record(item.value)).length + " entries"
                    : item.secret ? item.value ? "Configured" : "Not configured"
                      : typeof item.value === "boolean" ? item.value ? "[x] Enabled" : "[ ] Disabled"
                        : item.key === "safety.permission_mode" ? permissionInfo(String(item.value)).label
                          : String(item.value ?? (item.action ? "" : "Default")))}
              </text>}
            </box>
            {choice?.row.key === item.key && choice.row.choices?.map((value, index) => <text
              key={index} id={"settings-option-" + index} fg={item.key === "safety.permission_mode" ? permissionInfo(String(value)).color : index === choice.index ? p.action : p.muted}
              onMouseDown={(event) => { event.stopPropagation(); if (active && !busy) { choice.row.set?.(value); choose(null); } }}>
              {visibleText("    " + (index === choice.index ? "› " : "  ") + (item.key === "safety.permission_mode" ? permissionInfo(String(value)).label : String(value)))}
            </text>)}
          </box>)}
        </scrollbox>
      </box>
    </box>
    <box id="settings-footer" flexDirection="column" flexShrink={0} border={["top"]} borderColor={p.border}>
      <text fg={p.hint} height={1} truncate>{visibleText(fieldPath || " ")}</text>
      <text fg={p.muted} height={2} wrapMode="word">{visibleText([description, limits].filter(Boolean).join(" · ") || " ")}</text>
      <box flexDirection="row" flexShrink={0}>
        <text id="settings-feedback" fg={error ? p.error : feedback ? p.success : p.muted} wrapMode="word" flexGrow={1}>
          {visibleText(error || feedback || " ")}
        </text>
        <text fg={p.muted}>{pendingChanges + " unsaved changes"}</text>
      </box>
      <box flexDirection="row" gap={2} flexShrink={0}>
        <text id="settings-save" fg={p.background} bg={p.action} onMouseDown={() => { if (active) void save(); }}> Save (Ctrl+S) </text>
        <text id="settings-close" fg={p.soft} bg={p.plate} onMouseDown={() => { if (active && !busy) back(); }}> {collection || server ? "Back" : "Close / discard"} </text>
        {row?.secret && row.set && <text id="settings-clear" fg={p.error} onMouseDown={() => {
          if (active && !busy && !editing) row.set?.(Array.isArray(row.value) ? [] : row.schema?.type === "object" ? {} : "");
        }}>[Clear value]</text>}
        {row?.remove && <text id="settings-remove" fg={p.error} onMouseDown={() => { if (active && !busy && !editing) row.remove?.(); }}>[Remove item]</text>}
      </box>
      <text fg={p.hint} wrapMode="word">{busy ? "Saving…" : editing
        ? "Enter apply · Shift+Enter newline · Esc revert · Ctrl+S save"
        : "Tab switch pane · ↑↓ select · Enter edit · Ctrl+S save · Esc back"}</text>
    </box>
  </box>;
}
