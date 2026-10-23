import { record } from "../session/controller";

export const settingCategories = [
  ["general", "General"], ["model", "Model"], ["themes", "Themes"], ["execution", "Execution"],
  ["safety", "Approval & tools"], ["mcp", "MCP servers"], ["recon", "Recon"],
  ["advanced", "Advanced"],
] as const;
export type SettingCategory = typeof settingCategories[number][0];
export type ConfigField = {
  key: string; label: string; schema: Record<string, unknown>; category: SettingCategory;
  secret: boolean; choices?: unknown[];
};

export const connectionFields = [
  ["provider", "Provider template"], ["website_url", "Website URL"],
  ["base_url", "API request URL"], ["api_key", "API key"], ["model", "Model"],
] as const;

const general = new Set(["language", "show_thinking", "auto_save", "output_dir", "runs_dir", "report_format"]);
const labels: Record<string, string> = {
  "session.language": "Language", "session.show_thinking": "Show reasoning",
  "session.auto_save": "Auto-save session", "session.output_dir": "Output directory",
  "session.runs_dir": "Run directory", "session.report_format": "Report format",
  "session.engine": "Engine", "session.solve_max_steps": "Step limit",
  "session.solve_max_tool_rounds": "Tool follow-up limit", "session.solve_max_parallel": "Parallel workers",
  "session.max_rounds": "Round limit", "session.persistent_rounds_per_cycle": "Rounds per cycle",
  "session.persistent_max_cycles": "Cycle limit",
  "session.persistent_auto_report": "Report after each cycle",
  "session.solve_auto_report": "Replay report on finish", "session.conversation_auto_report": "Security report on finish",
  "session.solve_report_show": "Display replay report", "session.poc_verify_enabled": "Verify via PoC",
  "safety.permission_mode": "Approval policy", "safety.approval_timeout_seconds": "Approval timeout (s)",
  "safety.trusted_commands": "Trusted command prefixes", "safety.tool_max_concurrent": "Concurrent tools",
  "safety.tool_parallel": "Parallel tool calls", "safety.enable_python_execute": "Python execution tool",
  "safety.python_execute_max_lines": "Python line limit", "safety.python_execute_max_output_chars": "Python output limit",
  "llm.api_keys": "API key pool", "llm.max_tokens": "Response token limit",
  "llm.max_context_tokens": "Context window", "llm.temperature": "Temperature",
  "llm.reasoning_effort": "Reasoning effort", "llm.auth_mode": "Authentication",
  "subagent.enabled": "Enable subagents", "subagent.max_background_groups": "Background groups",
  "subagent.max_concurrent_leaf_total": "Concurrent workers", "subagent.max_concurrent_leaf_per_group": "Workers per group",
  "subagent.max_leaf_per_group": "Worker limit per group", "subagent.max_waves_per_group": "Waves per group",
  "subagent.max_steps_per_leaf": "Steps per worker", "subagent.leaf_max_tool_rounds": "Worker tool follow-ups",
  "subagent.leaf_timeout_seconds": "Worker timeout (s)", "subagent.group_timeout_seconds": "Group timeout (s)",
  "subagent.finalization_timeout_seconds": "Finalization timeout (s)",
  "subagent.max_model_tokens_per_solve": "Tokens per solve", "subagent.max_model_tokens_per_group": "Tokens per group",
  "subagent.merge_max_evidence_per_group": "Evidence per group", "subagent.result_max_chars": "Result character limit",
  "session.context_hot_max_messages": "Recent message limit", "session.context_hot_max_tokens": "Recent token limit",
  "session.memory_search_max_chars": "Memory search limit", "session.memory_archive_max_bytes": "Archive size (bytes)",
  "session.memory_archive_max_files": "Archive file limit", "session.context_auto_compact": "Automatic compaction",
  "session.context_compact_trigger_ratio": "Compaction trigger ratio", "session.context_compact_target_ratio": "Compaction target ratio",
  "session.context_recent_message_groups": "Recent groups to retain", "session.context_summary_max_tokens": "Summary token limit",
  "session.context_output_reserve_tokens": "Reserved response tokens", "session.context_compaction_mode": "Compaction mode",
  "session.context_compaction_audit_enabled": "Record compaction audit",
  "recon.fofa_email": "FOFA email", "recon.fofa_key": "FOFA API key", "recon.hunter_key": "Hunter API key",
  "recon.quake_key": "Quake API key", "recon.zoomeye_key": "ZoomEye API key",
  "recon.shodan_key": "Shodan API key", "recon.zerozone_key": "0.zone API key",
};
const choices: Record<string, unknown[]> = {
  "session.language": ["auto", "zh", "en"],
  "session.report_format": ["markdown", "html"], "llm.auth_mode": ["static", "oauth"],
};

export function valueAt(value: Record<string, unknown>, path: string): unknown {
  return path.split(".").reduce<unknown>((node, key) => record(node)[key], value);
}
export function withValue(value: Record<string, unknown>, path: string, next: unknown): Record<string, unknown> {
  const result = structuredClone(value);
  const parts = path.split(".");
  let node = result;
  for (const part of parts.slice(0, -1)) {
    node[part] = { ...record(node[part]) };
    node = record(node[part]);
  }
  node[parts.at(-1)!] = next;
  return result;
}
export function sameValue(a: unknown, b: unknown): boolean { return JSON.stringify(a) === JSON.stringify(b); }

export function editableConfig(document: Record<string, unknown>): Record<string, unknown> {
  let config = structuredClone(record(document.config));
  for (const path of Array.isArray(document.secret_paths) ? document.secret_paths.map(String) : []) {
    const value = valueAt(config, path);
    if (Array.isArray(value)) config = withValue(config, path, value.map((item, index) => item === "***" ? { $keep: index } : item));
  }
  return config;
}
export function validationConfig(draft: Record<string, unknown>, document: Record<string, unknown>): Record<string, unknown> {
  let config = draft;
  for (const path of Array.isArray(document.secret_paths) ? document.secret_paths.map(String) : []) {
    const value = valueAt(config, path);
    if (Array.isArray(value)) config = withValue(config, path, value.map((item) => typeof record(item).$keep === "number" ? "***" : item));
  }
  return config;
}

export function resolveSchema(schema: unknown, root: Record<string, unknown>): Record<string, unknown> {
  const source = record(schema);
  if (typeof source.$ref === "string" && source.$ref.startsWith("#/$defs/")) {
    return { ...record(record(root.$defs)[source.$ref.slice(8)]), ...source };
  }
  const variants = Array.isArray(source.anyOf) ? source.anyOf.map(record) : [];
  const concrete = variants.find((item) => item.type !== "null");
  return concrete ? { ...resolveSchema(concrete, root), ...source } : source;
}
export function schemaAt(root: Record<string, unknown>, path: string): Record<string, unknown> {
  let schema = root;
  for (const part of path.split(".")) schema = resolveSchema(record(schema.properties)[part], root);
  return schema;
}
export function fieldLabel(key: string): string {
  return labels[key] ?? key.split(".").at(-1)!.replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase());
}
export function configFields(document: Record<string, unknown>, config: Record<string, unknown>): ConfigField[] {
  const root = record(document.schema);
  const secrets = new Set(Array.isArray(document.secret_paths) ? document.secret_paths.map(String) : []);
  const engine = String(record(config.session).engine ?? "solve");
  const result: ConfigField[] = [];
  for (const [group, values] of Object.entries(config)) {
    if (group === "mcp" || group === "terminal") continue;
    for (const name of Object.keys(record(values))) {
      const key = group + "." + name;
      const schema = schemaAt(root, key);
      if (name.startsWith("tui_scope_")) continue;
      if (/deprecated/i.test(String(schema.description ?? "")) && name !== "solve_max_parallel") continue;
      if (group === "session" && (
        (["solve_max_steps", "solve_max_tool_rounds", "solve_auto_report", "solve_report_show"].includes(name) && engine === "rounds") ||
        (name === "solve_max_parallel" && engine !== "team") ||
        ((name === "max_rounds" || name.startsWith("repl_parallel_")) && engine !== "rounds")
      )) continue;
      const category: SettingCategory = group === "session"
        ? general.has(name) ? "general"
          : name === "engine" || name.startsWith("solve_") || name.startsWith("persistent_") ||
            name.startsWith("repl_parallel_") || ["max_rounds", "poc_verify_enabled", "conversation_auto_report"].includes(name)
            ? "execution" : "advanced"
        : group === "llm" ? "model" : group === "subagent" ? "execution"
          : group === "safety" ? "safety" : group === "recon" ? "recon" : "advanced";
      result.push({ key, label: fieldLabel(key), schema, category, secret: secrets.has(key),
        choices: Array.isArray(schema.enum) ? schema.enum : choices[key] });
    }
  }
  return result;
}
export function configChanges(baseline: Record<string, unknown>, draft: Record<string, unknown>): Record<string, unknown> {
  const changes: Record<string, unknown> = {};
  for (const [group, values] of Object.entries(draft)) {
    if (group === "mcp") {
      if (!sameValue(record(baseline.mcp).servers, record(values).servers)) changes["mcp.servers"] = record(values).servers;
    } else for (const [name, value] of Object.entries(record(values))) {
      const key = group + "." + name;
      if (!sameValue(valueAt(baseline, key), value)) changes[key] = value;
    }
  }
  return changes;
}
export function parseField(text: string, schema: Record<string, unknown>, previous: unknown): unknown {
  const variants = Array.isArray(schema.anyOf) ? schema.anyOf.map(record) : [];
  if (!text.trim() && variants.some((item) => item.type === "null")) return null;
  const numeric = schema.type === "integer" || schema.type === "number" || typeof previous === "number";
  if (numeric) {
    const value = Number(text);
    if (!text.trim() || !Number.isFinite(value)) throw new Error("Enter a valid number.");
    if (schema.type === "integer" && !Number.isInteger(value)) throw new Error("Enter a whole number.");
    for (const [limit, invalid, sign] of [
      ["minimum", value < Number(schema.minimum), "at least "],
      ["maximum", value > Number(schema.maximum), "at most "],
      ["exclusiveMinimum", value <= Number(schema.exclusiveMinimum), "greater than "],
      ["exclusiveMaximum", value >= Number(schema.exclusiveMaximum), "less than "],
    ] as const) if (schema[limit] !== undefined && invalid) throw new Error("Value must be " + sign + schema[limit] + ".");
    return value;
  }
  if (typeof schema.minLength === "number" && text.length < schema.minLength) throw new Error("Enter at least " + schema.minLength + " characters.");
  if (typeof schema.maxLength === "number" && text.length > schema.maxLength) throw new Error("Use at most " + schema.maxLength + " characters.");
  if (typeof schema.pattern === "string" && !new RegExp(schema.pattern).test(text)) throw new Error("Value must match " + schema.pattern);
  return text;
}
