import { useEffect, useRef, useState } from "react";
import { BorderChars, type ScrollBoxRenderable, type TextareaRenderable } from "@opentui/core";
import { useKeyboard } from "@opentui/react";
import type { ThemeId } from "../theme/palette";
import { palettes } from "../theme/palette";
import { visibleText } from "./content";

export interface FormRow {
  key: string; label: string; value?: unknown; displayValue?: string; choices?: unknown[]; choiceLabels?: string[];
  type?: "text" | "number" | "json" | "secret";
  action?: () => void; error?: string; section?: string; footer?: boolean; disabled?: boolean;
}
export function FormPanel({ title, rows, theme, active, busy, onChange, onSave, onCancel, confirmKeys, notice }: {
  title: string; rows: FormRow[]; theme: ThemeId; active: boolean; busy?: boolean; notice?: string;
  onChange: (key: string, value: unknown) => void; onSave: () => void; onCancel: () => void; confirmKeys?: boolean;
}) {
  const [focus, setFocus] = useState(0);
  const focusIndex = useRef(0);
  const [editing, setEditing] = useState(false);
  const [buffer, setBuffer] = useState("");
  const [error, setError] = useState("");
  const editor = useRef<TextareaRenderable>(null);
  const scroll = useRef<ScrollBoxRenderable>(null);
  const p = palettes[theme];
  const row = rows[Math.min(focus, rows.length - 1)];
  useEffect(() => { scroll.current?.scrollChildIntoView(`form-row-${focus}`); }, [focus]);
  const errors = rows.map((item) => item.error ?? "").join("|");
  useEffect(() => {
    const first = rows.findIndex((item) => item.error);
    if (first >= 0 && !editing) { focusIndex.current = first; setFocus(first); }
  }, [errors]);
  function activate() {
    const item = rows[Math.min(focusIndex.current, rows.length - 1)];
    if (!item || busy || item.disabled) return;
    setError("");
    if (item.action) { item.action(); return; }
    if (item.choices?.length) {
      onChange(item.key, item.choices[(item.choices.indexOf(item.value) + 1) % item.choices.length]); return;
    }
    if (typeof item.value === "boolean") { onChange(item.key, !item.value); return; }
    setBuffer(item.type === "secret" ? "" : typeof item.value === "string" ? item.value : JSON.stringify(item.value ?? null, null, 2));
    setEditing(true);
  }
  function commit() {
    if (!row) return;
    const text = editor.current?.plainText ?? buffer;
    try {
      if (row.type === "secret" && !text) { setEditing(false); return; }
      const value = row.type === "secret" ? text === "!clear" ? Array.isArray(row.value) ? [] : "" : Array.isArray(row.value) ? JSON.parse(text) : text
        : row.type === "number" || typeof row.value === "number" ? Number(text)
          : row.type === "json" || (typeof row.value !== "string" && row.type !== "text") ? JSON.parse(text) : text;
      if (typeof value === "number" && !Number.isFinite(value)) throw new Error("Enter a valid number");
      onChange(row.key, value); setEditing(false); setError("");
    } catch (error) { setError(String(error)); }
  }
  useKeyboard((key) => {
    if (!active || key.defaultPrevented) return;
    if (key.name === "escape" || key.ctrl && key.name === "c") {
      key.preventDefault(); key.stopPropagation();
      if (editing) setEditing(false); else onCancel(); return;
    }
    if (key.ctrl && key.name === "s") {
      key.preventDefault(); key.stopPropagation();
      if (editing) commit(); else if (!busy) onSave(); return;
    }
    if (key.name === "return" && !key.shift) {
      key.preventDefault(); key.stopPropagation();
      if (editing) commit(); else activate(); return;
    }
    if (editing) return;
    if (confirmKeys && !key.ctrl && !key.meta && ["y", "n"].includes(key.name.toLowerCase())) {
      key.preventDefault(); key.stopPropagation();
      if (key.name.toLowerCase() === "n") onCancel(); else if (!busy) onSave(); return;
    }
    if (busy) return;
    if (["up", "down", "tab", "pageup", "pagedown"].includes(key.name)) {
      key.preventDefault(); key.stopPropagation();
      const delta = key.name === "up" || key.name === "pageup" || key.shift ? -1 : 1;
      focusIndex.current = (focusIndex.current + delta * (key.name.startsWith("page") ? 8 : 1) + rows.length * 8) % rows.length;
      setFocus(focusIndex.current);
    }
  });
  function renderRow(item: FormRow, index: number) {
    const selected = index === focus;
    const value = item.displayValue ?? (item.type === "secret" && item.value ? "•••••••• (saved)"
      : typeof item.value === "boolean" ? item.value ? "[x] Enabled" : "[ ] Disabled"
        : typeof item.value === "string" ? item.value || "—" : JSON.stringify(item.value ?? null));
    return <box id={`form-row-${index}`} key={item.key} flexDirection="column" flexShrink={0}
      onMouseDown={() => { if (active && !busy && !editing) { focusIndex.current = index; setFocus(index); if (item.footer) activate(); } }}>
      {item.section && <text fg={p.action}><b>{` ${item.section} ─────────────`}</b></text>}
      <box flexDirection="row" backgroundColor={selected ? p.plate : p.panel}>
        <text fg={item.error ? p.error : selected ? p.action : p.muted} width={confirmKeys ? 24 : undefined} wrapMode="word">
          {visibleText(`${selected ? "›" : " "} ${item.label}${!confirmKeys && !item.action ? ": " : ""}`)}
        </text>
        {!item.action && (selected && editing ? <box flexGrow={1} minWidth={1}>
          <textarea key={item.key} id="form-editor" ref={editor} focused={active} initialValue={buffer}
            height={item.type === "secret" ? 1 : Math.min(4, Math.max(1, buffer.split("\n").length))}
            textColor={p.text} backgroundColor={p.plate}
            onContentChange={() => { if (item.type === "secret") setBuffer(editor.current?.plainText ?? ""); }} />
          {item.type === "secret" && <box position="absolute" left={0} top={0} width="100%" height={1} backgroundColor={p.plate} zIndex={1}>
            <text fg={p.text}>{"•".repeat(Array.from(buffer).length)}</text>
          </box>}
        </box> : item.choices?.length && confirmKeys ? <text flexGrow={1} wrapMode="word">
          {item.choices.map((choice, i) => <span key={i} fg={choice === item.value ? p.background : p.hint}
            bg={choice === item.value ? p.action : p.panel}>{` ${item.choiceLabels?.[i] ?? String(choice)} `}</span>)}
        </text> : <text flexGrow={1} fg={item.error ? p.error : typeof item.value === "boolean" && item.value ? p.success : p.text} wrapMode="word">{visibleText(value)}</text>)}
      </box>
      {item.error && <text fg={p.error} wrapMode="word">{visibleText(item.error)}</text>}
    </box>;
  }
  return <box id="form-pane" title={title} border borderStyle={confirmKeys ? "rounded" : "single"}
    customBorderChars={confirmKeys ? { ...BorderChars.heavy,
      topLeft: "╭", topRight: "╮", bottomLeft: "╰", bottomRight: "╯" } : undefined}
    borderColor={p.action} backgroundColor={p.panel} flexDirection="column" flexGrow={1} minHeight={0} paddingX={1}>
    <scrollbox ref={scroll} flexGrow={1} scrollX={false}>
      {rows.map((item, index) => !item.footer && renderRow(item, index))}
    </scrollbox>
    {(error || notice) && <text fg={error ? p.error : p.muted} wrapMode="word">{visibleText(error || notice || "")}</text>}
    <box flexDirection="row" justifyContent="center" gap={2} flexShrink={0}>
      {rows.map((item, index) => item.footer && <box key={item.key} id={`form-row-${index}`}
        onMouseDown={() => { if (active && !busy && !editing) { focusIndex.current = index; setFocus(index); activate(); } }}>
        <text fg={item.disabled ? p.hint : item.key === "confirm" ? p.background : p.soft}
          bg={item.disabled ? p.plate : item.key === "confirm" ? p.action : p.plate}>
          {visibleText(` ${index === focus ? "›" : " "} ${item.label} `)}
        </text>
      </box>)}
    </box>
    <text fg={p.muted}>{busy ? "Working…" : editing ? "Enter apply · Shift+Enter newline · Esc cancel edit" : "↑↓/Tab select · Enter edit/cycle · Esc cancel"}</text>
  </box>;
}
