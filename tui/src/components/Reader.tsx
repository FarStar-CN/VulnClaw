import { useState } from "react";
import { useKeyboard, useRenderer } from "@opentui/react";
import type { SessionController } from "../session/controller";
import { MarkdownBody } from "./MarkdownBody";
import { codeBlocks } from "./code-blocks";
import { copyText } from "../platform/clipboard";
import { palettes } from "../theme/palette";
import { visibleText } from "./content";

export function Reader({ controller, title, text, format, active, width, messageId }: {
  controller: SessionController; title: string; text: string; format: "markdown" | "text";
  active: boolean; width: number; messageId?: string;
}) {
  const renderer = useRenderer();
  const [raw, setRaw] = useState(false);
  const [code, setCode] = useState(0);
  const blocks = format === "markdown" ? codeBlocks(text) : [];
  const p = palettes[controller.theme];
  function copy(value: string) {
    void copyText(renderer, value).then((message) => controller.notify(message)).catch((error: unknown) => controller.notify(String(error)));
  }
  useKeyboard((key) => {
    if (!active || key.defaultPrevented) return;
    if (key.name === "escape") { controller.dismiss(); key.preventDefault(); }
    if (!key.ctrl && key.name === "v") { setRaw((value) => !value); key.preventDefault(); }
    if (!key.ctrl && key.name === "m") { copy(text); key.preventDefault(); }
    if (!key.ctrl && key.name === "c" && blocks.length) { copy(blocks[code]); key.preventDefault(); }
    if (key.name === "[" && blocks.length) { setCode((value) => (value + blocks.length - 1) % blocks.length); key.preventDefault(); }
    if (key.name === "]" && blocks.length) { setCode((value) => (value + 1) % blocks.length); key.preventDefault(); }
  });
  return <box id="reader-pane" title={visibleText(title)} border borderColor={p.accent} flexDirection="column" flexGrow={1} minHeight={0}>
    <scrollbox id="reader-scroll" flexGrow={1} scrollX={false} focused={active}>
      {format === "markdown" && !raw ? <MarkdownBody id={`reader-${messageId ?? "document"}`} text={text} width={width - 4} theme={controller.theme} />
        : <text wrapMode="word">{visibleText(text)}</text>}
    </scrollbox>
    <text fg={p.muted}>{`V raw/render · M copy source${blocks.length ? ` · C copy code ${code + 1}/${blocks.length} · [ ] choose code` : ""} · Esc close`}</text>
  </box>;
}
