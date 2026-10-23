import { SyntaxStyle, type CliRenderer } from "@opentui/core";
import defaults from "../../../vulnclaw/config/terminal_themes.json";

export type Palette = { [Key in keyof typeof defaults.themes.blue]: string };
export type ThemeId = string;
export interface ThemeConfig { theme: ThemeId; themes: Record<ThemeId, Palette> }
export const defaultThemeConfig = defaults;

function object(value: unknown, path: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${path} must be an object`);
  return value as Record<string, unknown>;
}
function colors(value: unknown, path: string): Palette {
  const result: Partial<Palette> = {};
  for (const [key, color] of Object.entries(object(value, path))) {
    if (!Object.hasOwn(defaults.themes.blue, key)) throw new Error(`Unknown color: ${path}.${key}`);
    if (typeof color !== "string" || !/^#[0-9a-f]{6}$/i.test(color)) {
      throw new Error(`${path}.${key} must be a #RRGGBB color`);
    }
    result[key as keyof Palette] = color;
  }
  for (const key of Object.keys(defaults.themes.blue)) {
    if (!Object.hasOwn(result, key)) throw new Error(`Missing color: ${path}.${key}`);
  }
  return result as Palette;
}
export function parseThemes(value: unknown): ThemeConfig {
  const config = object(value, "terminal");
  for (const key of Object.keys(config)) {
    if (key !== "theme" && key !== "themes") throw new Error(`Unknown theme configuration field: ${key}`);
  }
  const themes = Object.fromEntries(Object.entries(object(config.themes, "terminal.themes")).map(([name, palette]) => {
    if (!/^[a-z][a-z0-9_-]*$/.test(name)) throw new Error(`Invalid theme name: ${name}; use lowercase letters, digits, - or _`);
    return [name, colors(palette, `terminal.themes.${name}`)];
  }));
  if (typeof config.theme !== "string" || !Object.hasOwn(themes, config.theme)) throw new Error(`Unknown terminal theme: ${String(config.theme)}`);
  return { theme: config.theme, themes };
}

export let palettes = parseThemes(defaults).themes;
const listeners = new Set<() => void>();
export function subscribeThemes(listener: () => void): () => void {
  listeners.add(listener); return () => { listeners.delete(listener); };
}
export function configureThemes(value: unknown): ThemeConfig {
  const config = parseThemes(value); palettes = config.themes;
  for (const listener of listeners) listener();
  return config;
}

export const permissions = {
  read_only: { label: "Read-Only", colorKey: "permissionReadOnly" },
  ask: { label: "Ask", colorKey: "permissionAsk" },
  auto_review: { label: "Auto-review", colorKey: "permissionAutoReview" },
  full_access: { label: "Full access", colorKey: "permissionFullAccess" },
} as const satisfies Record<string, { label: string; colorKey: keyof Palette }>;
export type Permission = keyof typeof permissions;
export const permissionOrder = Object.keys(permissions) as Permission[];
export function permissionInfo(value: string, theme: ThemeId) {
  const policy = permissions[value as Permission] ?? permissions.ask;
  return { label: policy.label, color: palettes[theme][policy.colorKey] };
}
export function severityColor(value: string, theme: ThemeId): string {
  const p = palettes[theme];
  return ({ critical: p.error, high: p.warning, medium: p.signal, low: p.low } as Record<string, string>)[value.toLowerCase()] ?? p.hint;
}

export function markdownStyle(renderer: CliRenderer, theme: ThemeId): SyntaxStyle {
  const p = palettes[theme];
  return SyntaxStyle.fromStyles({
    default: { fg: p.text },
    "markup.heading": { fg: p.accent, bold: true },
    "markup.strong": { bold: true },
    "markup.italic": { italic: true },
    "markup.link": { fg: p.accent, underline: true },
    "markup.raw": { fg: p.accent },
    "markup.quote": { fg: p.muted, italic: true },
    keyword: { fg: p.accent, bold: true },
    string: { fg: p.codeString },
    comment: { fg: p.muted, italic: true },
  }, renderer.nativeScene);
}
