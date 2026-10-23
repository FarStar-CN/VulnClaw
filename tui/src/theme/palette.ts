import { SyntaxStyle, type CliRenderer } from "@opentui/core";

const shared = {
  text: "#e1e5ec", soft: "#c2c8d2", muted: "#a1a9b6", hint: "#7c8593",
  background: "#181a1f", panel: "#202329", plate: "#2a2e36", dock: "#363d49",
  error: "#f87171", warning: "#fb923c", signal: "#fbbf24", success: "#7dcb9a", low: "#67d4ce",
};
export const palettes = {
  blue: { ...shared, action: "#7aa2f7", accent: "#9ab8ff", border: "#446a98" },
  orange: { ...shared, action: "#e9a06b", accent: "#f2ba8e", border: "#9c6b43" },
} as const;
export type ThemeId = keyof typeof palettes;

export const permissions = {
  read_only: { label: "Read-Only", color: "#67d4ce" },
  ask: { label: "Ask", color: "#fbbf24" },
  auto_review: { label: "Auto-review", color: "#9ab8ff" },
  full_access: { label: "Full access", color: "#f87171" },
} as const;
export type Permission = keyof typeof permissions;
export const permissionOrder = Object.keys(permissions) as Permission[];
export function permissionInfo(value: string) { return permissions[value as Permission] ?? permissions.ask; }
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
    string: { fg: "#a3d99b" },
    comment: { fg: p.muted, italic: true },
  }, renderer.nativeScene);
}
