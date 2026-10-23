import { SyntaxStyle, type CliRenderer } from "@opentui/core";

export const palettes = {
  blue: { accent: "#9ab8ff", border: "#446a98", text: "#e1e5ec", muted: "#a1a9b6", background: "#181a1f", error: "#f87171" },
  orange: { accent: "#f2ba8e", border: "#9c6b43", text: "#e1e5ec", muted: "#a1a9b6", background: "#181a1f", error: "#f87171" },
} as const;
export type ThemeId = keyof typeof palettes;

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
