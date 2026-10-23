import { useEffect, useRef, useState } from "react";
import { type TextareaRenderable } from "@opentui/core";
import { useKeyboard } from "@opentui/react";
import type { SessionController } from "../session/controller";
import { palettes } from "../theme/palette";
import { visibleText } from "./content";
import { completions } from "../session/completions";

export function Composer({ controller, active }: { controller: SessionController; active: boolean }) {
  const editor = useRef<TextareaRenderable>(null);
  const [selected, setSelected] = useState(0);
  const p = palettes[controller.theme];
  const query = controller.draft.trimStart();
  const candidates = completions(controller);
  useEffect(() => {
    if (editor.current && editor.current.plainText !== controller.draft) editor.current.setText(controller.draft);
  }, [controller.draft]);
  useEffect(() => { setSelected(0); }, [query]);
  useKeyboard((key) => {
    if (!active || controller.approvals.length || controller.overlay) return;
    if (candidates.length && ["up", "down"].includes(key.name)) {
      key.preventDefault(); key.stopPropagation();
      setSelected((index) => (index + (key.name === "up" ? -1 : 1) + candidates.length) % candidates.length);
    }
    if (key.name === "return" && !key.shift && candidates.length && candidates[selected] !== query) {
      key.preventDefault(); key.stopPropagation();
      controller.draft = candidates[selected] + " "; controller.changed();
    }
  });
  return <box id="composer-pane" border borderColor={p.accent} flexDirection="column" flexShrink={0}>
    {active && candidates.length > 0 && <box flexDirection="column" maxHeight={5}>
      {candidates.slice(Math.max(0, selected - 4), Math.max(5, selected + 1)).map((candidate) =>
        <text key={candidate} fg={candidate === candidates[selected] ? p.accent : p.muted}>{visibleText(candidate)}</text>)}
    </box>}
    <textarea id="composer-editor" ref={editor} focused={active} height={Math.min(5, Math.max(2, controller.draft.split("\n").length))}
      initialValue={controller.draft} placeholder="Ask a question or /help · Shift+Enter for a new line"
      backgroundColor={p.background} textColor={p.text}
      keyBindings={[{ name: "return", action: "submit" }, { name: "return", shift: true, action: "newline" }]}
      onContentChange={() => {
        const current = editor.current;
        if (!current || current.isDestroyed || current.plainText === controller.draft) return;
        controller.draft = current.plainText; controller.changed();
      }} onSubmit={() => { void controller.submit(editor.current?.plainText ?? ""); }} />
  </box>;
}
