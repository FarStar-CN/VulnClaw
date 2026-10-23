import { useSyncExternalStore } from "react";
import english from "../../../vulnclaw/i18n/en.json";
import chinese from "../../../vulnclaw/i18n/zh.json";

export type Language = "en" | "zh";
export function detectLanguage(env: Record<string, string | undefined> = process.env): Language {
  const override = env.VULNCLAW_LANG?.toLowerCase();
  if (override === "en" || override === "zh") return override;
  return env.LANG?.toLowerCase().startsWith("zh") ? "zh" : "en";
}
let language = detectLanguage();
const listeners = new Set<() => void>();
const catalogs: Record<Language, Record<string, string>> = { en: english, zh: chinese };
export function currentLanguage(): Language { return language; }
export function setLanguage(mode: unknown): void {
  const next = mode === "auto" ? detectLanguage() : mode;
  if ((next !== "en" && next !== "zh") || language === next) return;
  language = next;
  for (const listener of listeners) listener();
}
// English source messages are scoped to terminal.* in the shared Python catalogs.
// Call this only for interface copy, never arbitrary user or backend content.
export function t(message: string, values: Record<string, string | number> = {}): string {
  const key = "terminal." + message;
  const text = catalogs[language][key] ?? catalogs.en[key] ?? message;
  return text.replace(/\{(\w+)\}/g, (placeholder, name: string) => String(values[name] ?? placeholder));
}
function subscribe(listener: () => void): () => void {
  listeners.add(listener); return () => { listeners.delete(listener); };
}
export function useI18n(): typeof t {
  useSyncExternalStore(subscribe, currentLanguage);
  return t;
}

const statuses: Record<string, string> = {
  pending: "Pending", queued: "Queued", running: "Running", streaming: "Streaming",
  completed: "Completed", done: "Completed", cancelled: "Cancelled", failed: "Failed", error: "Error",
  interrupted: "Interrupted", stopped: "Stopped", no_path: "No viable path", needs_user: "Needs review",
  budget: "Budget exhausted", idle: "Idle", approved: "Approved", denied: "Denied", expired: "Expired",
  user: "You", assistant: "Assistant", tool: "Tool", reasoning: "Thinking",
  recon: "Recon", scan: "Scan", exploit: "Exploit", post_exploitation: "Post-exploitation",
  critical: "Critical", high: "High", medium: "Medium", low: "Low", info: "Info",
};
export function statusLabel(value: string): string {
  return language === "en" ? value : t(statuses[value.toLowerCase()] ?? value);
}
