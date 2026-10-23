import type { SessionController } from "./controller";

export const LOCAL_COMMANDS = {
  theme: "Switch blue/orange theme", messages: "Read or copy messages",
  findings: "Read findings", agents: "Choose a reading source", chain: "Read attack chain",
  reports: "Read the latest report",
};
export function completions(controller: SessionController): string[] {
  const query = controller.draft.trimStart();
  if (query.startsWith("/theme ")) return ["/theme blue", "/theme orange"].filter((item) => item.startsWith(query));
  if (query.startsWith("/") && !query.includes(" ")) return [...Object.keys(controller.commands), ...Object.keys(LOCAL_COMMANDS), ...(controller.onHistoryRetry ? ["history-retry"] : [])]
    .map((name) => `/${name}`).filter((name) => name.startsWith(query));
  if (query.startsWith("@") && !query.includes(" ")) return controller.skills
    .filter((name) => name.startsWith(query.slice(1))).map((name) => `Use VulnClaw skill ${name}.`);
  return [];
}
