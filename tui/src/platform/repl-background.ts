import type { CliRenderer, NativeSession } from "@opentui/core";
import type { SessionController } from "../session/controller";
import { palettes } from "../theme/palette";

export function syncReplBackground(controller: SessionController, renderer: CliRenderer, output: NativeSession): void {
  const encoder = new TextEncoder();
  let current = "";
  const update = () => {
    const color = palettes[controller.theme].background;
    if (color === current) return;
    if (output.write(encoder.encode(`\x1b]11;${color}\x07`))) current = color;
    else renderer.requestRender();
  };
  renderer.on("frame", update);
  renderer.once("destroy", () => {
    renderer.off("frame", update);
    renderer.resetTerminalBgColor();
  });
  update();
}
