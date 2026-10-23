import { useEffect, useRef, useState } from "react";
import type { ThemeId } from "../theme/palette";
import { useI18n } from "../i18n";
import { FormPanel, type FormRow } from "./FormPanel";

export type ScopePreview = {
  scope?: Record<string, unknown>; target?: string; constraints?: Record<string, unknown> | null;
  actions?: string[]; available_actions?: string[];
  action_policy?: { allow_actions: string[]; block_actions: string[] };
  presets?: { name: string; actions: string[] }[]; errors?: Record<string, string>;
};

export function ScopeForm({ initial, loading, active, readOnly, theme, summary, onPreview, onSave, onSaved, onCancel }: {
  initial: ScopePreview; loading: boolean; active: boolean; readOnly: boolean; theme: ThemeId;
  summary: (constraints: Record<string, unknown>) => string;
  onPreview: (scope: Record<string, unknown>) => Promise<ScopePreview>;
  onSave: (scope: Record<string, unknown>, reset: boolean) => Promise<ScopePreview>;
  onSaved: (result: ScopePreview) => void; onCancel: () => void;
}) {
  const t = useI18n();
  const [preview, setPreview] = useState(initial);
  const [draft, setDraft] = useState(initial.scope ?? {});
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const cancelled = useRef(false);
  const revision = useRef(0);
  useEffect(() => { setPreview(initial); setDraft(initial.scope ?? {}); }, [initial]);
  useEffect(() => () => { cancelled.current = true; }, []);
  function cancel() { cancelled.current = true; onCancel(); }
  async function validate(next: Record<string, unknown>) {
    const version = ++revision.current;
    setDraft(next); setBusy(true); setNotice("");
    try {
      const result = await onPreview(next);
      if (cancelled.current || version !== revision.current) return;
      setPreview((old) => ({ ...old, ...result })); setDraft(result.scope ?? next);
    } catch (error) { if (!cancelled.current) setNotice(String(error)); }
    finally { if (!cancelled.current && version === revision.current) setBusy(false); }
  }
  async function save() {
    if (busy || loading || readOnly) return;
    setBusy(true); setNotice("");
    try {
      const empty = Object.values(draft).every((value) => value == null || value === "" || Array.isArray(value) && !value.length);
      const result = await onSave(draft, empty);
      if (cancelled.current) return;
      if (Object.keys(result.errors ?? {}).length) {
        setPreview((old) => ({ ...old, ...result })); setDraft(result.scope ?? draft);
      } else onSaved(result);
    } catch (error) { if (!cancelled.current) setNotice(String(error)); }
    finally { if (!cancelled.current) setBusy(false); }
  }
  const allActions = preview.available_actions ?? [];
  const selected = preview.actions ?? [];
  const presets = preview.presets ?? [];
  function change(key: string, value: unknown) {
    const next = { ...draft };
    if (key === "preset" || key.startsWith("action.")) {
      const choices = new Set(key === "preset" ? presets.find((preset) => preset.name === value)?.actions ?? selected : selected);
      if (key.startsWith("action.")) { if (value) choices.add(key.slice(7)); else choices.delete(key.slice(7)); }
      const policy = preview.action_policy;
      next.allow_actions = [...(policy?.allow_actions ?? []).filter((action) => !allActions.includes(action)), ...choices];
      next.block_actions = [...(policy?.block_actions ?? []).filter((action) => !allActions.includes(action)), ...allActions.filter((action) => !choices.has(action))];
    } else next[key] = value;
    void validate(next);
  }
  const errors = preview.errors ?? {};
  const fields = [
    ["only_host", "Allow hosts"], ["blocked_host", "Block hosts"],
    ["allowed_ports", "Allow ports"], ["blocked_ports", "Block ports"],
    ["only_path", "Allow paths"], ["blocked_path", "Block paths"],
  ];
  const rows: FormRow[] = [
    { key: "target", label: "Current target (read-only)", value: preview.target || "—", disabled: true },
    ...fields.map(([key, label]) => ({ key: key!, label: label!, type: "text" as const,
      value: Array.isArray(draft[key!]) ? (draft[key!] as unknown[]).join(",") : draft[key!] ?? "",
      error: errors[key!], section: key === "only_host" ? "Access rules" : undefined })),
    { key: "preset", label: "Action preset", section: "Allow actions", value: presets.find((preset) =>
      preset.actions.length === selected.length && preset.actions.every((action) => selected.includes(action)))?.name ?? "Custom",
      choices: presets.map((preset) => preset.name) },
    ...allActions.map((action) => ({ key: `action.${action}`, label: `  ${action}`, value: selected.includes(action), error: errors.allow_actions ?? errors.block_actions })),
    { key: "effective", label: "Effective constraints", value: Object.keys(errors).length ? t("Correct the fields to preview effective constraints.") : summary(preview.constraints ?? {}), disabled: true },
    { key: "reset", label: "Reset draft", action: () => { void validate({}); } },
    { key: "confirm", label: "Save scope (Y)", footer: true, disabled: readOnly, action: () => { void save(); } },
    { key: "cancel", label: "Cancel (N)", footer: true, action: cancel },
  ];
  return <FormPanel title={t("Session scope preview")} rows={rows} theme={theme} active={active} busy={busy || loading} confirmKeys
    notice={notice || (readOnly ? t("A task is active. Scope is read-only until it completes.") : undefined)}
    onChange={change} onSave={() => { void save(); }} onCancel={cancel} />;
}
