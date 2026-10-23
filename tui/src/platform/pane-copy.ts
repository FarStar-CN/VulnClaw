import { type CliRenderer } from "@opentui/core";
import stringWidth from "string-width";

export interface PaneRect { x: number; y: number; width: number; height: number }

/** Copy the last presented pane, including wrapping and borders. */
export function paneText(renderer: CliRenderer, rect: PaneRect): string {
  const frame = renderer.currentRenderBuffer;
  const x = Math.max(0, Math.floor(rect.x));
  const y = Math.max(0, Math.floor(rect.y));
  const width = Math.min(Math.floor(rect.width), frame.width - x);
  const height = Math.min(Math.floor(rect.height), frame.height - y);
  if (width <= 0 || height <= 0) return "";
  const rows = new TextDecoder().decode(frame.getRealCharBytes(true)).split("\n");
  const segmenter = new Intl.Segmenter(undefined, { granularity: "grapheme" });
  return rows.slice(y, y + height).map((row) => {
    let column = 0;
    let selected = "";
    for (const { segment } of segmenter.segment(row)) {
      const cells = stringWidth(segment);
      const right = column + cells;
      if (column >= x && right <= x + width) selected += segment;
      else if (right > x && column < x + width) {
        selected += " ".repeat(Math.min(right, x + width) - Math.max(column, x));
      }
      column = right;
      if (column >= x + width) break;
    }
    return selected;
  }).join("\n");
}
