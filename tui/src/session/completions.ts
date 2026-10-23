import type { SessionController } from "./controller";
import { palettes, permissionOrder } from "../theme/palette";
import { t } from "../i18n";

export const LOCAL_COMMANDS = {
  theme: "Switch theme",
  settings: "Edit the complete configuration", config: "Configure the model provider",
  language: "Set display language",
  help: "List commands", clear: "Clear the display", quit: "Exit the client",
};
const order = ["run", "recon", "scan", "exploit", "persistent", "codescan", "report", "target", "scope", "permission",
  "new", "clear", "compact", "tools", "think", "config", "theme", "help", "quit"];
export function completions(controller: SessionController): string[] {
  const query = controller.draft.trimStart();
  if (query.startsWith("/theme ")) return Object.keys(palettes).map((name) => `/theme ${name}`).filter((item) => item.startsWith(query));
  if (query.startsWith("/permission ")) return permissionOrder.map((mode) => `/permission ${mode}`).filter((item) => item.startsWith(query));
  for (const command of ["/language", "/lang"]) {
    if (query.startsWith(command + " ")) return ["auto", "zh", "en"].map((mode) => `${command} ${mode}`).filter((item) => item.startsWith(query));
  }
  if (query.startsWith("/") && !query.includes(" ")) return [...new Set([...Object.keys(controller.commands), ...Object.keys(LOCAL_COMMANDS), ...(controller.onHistoryRetry ? ["history-retry"] : [])])]
    .sort((a, b) => (order.indexOf(a) < 0 ? 99 : order.indexOf(a)) - (order.indexOf(b) < 0 ? 99 : order.indexOf(b)))
    .map((name) => `/${name}`).filter((name) => name.startsWith(query));
  if (query.startsWith("@") && !query.includes(" ")) return controller.skills
    .filter((name) => name.startsWith(query.slice(1))).map((name) => `Use VulnClaw skill ${name}.`);
  return [];
}
export function completionDescription(controller: SessionController, text: string): string {
  const name = text.slice(1);
  return t(controller.commands[name] ?? LOCAL_COMMANDS[name as keyof typeof LOCAL_COMMANDS] ?? "");
}
