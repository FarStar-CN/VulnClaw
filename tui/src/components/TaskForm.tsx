import { useRef, useState } from "react";
import { type SessionController, record } from "../session/controller";
import type { TaskRequest } from "../transport/protocol.generated";
import { FormPanel, type FormRow } from "./FormPanel";

export function TaskForm({ controller, initial, original, active }: {
  controller: SessionController; initial: Record<string, unknown>; original: string; active: boolean;
}) {
  const [preview, setPreview] = useState(initial);
  const [raw, setRaw] = useState(original);
  const [busy, setBusy] = useState(false);
  const task = record(preview.task);
  const options = record(task.options);
  const cycleDraft = useRef({ rounds: options.rounds_per_cycle, cycles: options.max_cycles });
  const errors = record(preview.errors);
  const presets = (Array.isArray(preview.presets) ? preview.presets : []).map(record);
  const allActions = [...new Set(presets.flatMap((preset) => Array.isArray(preset.actions) ? preset.actions.map(String) : []))];
  const allowed = Array.isArray(options.allow_actions) ? options.allow_actions.map(String) : [];
  const [reportEdited, setReportEdited] = useState(record(preview.defaults).auto_report_explicit === true);
  async function validate(next: Record<string, unknown>, confirm = false) {
    setBusy(true);
    try {
      const result = await controller.control("task.preview", Object.keys(next).length ? { task: next } : { text: raw });
      setPreview((previous) => ({ ...previous, ...result,
        task: result.task ?? (Object.keys(next).length ? next : previous.task),
      }));
      if (confirm && !Object.keys(record(result.errors)).length && result.task) controller.startTask(result.task as TaskRequest);
    } catch (error) { controller.notify(String(error)); }
    finally { setBusy(false); }
  }
  function change(key: string, value: unknown) {
    if (key === "raw") { setRaw(String(value)); return; }
    const next = structuredClone(task);
    const updated = record(next.options);
    next.options = updated;
    if (key === "preset") {
      const preset = presets.find((preset) => preset.name === value);
      if (!preset) return;
      updated.allow_actions = preset.actions;
      updated.block_actions = allActions.filter((action) => !(preset.actions as string[]).includes(action));
    } else if (key.startsWith("action.")) {
      const selected = new Set(allowed);
      if (value) selected.add(key.slice(7)); else selected.delete(key.slice(7));
      updated.allow_actions = [...selected]; updated.block_actions = allActions.filter((action) => !selected.has(action));
    } else if (key.startsWith("options.")) {
      const field = key.slice(8);
      updated[field] = value;
      if (field === "auto_report") setReportEdited(true);
      if (field === "continuous" && !reportEdited) {
        const defaults = record(preview.defaults);
        updated.auto_report = value ? defaults.continuous_auto_report : defaults.auto_report;
      }
      if (field === "continuous" && value) {
        const defaults = record(preview.defaults);
        updated.rounds_per_cycle ??= cycleDraft.current.rounds ?? defaults.rounds_per_cycle;
        updated.max_cycles ??= cycleDraft.current.cycles ?? defaults.max_cycles;
      } else if (field === "continuous") {
        cycleDraft.current = { rounds: updated.rounds_per_cycle, cycles: updated.max_cycles };
        delete updated.rounds_per_cycle; delete updated.max_cycles;
      }
    } else next[key] = value;
    void validate(next);
  }
  const fields: [string, string, FormRow["type"]?][] = [
    ["target", "Target URL"], ["options.only_host", "Allow hosts", "text"],
    ["options.blocked_host", "Block hosts", "text"], ["options.allowed_ports", "Allow ports (JSON or comma-separated)", "text"],
    ["options.blocked_ports", "Block ports", "text"], ["options.only_path", "Allow paths", "text"],
    ["options.blocked_path", "Block paths", "text"],
  ];
  function field(key: string, label: string, type?: FormRow["type"]): FormRow {
    const name = key.replace(/^options\./, "");
    let value = key.startsWith("options.") ? options[name] : task[name];
    if (type === "text") value = Array.isArray(value) ? value.join(",") : value ?? "";
    return { key, label, value, type, error: typeof errors[name] === "string" ? errors[name] : undefined };
  }
  const rows: FormRow[] = task.command === "codescan" ? [field("target", "Source path", "text")] : Object.keys(task).length ? [
    ...fields.map(([key, label, type]) => field(key, label, type)),
    { key: "preset", label: "Action preset", value: presets.find((preset) =>
      Array.isArray(preset.actions) && preset.actions.length === allowed.length && preset.actions.every((action) => allowed.includes(String(action))))?.name ?? "Custom",
      choices: presets.map((preset) => preset.name) },
    ...allActions.map((action) => ({ key: `action.${action}`, label: `  ${action}`, value: allowed.includes(action) })),
    field("options.continuous", "Continuous execution"),
    ...(options.continuous ? [field("options.rounds_per_cycle", "Rounds per cycle", "number"),
      field("options.max_cycles", "Max cycles (0 = until cancelled)", "number")] : []),
    field("options.poc_verify", "Verify via PoC"), field("options.auto_report", "Auto report"), field("resume", "Resume target history"),
    field("prompt", "Additional instructions", "text"),
    ...["snapshot_id", "run_name", "resume_run_name", "runs_dir"].map((key) => field(key, key, "text")),
  ] : [{ key: "raw", label: "Task command", value: raw, type: "text", error: typeof errors.target === "string" ? errors.target : undefined }];
  rows.push({ key: "confirm", label: "Confirm execution (Ctrl+S)", action: () => { void validate(task, true); } },
    { key: "cancel", label: "Cancel", action: () => controller.dismiss() });
  return <FormPanel title="Task overview" theme={controller.theme} active={active} busy={busy} rows={rows} confirmKeys
    onChange={change} onSave={() => { void validate(task, true); }} onCancel={() => controller.dismiss()} />;
}
