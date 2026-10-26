import { t } from "../i18n";

export type Command = { name: string; description: string; category: string; syntax: string; examples: string[] };
// Only commands handled by the terminal override the backend definitions.
const local: Command[] = [
  { name: "theme", category: "Configuration", description: "Switch theme", syntax: "/theme [NAME]", examples: ["/theme orange"] },
  { name: "settings", category: "Configuration", description: "Edit the complete configuration", syntax: "/settings", examples: ["/settings"] },
  { name: "config", category: "Configuration", description: "Configure the model provider", syntax: "/config", examples: ["/config"] },
  { name: "scope", category: "Session", description: "Preview and edit session scope defaults", syntax: "/scope [reset | SCOPE_OPTIONS]", examples: ["/scope", "/scope --allowed-ports 80,443", "/scope reset"] },
  { name: "help", category: "Management", description: "List commands", syntax: "/help [COMMAND]", examples: ["/help scope"] },
  { name: "clear", category: "Session", description: "Clear the display; keep the conversation", syntax: "/clear", examples: ["/clear"] },
  { name: "quit", category: "Management", description: "Exit the client", syntax: "/quit", examples: ["/quit"] },
];
export function mergeCommands(summaries: Record<string, string>, metadata: Command[]): Command[] {
  const commands = new Map(Object.entries(summaries).map(([name, description]) => [name,
    { name, description, category: "Management", syntax: `/${name}`, examples: [] as string[] }]));
  for (const command of metadata) commands.set(command.name, command);
  for (const command of local) {
    // Scope keeps the backend's supported flags and adds terminal confirmation semantics.
    commands.set(command.name, command.name === "scope" && commands.get("scope")
      ? { ...commands.get("scope")!, description: command.description, examples: command.examples } : command);
  }
  return [...commands.values()];
}
export function helpText(commands: Command[], rawTopic = ""): string {
  const aliases: Record<string, string> = { cfg: "config", lang: "language", exp: "experience" };
  const token = rawTopic.replace(/^\//, "").toLowerCase();
  const topic = aliases[token] ?? token;
  const selected = commands.filter((command) => !topic || command.name === topic);
  if (!selected.length) return `${t("Unknown help topic: {topic}", { topic })}\n${t("Available topics")}: ${commands.map((command) => `/${command.name}`).join(", ")}`;
  const lines: string[] = [];
  for (const category of ["Tasks", "Session", "Configuration", "Management"]) {
    const group = selected.filter((command) => command.category === category);
    if (!group.length) continue;
    lines.push(`## ${t(category)}`);
    for (const command of group) {
      lines.push(`\`${command.syntax}\``, t(command.description));
      if (command.examples.length) lines.push(`${t("Examples")}: ${command.examples.map((example) => `\`${example}\``).join(" · ")}`);
      lines.push("");
    }
  }
  return lines.join("\n");
}
