import { act } from "react";
import { CodeRenderable, type Renderable } from "@opentui/core";
import type { TestRendererSetup } from "@opentui/core/testing";

export async function finalFrame(setup: TestRendererSetup): Promise<string> {
  // Window changes requested by a native frame must commit before the next layout pass.
  await act(async () => { await setup.renderOnce(); });
  await act(async () => {
    const promises: Promise<void>[] = [];
    function visit(node: Renderable) {
      if (node instanceof CodeRenderable) promises.push(node.highlightingDone);
      for (const child of node.getChildren()) visit(child);
    }
    visit(setup.renderer.root);
    await Promise.all(promises);
    await setup.renderOnce();
  });
  return setup.captureCharFrame();
}
