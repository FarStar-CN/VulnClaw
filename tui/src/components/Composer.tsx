import { useEffect, useRef, useState } from "react";
import { MouseButton, type TextareaRenderable } from "@opentui/core";
import { useKeyboard, useRenderer } from "@opentui/react";
import type { SessionController } from "../session/controller";
import { palettes } from "../theme/palette";
import { visibleText } from "./content";
import { completions, completionDescription } from "../session/completions";
import { pasteText } from "../platform/clipboard";
import { useI18n } from "../i18n";

export function Composer({ controller, active, framed = false }: { controller: SessionController; active: boolean; framed?: boolean }) {
  const t = useI18n();
  const editor = useRef<TextareaRenderable>(null);
  const syncedDraft = useRef(controller.draft);
  const renderer = useRenderer();
  const [selected, setSelected] = useState(0);
  const selectedRef = useRef(0);
  const cycle = useRef<{ candidates: string[]; selected: number; applied: string } | null>(null);
  const p = palettes[controller.theme];
  if (cycle.current && cycle.current.applied !== controller.draft) cycle.current = null;
  const candidates = cycle.current?.candidates ?? completions(controller);
  useEffect(() => {
    const rows = active && candidates.length ? Math.min(5, candidates.length) + 1 : 0;
    if (rows !== controller.completionRows) { controller.completionRows = rows; controller.changed(); }
  }, [active, candidates.length, controller]);
  const selection = Math.min(selected, Math.max(0, candidates.length - 1));
  useEffect(() => {
    // Native edit notifications can arrive after render; only write external draft changes back.
    if (editor.current && syncedDraft.current !== controller.draft) {
      syncedDraft.current = controller.draft;
      if (editor.current.plainText !== controller.draft) {
        editor.current.setText(controller.draft);
        editor.current.gotoBufferEnd();
      }
    }
    if (!cycle.current) { selectedRef.current = 0; setSelected(0); }
  }, [controller.draft]);
  useEffect(() => {
    const resize = () => {
      if (!editor.current || editor.current.isDestroyed) return;
      const rows = Math.max(1, Math.min(5, editor.current.editorView.getTotalVirtualLineCount()));
      if (rows !== controller.composerRows) { controller.composerRows = rows; controller.changed(); }
    };
    renderer.on("frame", resize);
    return () => { renderer.off("frame", resize); };
  }, [renderer, controller]);
  function apply(index: number, items = candidates) {
    const value = items[index]; if (!value) return;
    const applied = value + " ";
    cycle.current = { candidates: items, selected: index, applied };
    selectedRef.current = index; setSelected(index); controller.inputFocused = true; controller.draft = applied;
    syncedDraft.current = applied;
    editor.current?.setText(applied); editor.current?.gotoBufferEnd();
    controller.changed();
  }
  function submit() {
    const current = editor.current, text = current?.plainText ?? "";
    cycle.current = null; controller.draft = text; syncedDraft.current = text;
    void controller.submit(text);
    // Clear accepted input before deferred native edit notifications can restore it.
    if (current && !controller.draft) { syncedDraft.current = ""; current.setText(""); current.gotoBufferEnd(); }
  }
  useKeyboard((key) => {
    if (!active || key.defaultPrevented || controller.approvals.length || controller.overlay) return;
    if (cycle.current && cycle.current.applied !== controller.draft) cycle.current = null;
    const options = cycle.current?.candidates ?? completions(controller);
    const current = Math.min(selectedRef.current, Math.max(0, options.length - 1));
    if (key.name === "tab" && !key.shift) {
      key.preventDefault(); key.stopPropagation();
      if (options.length) apply(cycle.current ? (cycle.current.selected + 1) % options.length : current, options);
      return;
    }
    if (!controller.inputFocused) return;
    if (options.length && ["up", "down"].includes(key.name) && !key.ctrl) {
      key.preventDefault(); key.stopPropagation();
      const index = (current + (key.name === "up" ? -1 : 1) + options.length) % options.length;
      selectedRef.current = index; setSelected(index); if (cycle.current) cycle.current.selected = index;
    }
    if (key.name === "return" && !key.shift && options.length && options[current] !== controller.draft.trim()) {
      key.preventDefault(); key.stopPropagation(); apply(current, options);
    }
  });
  const begin = Math.max(0, selection - 4);
  const showMenu = active && candidates.length > 0;
  return <box id="composer-pane" backgroundColor={p.plate}
    flexDirection="column" flexShrink={0} onMouseDown={() => { controller.inputFocused = true; controller.changed(); }}>
    {showMenu && <box id="completion-menu" flexDirection="column" backgroundColor={p.panel}>
      <text fg={p.accent}>{t("Commands · {selected}/{count}", { selected: selection + 1, count: candidates.length })}</text>
      {candidates.slice(begin, begin + 5).map((candidate) => <text key={candidate}
        fg={candidate === candidates[selection] ? p.action : p.muted}
        bg={candidate === candidates[selection] ? p.plate : p.panel}
        onMouseDown={(event) => { event.stopPropagation(); apply(candidates.indexOf(candidate)); }}>
        {visibleText(`${candidate === candidates[selection] ? "›" : " "} ${candidate}  ${completionDescription(controller, candidate)}`)}
      </text>)}
    </box>}
    <box id="composer-input" border={framed ? true : ["top", "bottom"]} borderColor={p.composerBorder}
      flexDirection="row" height={controller.composerRows + 2} flexShrink={0}>
      <text fg={p.action}>{" > "}</text>
      <box flexGrow={1} minWidth={0} height={controller.composerRows}
        onMouseDown={(event) => {
          if (!active || event.button !== MouseButton.RIGHT) return;
          event.preventDefault(); event.stopPropagation();
          controller.inputFocused = true; controller.changed();
          void pasteText(renderer).then((text) => {
            if (!controller.overlay && !controller.approvals.length && editor.current && !editor.current.isDestroyed) editor.current.insertText(text);
          }).catch((error: unknown) => controller.notify(String(error)));
        }}>
        <textarea id="composer-editor" ref={editor} focused={active && controller.inputFocused} flexGrow={1}
          height={controller.composerRows} wrapMode="word" initialValue={controller.draft}
          backgroundColor={p.plate} textColor={p.text}
          keyBindings={[{ name: "return", action: "submit" }, { name: "return", shift: true, action: "newline" }]}
          onContentChange={() => {
            const current = editor.current;
            if (!current || current.isDestroyed || current.plainText === controller.draft) return;
            cycle.current = null; selectedRef.current = 0; setSelected(0);
            controller.draft = current.plainText; syncedDraft.current = current.plainText; controller.changed();
          }} onSubmit={submit} />
        {/* Native textarea placeholder redraws can split UTF-8 characters; use a text node. */}
        {!controller.draft && <text id="composer-placeholder" position="absolute" top={0} left={0} width="100%"
          height={1} wrapMode="none" selectable={false} fg={p.hint}>
          {t("Ask a question, / for commands, @ for skills")}
        </text>}
      </box>
    </box>
  </box>;
}
