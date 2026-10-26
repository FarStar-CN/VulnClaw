import { createCliRenderer, destroyTreeSitterClient, NativeSession, type ScrollBoxRenderable } from "@opentui/core";
import { createRoot } from "@opentui/react";
import { SessionController } from "../../src/session/controller";
import { ReplShell } from "../../src/shells/ReplShell";
import { syncReplBackground } from "../../src/platform/repl-background";
import { defaultThemeConfig } from "../../src/theme/palette";

const controller = new SessionController(); controller.connected = true;
const themes = {
  blue: { ...defaultThemeConfig.themes.blue, background: "#234567", transcript: "#aabbcc" },
  orange: { ...defaultThemeConfig.themes.orange, background: "#654321", transcript: "#ddeeff" },
};
controller.applyConfigDocument({ config: { terminal: { theme: "blue", themes } } });
const history = process.env.REFLOW_HISTORY === "1";
const message = { protocol_version: 2, type: "message", task_id: "fixture", source: "main",
  role: "assistant", format: "text" } as const;
if (history) {
  controller.draft = "draft 中文";
  controller.receive({ ...message, message_id: "history", operation: "append",
    text: "HISTORY-BEGIN\n" + Array.from({ length: 18 }, (_, i) =>
      `history-${i}: 中文 ${"abcd".repeat(25)}`).join("\n") + "\nHISTORY-END" });
  controller.receive({ ...message, message_id: "history", operation: "end", status: "completed" });
} else controller.runtime = {
  provider: "fixture", model: "fixture", config_ready: false, mcp_started: 3, skills: [],
  mcp_servers: [0, 1, 2, 3].map((i) => ({
    name: `fixture-${i}`, ready: i < 3, status: i < 3 ? "ready" : "starting",
  })),
};
const output = new NativeSession(process.stdout);
const renderer = await createCliRenderer({ nativeSession: output, screenMode: "alternate-screen",
  exitOnCtrlC: false, exitSignals: [], consoleMode: "disabled", useMouse: true });
syncReplBackground(controller, renderer, output);
const root = createRoot(renderer);
root.render(<ReplShell controller={controller} />);
renderer.keyInput.on("keypress", (key) => {
  if (key.name === "f5") {
    let index = 0;
    const timer = setInterval(() => {
      const end = index + 10;
      controller.receive({ ...message, message_id: "bulk", operation: "append",
        text: Array.from({ length: 10 }, (_, i) => `bulk-${index + i}: ${"abcd".repeat(12)}\n`).join("") });
      index = end;
      if (index === 300) {
        clearInterval(timer);
        controller.receive({ ...message, message_id: "bulk", operation: "append", text: "BULK-END" });
        controller.receive({ ...message, message_id: "bulk", operation: "end", status: "completed" });
      }
    }, 10);
  } else if (key.name === "f6" || key.name === "f7") {
    const scroll = renderer.root.findDescendantById("transcript-scroll") as ScrollBoxRenderable;
    scroll.scrollTo(key.name === "f6" ? 0 : scroll.scrollHeight);
    renderer.requestRender();
  } else if (key.name === "f8" || key.name === "f9") {
    if (key.name === "f9") themes.orange.background = "#345678";
    controller.applyConfigDocument({ config: { terminal: { theme: "orange", themes } } });
  }
});
controller.onExit = () => {
  root.unmount();
  void (async () => {
    await destroyTreeSitterClient(); renderer.destroy(); await renderer.closed;
    process.exit(0);
  })();
};
