import { useRef, useState } from "react";
import type { ScrollBoxRenderable, TextareaRenderable } from "@opentui/core";
import { useKeyboard } from "@opentui/react";
import { record, type SessionController } from "../session/controller";
import { palettes } from "../theme/palette";
import { visibleText } from "./content";
import { connectionFields as fields } from "./config-fields";
import { useI18n } from "../i18n";

type Field = typeof fields[number][0];

export function LlmSettings({ controller, document, active, onSave, onCancel }: {
  controller: SessionController; document: Record<string, unknown>; active: boolean;
  onSave?: (values: Record<string, string>) => Promise<void>; onCancel?: () => void;
}) {
  const t = useI18n();
  const p = palettes[controller.theme];
  const providers = (Array.isArray(document.providers) ? document.providers : []).map(record);
  const [values, setValues] = useState<Record<string, string>>({
    provider: String(document.provider ?? ""), website_url: String(document.website_url ?? ""),
    base_url: String(document.base_url ?? ""), api_key: "", model: String(document.model ?? ""),
  });
  const [focus, setFocus] = useState(0);
  const focusRef = useRef(0);
  const [editing, updateEditing] = useState(false);
  const editingRef = useRef(false);
  const [list, updateList] = useState(false);
  const listRef = useRef(false);
  const [listIndex, setListIndex] = useState(0);
  const listIndexRef = useRef(0);
  const [buffer, setBuffer] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [suggestion, setSuggestion] = useState<number | null>(null);
  const suggestionRef = useRef<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [error, setError] = useState("");
  const editor = useRef<TextareaRenderable>(null);
  const scroll = useRef<ScrollBoxRenderable>(null);
  const field = fields[focus][0];
  function setEditing(value: boolean) { editingRef.current = value; updateEditing(value); }
  function setList(value: boolean) { listRef.current = value; updateList(value); }
  function matchingModels(text: string) {
    const typed = text.trim().toLowerCase();
    return models.filter((name) => name.toLowerCase().includes(typed))
      .sort((a, b) => Number(b.toLowerCase().startsWith(typed)) - Number(a.toLowerCase().startsWith(typed)));
  }
  const matches = matchingModels(buffer);
  async function fetchModels() {
    if (!values.base_url.trim()) { setError(t("Set an API request URL before fetching models. You can type a model id.")); return; }
    setBusy(true); setFeedback("Fetching models…"); setError("");
    try {
      const result = await controller.control("config.models", { base_url: values.base_url, api_key: values.api_key });
      const next = Array.isArray(result.models) ? result.models.filter((item): item is string => typeof item === "string") : [];
      setModels(next);
      if (next.length) setFeedback(t("{count} models available.", { count: next.length }));
      else { setFeedback(""); setError(t("No models returned; keeping the current model. You can type a model id.")); }
    } catch (error) { setFeedback(""); setError(`${String(error)} · ${t("You can type a model id.")}`); }
    finally { setBusy(false); }
  }
  async function preset() {
    const provider = providers[listIndexRef.current]; if (!provider || busy) return;
    setList(false); setBusy(true); setError("");
    try {
      const next = await controller.control("config.preset", { provider: provider.id });
      setValues((current) => ({ ...current, ...Object.fromEntries(["provider", "website_url", "base_url", "model"].map((key) => [key, String(next[key] ?? "")])) }));
      setModels([]); setSuggestion(null); setFeedback("");
    } catch (error) { setError(String(error)); }
    finally { setBusy(false); }
  }
  function editedValues() {
    if (!editingRef.current) return values;
    const text = editor.current?.plainText ?? buffer;
    return { ...values, [field]: field === "model" && suggestionRef.current !== null ? matchingModels(text)[suggestionRef.current] ?? text : text };
  }
  function commit() {
    const next = editedValues(); setValues(next); setEditing(false); setSuggestion(null);
    if (field === "base_url" || field === "api_key") setModels([]);
  }
  async function save() {
    if (busy) return;
    setBusy(true); setError(""); setFeedback("Saving…");
    try {
      if (onSave) { await onSave(editedValues()); return; }
      await controller.control("config.write", editedValues());
      controller.notify(t("Configuration saved.")); controller.dismiss();
    } catch (error) { setError(String(error)); setFeedback(""); }
    finally { setBusy(false); }
  }
  function activate() {
    if (busy) return;
    setError("");
    const currentField = fields[focusRef.current][0];
    if (currentField === "provider") {
      if (!providers.length) { setError(t("No provider templates available.")); return; }
      listIndexRef.current = Math.max(0, providers.findIndex((item) => item.id === values.provider));
      setListIndex(listIndexRef.current); setList(true);
    } else {
      setBuffer(values[currentField]); setEditing(true); setSuggestion(null); suggestionRef.current = null;
      if (currentField === "model") void fetchModels();
    }
  }
  function move(delta: number) {
    focusRef.current = (focusRef.current + delta + fields.length) % fields.length;
    setFocus(focusRef.current); setError(""); scroll.current?.scrollChildIntoView(`llm-row-${focusRef.current}`);
  }
  useKeyboard((key) => {
    if (!active || key.defaultPrevented) return;
    if (key.name === "escape" || key.ctrl && key.name === "c") {
      key.preventDefault(); key.stopPropagation();
      if (listRef.current) setList(false); else if (editingRef.current) { setEditing(false); setSuggestion(null); } else (onCancel ?? (() => controller.dismiss()))();
      return;
    }
    if (key.ctrl && key.name === "s") { key.preventDefault(); key.stopPropagation(); void save(); return; }
    if (key.name === "return") {
      key.preventDefault(); key.stopPropagation();
      if (listRef.current) void preset(); else if (editingRef.current) commit(); else activate(); return;
    }
    if (["up", "down", "tab"].includes(key.name)) {
      if (editingRef.current && fields[focusRef.current][0] !== "model") return;
      key.preventDefault(); key.stopPropagation();
      const delta = key.name === "up" || key.shift ? -1 : 1;
      if (listRef.current) { listIndexRef.current = (listIndexRef.current + delta + providers.length) % providers.length; setListIndex(listIndexRef.current); }
      else if (editingRef.current && matches.length) {
        const index = suggestionRef.current;
        suggestionRef.current = index === null ? delta > 0 ? 0 : matches.length - 1 : (index + delta + matches.length) % matches.length;
        setSuggestion(suggestionRef.current);
      }
      else if (!editingRef.current && !busy) move(delta);
    }
  });
  function displayed(key: Field): string {
    if (key === "api_key") return values.api_key ? "•".repeat(Array.from(values.api_key).length) : document.api_key_set ? t("•••••••• (saved)") : "—";
    if (key === "provider") return String(providers.find((item) => item.id === values.provider)?.label ?? values.provider);
    return values[key] || "—";
  }
  const options = list ? providers.map((item) => String(item.label ?? item.id)) : matches;
  const selected = list ? listIndex : suggestion;
  const start = Math.max(0, (selected ?? 0) - 5);
  return <box id="llm-pane" border borderColor={p.action} backgroundColor={p.panel} flexDirection="column" flexGrow={1} minHeight={0} paddingX={1} title={t("LLM settings")}>
    <scrollbox id="llm-scroll" ref={scroll} flexGrow={1} scrollX={false}>
      {fields.map(([key, label], index) => <box key={key} id={`llm-row-${index}`} flexDirection="column" flexShrink={0}>
        <box flexDirection="row" backgroundColor={focus === index ? p.plate : p.panel}
          onMouseDown={() => { if (!busy && !editing && !list) { focusRef.current = index; setFocus(index); } }}>
          <text fg={focus === index ? p.action : p.muted} width={21}>{`${focus === index ? editing ? "✎" : "›" : " "} ${t(label)}`}</text>
          {editing && field === key ? <box flexGrow={1} minWidth={1}>
            <textarea id="llm-editor" key={key} ref={editor} focused={active} initialValue={buffer} height={1}
              textColor={p.text} backgroundColor={p.plate}
              onContentChange={() => { setBuffer(editor.current?.plainText ?? ""); setSuggestion(null); suggestionRef.current = null; }} />
            {key === "api_key" && <box position="absolute" top={0} left={0} width="100%" height={1} backgroundColor={p.plate} zIndex={1}>
              <text fg={p.text}>{"•".repeat(Array.from(buffer).length)}</text>
            </box>}
          </box> : <text fg={p.soft} wrapMode="word" flexGrow={1}>{visibleText(displayed(key))}</text>}
        </box>
        {focus === index && (list || editing && field === "model") && <box flexDirection="column" paddingLeft={4}>
          {options.slice(start, start + 6).map((option, offset) => <text key={start + offset}
            fg={start + offset === selected ? p.action : p.muted} bg={start + offset === selected ? p.plate : p.panel}>
            {visibleText(`${start + offset === selected ? "›" : " "} ${option}`)}
          </text>)}
          {options.length > start + 6 && <text fg={p.hint}>{t("… {count} more", { count: options.length - start - 6 })}</text>}
        </box>}
      </box>)}
    </scrollbox>
    <text fg={error ? p.error : p.muted} wrapMode="word">{visibleText(error || t(feedback) || " ")}</text>
    <text fg={p.hint} wrapMode="word">{t(list ? "↑↓/Tab template · Enter apply · Esc cancel" : editing ? "↑↓ model suggestions · Enter confirm · Esc revert · Ctrl+S save" : "↑↓/Tab row · Enter edit · Ctrl+S save · Esc close")}</text>
  </box>;
}
