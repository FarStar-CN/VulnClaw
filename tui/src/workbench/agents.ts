import type { SubagentEvent } from "../transport/protocol.generated";
import { t, statusLabel } from "../i18n";

export interface AgentRow { id: string | null; text: string }
export function activeAgentRows(agents: Map<string, SubagentEvent>): AgentRow[] {
  const rows: AgentRow[] = [{ id: "main", text: "main" }];
  const active = [...agents.values()].filter((agent) => ["pending", "running"].includes(agent.status));
  const children = new Map<string, SubagentEvent[]>();
  const activeIds = new Set(active.map((agent) => agent.agent_id));
  for (const agent of active) {
    let parent = agents.get(agent.parent_id);
    const visited = new Set([agent.agent_id]);
    while (parent && !activeIds.has(parent.agent_id) && !visited.has(parent.agent_id)) {
      visited.add(parent.agent_id); parent = agents.get(parent.parent_id);
    }
    const key = parent && !visited.has(parent.agent_id) ? parent.agent_id : "main";
    children.set(key, [...(children.get(key) ?? []), agent]);
  }
  const seen = new Set<string>();
  const groups = new Set<string>();
  function append(agent: SubagentEvent, prefix: string, indent: string) {
    if (seen.has(agent.agent_id)) return;
    seen.add(agent.agent_id);
    if (!prefix && agent.group_id) {
      if (!groups.has(agent.group_id)) {
        groups.add(agent.group_id);
        const owner = [...agents.values()].find((item) => item.group_id === agent.group_id && item.agent_type === "group-leader");
        rows.push({ id: null, text: `${owner?.name ?? agent.group_id} [${agent.group_id}]` });
      }
      prefix = "  └─ "; indent = "     ";
    }
    const role = agent.agent_type === "group-leader" ? "leader" : agent.agent_type;
    rows.push({ id: agent.agent_id, text: `${prefix}${agent.name} (${t(role)}) · ${statusLabel(agent.status)}` });
    const members = children.get(agent.agent_id) ?? [];
    members.forEach((child, i) => append(child, indent + (i === members.length - 1 ? "└─ " : "├─ "), indent + (i === members.length - 1 ? "   " : "│  ")));
  }
  (children.get("main") ?? []).forEach((agent) => append(agent, "", ""));
  active.filter((agent) => !seen.has(agent.agent_id)).forEach((agent) => append(agent, "", ""));
  return rows;
}
