import { mkdir, readFile, rename, unlink, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { randomUUID } from "node:crypto";
import { YAML } from "bun";
import { defaultLayout, normalizeLayout, type LayoutState, type View, type ViewId } from "../workbench/layout";
import type { SessionController } from "../session/controller";
import { record } from "../session/controller";
import { defaultThemeConfig, parseThemes, type Palette, type ThemeConfig } from "../theme/palette";

export function clientDirectory(): string {
  const root = process.env.VULNCLAW_HOME ?? join(homedir(), ".vulnclaw");
  return join(root === "~" ? homedir() : root.replace(/^~[/\\]/, homedir() + "/"), "tui");
}
export function configurationFile(env: Record<string, string | undefined> = process.env, home = homedir()): string {
  return join(env.VULNCLAW_CONFIG_DIR ?? join(home, ".vulnclaw"), "config.yaml");
}
export async function loadStartupAppearance(path = configurationFile(), directory = clientDirectory()): Promise<{
  terminal: ThemeConfig; migration: ThemeConfig | null; language: "auto" | "en" | "zh";
}> {
  let config: unknown = {};
  try { config = YAML.parse(await readFile(path, "utf-8")) ?? {}; }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
  if (typeof config !== "object" || config === null || Array.isArray(config)) throw new Error("config must be an object");
  const language = record(record(config).session).language ?? "auto";
  if (language !== "auto" && language !== "en" && language !== "zh") throw new Error("session.language must be auto, en or zh");
  if (Object.hasOwn(config, "terminal")) {
    const terminal = (config as Record<string, unknown>).terminal;
    if (typeof terminal !== "object" || terminal === null || Array.isArray(terminal)) throw new Error("terminal must be an object");
    return { terminal: parseThemes({ ...defaultThemeConfig, ...terminal }), migration: null, language };
  }
  const migration = await legacyThemeConfig(directory);
  return { terminal: migration ?? defaultThemeConfig, migration, language };
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
export async function legacyThemeConfig(directory = clientDirectory()): Promise<ThemeConfig | null> {
  const saved = await readJson(join(directory, "themes.json"));
  const layout = await readJson(join(directory, "layout.json"));
  if (!saved && !(layout?.format_version === 2 && typeof layout.theme === "string")) return null;
  const blue = defaultThemeConfig.themes.blue;
  const builtinOverrides = Object.fromEntries(Object.entries(defaultThemeConfig.themes).map(([name, palette]) =>
    [name, Object.fromEntries(Object.entries(palette).filter(([key, color]) => blue[key as keyof Palette] !== color))]));
  const definitions = { ...builtinOverrides, ...record(saved?.themes) };
  const themes = Object.fromEntries(Object.entries(definitions).map(([name, overrides]) =>
    [name, { ...blue, ...record(saved?.shared), ...builtinOverrides[name], ...record(overrides) }]));
  const selected = layout?.format_version === 2 && typeof layout.theme === "string" && Object.hasOwn(themes, layout.theme)
    ? layout.theme : defaultThemeConfig.theme;
  return parseThemes({ theme: selected, themes });
}
export async function loadPreferences(directory = clientDirectory()): Promise<{ layout: LayoutState }> {
  const defaults = { format_version: 2, layout: defaultLayout() };
  const saved = await readJson(join(directory, "layout.json"));
  if (!saved) return defaults;
  if (saved.format_version !== 2) {
    // The design deliberately resets the retired layout and theme, while retaining session history.
    await writeJson(join(directory, "layout.json"), defaults); return defaults;
  }
  const raw = record(saved.layout);
  const views = (value: unknown): View[] => Array.isArray(value) ? value.map((item) => {
    const row = record(item);
    return { id: row.id as ViewId, collapsed: row.collapsed === true, expandedHeight: Number(row.expandedHeight ?? 0),
      ...(row.id === "capabilities" ? { mcpExpanded: row.mcpExpanded === true } : {}) };
  }) : [];
  return { layout: normalizeLayout({
    primaryWidth: Number(raw.primaryWidth), secondaryWidth: Number(raw.secondaryWidth),
    primary: views(raw.primary), secondary: views(raw.secondary), focus: raw.focus as ViewId,
  }) };
}
export async function restoreClientSession(controller: SessionController, directory = clientDirectory()): Promise<void> {
  const saved = await readJson(join(directory, "session.json"));
  if (!saved) return;
  controller.history = Array.isArray(saved.history) ? saved.history.filter((item): item is string => typeof item === "string") : [];
  // Legacy mode/permission/transcript/findings/messages never enter the backend or model.
}
export async function saveClientSession(controller: SessionController, directory = clientDirectory()): Promise<void> {
  await writeJson(join(directory, "session.json"), { history: controller.history });
}
