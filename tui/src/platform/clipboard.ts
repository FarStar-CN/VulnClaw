import { createClipboard, createHostClipboard, createRendererClipboardAdapter, type CliRenderer } from "@opentui/core";

const instances = new WeakMap<CliRenderer, ReturnType<typeof createClipboard>>();
function clipboard(renderer: CliRenderer) {
  let service = instances.get(renderer);
  if (!service) {
    service = createClipboard({ host: createHostClipboard(), terminal: createRendererClipboardAdapter(renderer) });
    instances.set(renderer, service);
    renderer.on("destroy", () => { void service!.dispose(); });
  }
  return service;
}
export async function copyText(renderer: CliRenderer, text: string): Promise<string> {
  const result = await clipboard(renderer).writeText(text, { destination: "best-available" });
  if (result.host.status === "written") return "Copied to clipboard";
  if (result.terminal.status === "attempted") return "Clipboard request sent to terminal";
  throw new Error(`Clipboard unavailable (${result.host.status})`);
}
export async function pasteText(renderer: CliRenderer): Promise<string> {
  const result = await clipboard(renderer).read({ preferredTypes: ["text/plain"] });
  if (result.status !== "read") throw new Error(`Clipboard unavailable (${result.status})`);
  return new TextDecoder().decode(result.representation.bytes);
}
