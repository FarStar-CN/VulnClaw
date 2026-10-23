import type { SessionController } from "./controller";
import { palettes, permissionOrder } from "../theme/palette";
import { t } from "../i18n";

export function completions(controller: SessionController): string[] {
  const query = controller.draft.trimStart();
  if (query.startsWith("/theme ")) return Object.keys(palettes).map((name) => `/theme ${name}`).filter((item) => item.startsWith(query));
  if (query.startsWith("/permission ")) return permissionOrder.map((mode) => `/permission ${mode}`).filter((item) => item.startsWith(query));
  for (const command of ["/language", "/lang"]) {
    if (query.startsWith(command + " ")) return ["auto", "zh", "en"].map((mode) => `${command} ${mode}`).filter((item) => item.startsWith(query));
  }
  if (query.startsWith("/help ")) return controller.commandList().map((command) => `/help ${command.name}`).filter((item) => item.startsWith(query));
  if (query.startsWith("/") && !query.includes(" ")) return controller.commandList()
    .map((command) => `/${command.name}`).filter((name) => name.startsWith(query));
  if (query.startsWith("@") && !query.includes(" ")) return controller.skills
    .filter((name) => name.startsWith(query.slice(1))).map((name) => `Use VulnClaw skill ${name}.`);
  return [];
}
export function completionDescription(controller: SessionController, text: string): string {
  const name = text.slice(1);
  return t(controller.commandList().find((command) => command.name === name)?.description ?? "");
}
