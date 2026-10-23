import type { SubagentEvent } from "../transport/protocol.generated";
import { t, statusLabel } from "../i18n";

export type AgentRow =
  | { kind: "agent"; id: string; text: string }
  | { kind: "group"; id: null; text: string }
  | { kind: "directory"; id: null; text: string; count: number };

function agentTreeRows(agents: Map<string, SubagentEvent>, members: SubagentEvent[]): AgentRow[] {
  const rows: AgentRow[] = [];
  const children = new Map<string, SubagentEvent[]>();
  const memberIds = new Set(members.map((agent) => agent.agent_id));
  for (const agent of members) {
    let parent = agents.get(agent.parent_id);
    const visited = new Set([agent.agent_id]);
    while (parent && !memberIds.has(parent.agent_id) && !visited.has(parent.agent_id)) {
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
        rows.push({ kind: "group", id: null, text: `${owner?.name ?? agent.group_id} [${agent.group_id}]` });
      }
      prefix = "  └─ "; indent = "     ";
    }
    const role = agent.agent_type === "group-leader" ? "leader" : agent.agent_type;
    rows.push({ kind: "agent", id: agent.agent_id, text: `${prefix}${agent.name} (${t(role)}) · ${statusLabel(agent.status)}` });
    const descendants = children.get(agent.agent_id) ?? [];
    descendants.forEach((child, i) => append(child, indent + (i === descendants.length - 1 ? "└─ " : "├─ "), indent + (i === descendants.length - 1 ? "   " : "│  ")));
  }
  (children.get("main") ?? []).forEach((agent) => append(agent, "", ""));
  members.filter((agent) => !seen.has(agent.agent_id)).forEach((agent) => append(agent, "", ""));
  return rows;
}

export function activeAgentRows(agents: Map<string, SubagentEvent>): AgentRow[] {
  const active = [...agents.values()].filter((agent) => ["pending", "running"].includes(agent.status));
  return [{ kind: "agent", id: "main", text: "main" }, ...agentTreeRows(agents, active)];
}

export function subagentRows(agents: Map<string, SubagentEvent>, endedExpanded: boolean): AgentRow[] {
  const ended = [...agents.values()].filter((agent) => ["completed", "failed", "killed"].includes(agent.status));
  return [
    ...activeAgentRows(agents),
    { kind: "directory", id: null, count: ended.length, text: t("Ended subagents ({count})", { count: ended.length }) },
    ...(endedExpanded ? agentTreeRows(agents, ended).map((row) => ({ ...row, text: "  " + row.text })) : []),
  ];
}
