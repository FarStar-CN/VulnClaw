import { useEffect, useRef, useState } from "react";
import type { ScrollBoxRenderable, TextareaRenderable } from "@opentui/core";
import { useKeyboard } from "@opentui/react";
import type { ThemeId } from "../theme/palette";
import { palettes } from "../theme/palette";
import { visibleText } from "./content";

export interface FormRow {
  key: string; label: string; value?: unknown; choices?: unknown[];
  type?: "text" | "number" | "json" | "secret";
  action?: () => void; error?: string;
}
export function FormPanel({ title, rows, theme, active, busy, onChange, onSave, onCancel, confirmKeys }: {
  title: string; rows: FormRow[]; theme: ThemeId; active: boolean; busy?: boolean;
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
  function activate() {
    const row = rows[Math.min(focusIndex.current, rows.length - 1)];
    if (!row || busy) return;
    setError("");
    if (row.action) { row.action(); return; }
    if (row.choices?.length) {
      const index = row.choices.indexOf(row.value);
      onChange(row.key, row.choices[(index + 1) % row.choices.length]); return;
    }
    if (typeof row.value === "boolean") { onChange(row.key, !row.value); return; }
    setBuffer(row.type === "secret" ? "" : typeof row.value === "string" ? row.value : JSON.stringify(row.value ?? null, null, 2));
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
    if (editing || busy) return;
    if (confirmKeys && !key.ctrl && ["y", "n"].includes(key.name)) {
      key.preventDefault(); key.stopPropagation();
      if (key.name === "y") onSave(); else onCancel(); return;
    }
    if (["up", "down", "tab", "pageup", "pagedown"].includes(key.name)) {
      key.preventDefault(); key.stopPropagation();
      const delta = key.name === "up" || key.name === "pageup" || key.shift ? -1 : 1;
      focusIndex.current = Math.max(0, Math.min(rows.length - 1, focusIndex.current + delta * (key.name.startsWith("page") ? 8 : 1)));
      setFocus(focusIndex.current);
    }
  });
  return <box id="form-pane" title={title} border borderColor={p.accent} flexDirection="column" flexGrow={1} minHeight={0}>
    <scrollbox ref={scroll} flexGrow={1} scrollX={false}>
      {rows.map((item, index) => <box id={`form-row-${index}`} key={item.key} flexDirection="column" flexShrink={0}
        onMouseDown={() => { if (active && !busy && !editing) { focusIndex.current = index; setFocus(index); } }}>
        <text fg={index === focus ? p.accent : p.text}>
          {visibleText(`${index === focus ? "›" : " "} ${item.label}${item.action ? "" : `: ${item.type === "secret" && item.value ? "***" : typeof item.value === "boolean" ? item.value ? "[x]" : "[ ]" : typeof item.value === "string" ? item.value : JSON.stringify(item.value ?? null)}`}`)}
        </text>
        {item.error && <text fg={p.error}>{visibleText(item.error)}</text>}
      </box>)}
    </scrollbox>
    {editing && <textarea key={row?.key} id="form-editor" ref={editor} focused={active} initialValue={buffer}
      height={4} textColor={p.text} backgroundColor={p.background}
      placeholder={row?.type === "secret" ? "Blank keeps the current value; !clear removes it" : "Edit value; Enter applies; Esc cancels"} />}
    {error && <text fg={p.error}>{visibleText(error)}</text>}
    <text fg={p.muted}>{busy ? "Working…" : editing ? "Enter apply · Shift+Enter newline · Esc cancel edit" : "↑↓ select · Enter edit/cycle · Ctrl+S confirm · Esc cancel"}</text>
  </box>;
}
