import { useRef } from "react";
import { useKeyboard, useRenderer } from "@opentui/react";
import type { SessionController } from "../session/controller";
import { copyText, pasteText } from "../platform/clipboard";
import { paneText } from "../platform/pane-copy";
import { restoreClientSession, saveClientSession } from "../platform/preferences";
import type { WorkbenchState } from "../workbench/layout";

export function useSessionKeys(controller: SessionController, mode: "tui" | "repl", workbench?: WorkbenchState) {
  const renderer = useRenderer();
  const lastInterrupt = useRef(0);
  useKeyboard((key) => {
    if (key.defaultPrevented || controller.approvals.length) return;
    if ((key.ctrl && key.name === "v") || (key.shift && key.name === "insert")) {
      key.preventDefault(); key.stopPropagation();
      const editor = renderer.currentFocusedEditor;
      if (editor) void pasteText(renderer).then((text) => {
        // An approval arriving during an OS clipboard read owns input immediately.
        if (!controller.approvals.length && renderer.currentFocusedEditor === editor) editor.insertText(text);
      }).catch((error: unknown) => controller.notify(String(error)));
      return;
    }
    if (key.ctrl && key.name === "y") {
      key.preventDefault(); key.stopPropagation();
      const id = controller.overlay ? "reader-pane" : mode === "repl" ? "transcript-scroll" : `${workbench?.layout.focus ?? "output"}-pane`;
      const pane = renderer.root.findDescendantById(id) ?? renderer.root.findDescendantById("form-pane");
      if (pane) void copyText(renderer, paneText(renderer, { x: pane.x, y: pane.y, width: pane.width, height: pane.height }))
        .then((message) => controller.notify(message)).catch((error: unknown) => controller.notify(String(error)));
      return;
    }
    if (controller.overlay) {
      if (controller.overlay.kind === "chain" && key.name === "f5") { key.preventDefault(); controller.dismiss(); }
      return;
    }
    if (key.ctrl && key.name === "c") {
      key.preventDefault(); key.stopPropagation();
      if (controller.state?.task.active) { controller.cancel(); lastInterrupt.current = 0; }
      else if (mode === "tui" || Date.now() - lastInterrupt.current < 3000) controller.onExit();
      else { lastInterrupt.current = Date.now(); controller.notify("Press Ctrl+C again to exit, or enter another request."); }
    } else if (key.ctrl && key.name === "s") {
      key.preventDefault(); void saveClientSession(controller).then(() => controller.notify("Session preferences saved")).catch((error: unknown) => controller.notify(String(error)));
    } else if (key.ctrl && key.name === "r") {
      key.preventDefault();
      if (controller.state?.task.active) { controller.notify("Restore requires an idle session"); return; }
      void restoreClientSession(controller).then((mode) => {
        if (mode) controller.action("session.mode.set", { mode });
        controller.draft = ""; controller.changed();
      }).catch((error: unknown) => controller.notify(String(error)));
    } else if (key.ctrl && key.name === "t") { key.preventDefault(); controller.showReasoning = !controller.showReasoning; controller.changed(); }
    else if (key.ctrl && ["p", "n"].includes(key.name)) { key.preventDefault(); controller.recall(key.name === "p"); }
    else if (key.ctrl && ["left", "right"].includes(key.name) && workbench) { key.preventDefault(); workbench.focus(key.name === "left"); controller.changed(); }
    else if (key.name === "tab") {
      key.preventDefault();
      if (key.shift) {
        const permissions = ["ask", "auto_review", "full_access"];
        controller.action("session.permission.set", { mode: permissions[(permissions.indexOf(controller.permission) + 1) % permissions.length] });
      } else controller.action("session.mode.set", { mode: controller.state?.execution_mode === "plan" ? "agent" : "plan" });
    } else if (key.name === "f5") { key.preventDefault(); controller.overlay = { kind: "chain" }; controller.changed(); }
    else if (key.name === "escape") {
      key.preventDefault(); if (workbench?.gesture) workbench.cancel(); else controller.draft = ""; controller.changed();
    }
  });
}
