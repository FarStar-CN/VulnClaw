import { useRef } from "react";
import { useKeyboard, usePaste, useRenderer, useTerminalDimensions } from "@opentui/react";
import type { SessionController } from "../session/controller";
import { pasteText } from "../platform/clipboard";
import { geometry, tuiChromeHeight, type WorkbenchState } from "../workbench/layout";

export function useSessionKeys(controller: SessionController, mode: "tui" | "repl", workbench?: WorkbenchState) {
  const renderer = useRenderer();
  const { width, height } = useTerminalDimensions();
  const tooSmall = workbench && geometry(width,
    Math.max(1, height - tuiChromeHeight(controller.composerRows, controller.completionRows)), workbench.layout).tooSmall;
  const lastInterrupt = useRef(0);
  usePaste((event) => {
    if (event.defaultPrevented || controller.approvals.length || controller.overlay || controller.inputFocused || tooSmall) return;
    event.preventDefault(); event.stopPropagation();
    controller.inputFocused = true;
    controller.draft += new TextDecoder().decode(event.bytes);
    controller.changed();
  });
  useKeyboard((key) => {
    if (key.defaultPrevented || controller.approvals.length) return;
    if (tooSmall && !controller.overlay && !(key.ctrl && key.name === "c")) {
      key.preventDefault(); key.stopPropagation(); return;
    }
    if ((key.ctrl && key.name === "v") || (key.shift && key.name === "insert")) {
      key.preventDefault(); key.stopPropagation();
      const editor = renderer.currentFocusedEditor;
      if (!editor && controller.overlay) return;
      void pasteText(renderer).then((text) => {
        if (controller.approvals.length) return;
        if (editor && renderer.currentFocusedEditor === editor) editor.insertText(text);
        else if (!controller.overlay) { controller.inputFocused = true; controller.draft += text; controller.changed(); }
      }).catch((error: unknown) => controller.notify(String(error)));
      return;
    }
    if (controller.overlay) return;
    if (controller.notice) { controller.notice = ""; controller.changed(); }
    if (key.ctrl && key.name === "c") {
      key.preventDefault(); key.stopPropagation();
      if (controller.state?.task.active) { controller.cancel(); lastInterrupt.current = 0; }
      else if (mode === "tui" || Date.now() - lastInterrupt.current < 3000) controller.onExit();
      else { lastInterrupt.current = Date.now(); controller.notify("Press Ctrl+C again to exit, or enter another request."); }
    } else if (key.ctrl && ["left", "right"].includes(key.name) && workbench) {
      key.preventDefault(); key.stopPropagation();
      controller.inputFocused = false; workbench.focus(key.name === "left"); controller.changed();
    } else if (key.name === "tab" && key.shift) {
      key.preventDefault(); key.stopPropagation(); controller.cyclePermission();
    } else if (key.name === "escape") {
      key.preventDefault();
      if (workbench?.gesture) workbench.cancel();
      else { controller.draft = ""; controller.inputFocused = true; }
      controller.changed();
    } else if (!controller.inputFocused && key.name === "return" && controller.draft) {
      key.preventDefault(); key.stopPropagation(); void controller.submit(controller.draft);
    } else if (!controller.inputFocused && !key.ctrl && !key.meta && key.sequence && !/[\x00-\x1f\x7f]/.test(key.sequence)) {
      key.preventDefault(); key.stopPropagation();
      controller.inputFocused = true; controller.draft += key.sequence; controller.changed();
    }
  });
}
