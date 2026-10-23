import { mkdir, readFile, rename, unlink, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { randomUUID } from "node:crypto";
import { defaultLayout, normalizeLayout, type LayoutState, type View, type ViewId } from "../workbench/layout";
import type { SessionController } from "../session/controller";
import { record } from "../session/controller";
import type { ThemeId } from "../theme/palette";

export function clientDirectory(): string {
  const root = process.env.VULNCLAW_HOME ?? join(homedir(), ".vulnclaw");
  return join(root === "~" ? homedir() : root.replace(/^~[/\\]/, homedir() + "/"), "tui");
}
export async function writeJson(path: string, value: unknown): Promise<void> {
  const bytes = JSON.stringify(value, null, 2) + "\n";
  const temporary = `${path}.${randomUUID()}.tmp`;
  await mkdir(dirname(path), { recursive: true });
  try { await writeFile(temporary, bytes, { flag: "wx", mode: 0o600 }); await rename(temporary, path); }
  finally { await unlink(temporary).catch((error: NodeJS.ErrnoException) => { if (error.code !== "ENOENT") throw error; }); }
}
async function readJson(path: string): Promise<Record<string, unknown> | null> {
  try { return record(JSON.parse(await readFile(path, "utf-8"))); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return null; throw error; }
}
export async function loadPreferences(directory = clientDirectory()): Promise<{ theme: ThemeId; layout: LayoutState }> {
  const defaults = { format_version: 2, theme: "blue" as ThemeId, layout: defaultLayout() };
  const saved = await readJson(join(directory, "layout.json"));
  if (!saved) return defaults;
  if (saved.format_version !== 2) {
    // The design deliberately resets the retired layout and theme, while retaining session history.
    await writeJson(join(directory, "layout.json"), defaults); return defaults;
  }
  const raw = record(saved.layout);
  const views = (value: unknown): View[] => Array.isArray(value) ? value.map((item) => {
    const row = record(item);
    return { id: row.id as ViewId, collapsed: row.collapsed === true, expandedHeight: Number(row.expandedHeight ?? 0) };
  }) : [];
  return { theme: saved.theme === "orange" ? "orange" : "blue", layout: normalizeLayout({
    primaryWidth: Number(raw.primaryWidth), secondaryWidth: Number(raw.secondaryWidth),
    primary: views(raw.primary), secondary: views(raw.secondary), focus: raw.focus as ViewId,
  }) };
}
export async function restoreClientSession(controller: SessionController, directory = clientDirectory()): Promise<"plan" | "agent" | null> {
  const saved = await readJson(join(directory, "session.json"));
  if (!saved) return null;
  controller.history = Array.isArray(saved.history) ? saved.history.filter((item): item is string => typeof item === "string") : [];
  controller.historyIndex = controller.history.length;
  // Legacy permission/transcript/findings/messages fields never enter the backend or model.
  if (["Agent", "YOLO", "agent"].includes(String(saved.mode))) return "agent";
  return saved.mode === "Plan" || saved.mode === "plan" ? "plan" : null;
}
export async function saveClientSession(controller: SessionController, directory = clientDirectory()): Promise<void> {
  await writeJson(join(directory, "session.json"), { mode: controller.state?.execution_mode === "plan" ? "Plan" : "Agent", history: controller.history });
}
