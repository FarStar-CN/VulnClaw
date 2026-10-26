import stringWidth from "string-width";
import type { ExecutionDetailRef, ToolExecutionEvent } from "../transport/protocol.generated";
import { statusLabel, t } from "../i18n";

export type ExecutionStatus = ToolExecutionEvent["status"];
export type Operation = Omit<ToolExecutionEvent, "type" | "protocol_version" | "task_id" | "call_id" | "agent_id">;
export interface ExecutionRecord {
  taskId: string; callId: string; tool: string; arguments: string;
  status: ExecutionStatus; operations: Map<string, Operation>;
  received: boolean; error?: string; duration?: number; evidenceId?: string;
  result?: string; structuredContent?: Record<string, unknown>;
  resultRef?: ExecutionDetailRef;
  members?: Map<string, ExecutionRecord>;
}
export function executionComplete(record: ExecutionRecord): boolean {
  return record.members ? [...record.members.values()].every(executionComplete)
    : record.received && !activeExecution(record.status);
}
export const activeExecution = (status: ExecutionStatus) => ["preparing", "awaiting_approval", "running"].includes(status);
export const failedExecution = (status: ExecutionStatus) => ["failed", "timed_out", "cancelled", "denied"].includes(status);
const labels: Record<ExecutionStatus, string> = {
  preparing: "Preparing", awaiting_approval: "Awaiting approval", running: "Running",
  completed: "Completed", returned: "Returned", partial: "Partially completed", failed: "Failed",
  timed_out: "Timed out", cancelled: "Cancelled", denied: "Denied", skipped: "Skipped",
};
export function executionStatus(status: ExecutionStatus): string { return t(labels[status]); }
export function fitExecution(text: string, width: number): string {
  const line = text.replace(/\s+/g, " ");
  if (stringWidth(line) <= width) return line;
  let result = "";
  for (const { segment } of new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(line)) {
    if (stringWidth(result + segment + "…") > width) break;
    result += segment;
  }
  return result + "…";
}
export function operationTitle(operation: Operation): string {
  return operation.kind === "http" ? `${operation.method ?? "GET"} ${operation.url ?? ""}`
    : operation.command || operation.argv?.join(" ") || t("Operation");
}
export function operationLabel(operation: Operation): string {
  return t(operation.kind === "http" ? "HTTP request" : operation.kind === "script" ? "Script" : "Command");
}
export function operationReceipt(operation: Operation): string {
  return [executionStatus(operation.status),
    operation.status_code !== undefined ? `HTTP ${operation.status_code}` : "",
    operation.exit_code !== undefined ? t("Exit {code}", { code: operation.exit_code }) : "",
    operation.duration_ms !== undefined ? `${operation.duration_ms} ms` : "",
    operation.attempt && operation.attempt > 1 ? t("Attempt {count}", { count: operation.attempt }) : "",
  ].filter(Boolean).join(" · ");
}
export function executionTitle(record: ExecutionRecord): string {
  const operations = [...record.operations.values()];
  if (operations.length === 1) return operationTitle(operations[0]!);
  if (operations.length > 1) return operations.every((op) => op.kind === "http")
    ? t("HTTP batch · {count} requests", { count: operations.length })
    : `${record.tool} · ${t("{count} operations", { count: operations.length })}`;
  let args: Record<string, unknown> = {};
  try { args = JSON.parse(record.arguments); } catch { /* Keep the tool label when arguments cannot be decoded. */ }
  if (!args || typeof args !== "object") args = {};
  if (record.tool === "shell_command") return String(args.command ?? args.cmd ?? record.tool);
  if (record.tool === "fetch") return `${args.method ?? "GET"} ${args.url ?? ""}`;
  if (record.tool === "python_execute") return t("Python script");
  if (record.tool === "http_probe_batch" && Array.isArray(args.requests)) {
    if (args.requests.length === 1) {
      const request = args.requests[0] as Record<string, unknown> | null;
      return `${request?.method ?? "GET"} ${request?.raw_url ?? request?.url ?? ""}`;
    }
    return t("HTTP batch · {count} requests", { count: args.requests.length });
  }
  return record.tool;
}
export function executionReceipt(record: ExecutionRecord): string {
  const operations = [...record.operations.values()];
  if (operations.length === 1) return operationReceipt({ ...operations[0]!, status: record.status === "returned" ? operations[0]!.status : record.status });
  return [executionStatus(record.status), record.duration !== undefined ? `${record.duration} ms` : "",
    operations.length > 1 ? t("{completed} completed · {failed} failed", {
      completed: operations.filter((op) => op.status === "completed").length,
      failed: operations.filter((op) => failedExecution(op.status)).length,
    }) : operations[0] ? operationReceipt(operations[0]) : "",
  ].filter(Boolean).join(" · ");
}
export interface ExecutionSection { label: string; text: string; detail?: ExecutionDetailRef }
export function operationSections(op: Operation): ExecutionSection[] {
  const headers = (value?: Record<string, string>) => Object.entries(value ?? {}).map(([k, v]) => `${k}: ${v}`).join("\n");
  const sections: (ExecutionSection & { field?: string })[] = [
    { label: "Working directory", text: op.cwd ?? "" },
    { label: "Arguments", text: op.argv ? JSON.stringify(op.argv) : "" },
    { label: "Source code", text: op.code ?? "", field: "code" },
    { label: "Request headers", text: headers(op.headers) },
    { label: "Request body", text: op.body ?? "", field: "body" },
    { label: "Response headers", text: headers(op.response_headers) },
    { label: "Response body", text: op.response_body ?? "", field: "response_body" },
    { label: "stdout", text: op.stdout ?? "", field: "stdout" },
    { label: "stderr", text: op.stderr ?? "", field: "stderr" },
  ];
  return sections.map(({ field, ...section }) => ({ ...section,
    detail: field ? op.detail_refs?.[field] : undefined,
  })).filter((section) => section.text !== "" || section.detail);
}
export function executionResult(record: ExecutionRecord, result: string): string {
  return record.operations.size && ["shell_command", "python_execute", "runtime_diff_probe", "fetch", "http_probe_batch"].includes(record.tool) ? "" : result;
}
const internalNames: Record<string, string> = {
  submit_finding: "Submit candidate finding", agent_run: "Dispatch subagent", agent_job: "Manage subagent tasks",
  evidence_list: "List evidence", evidence_view: "View evidence", evidence_search: "Search evidence",
  load_skill_reference: "Load skill reference", memory_search: "Search conversation history",
  vault_archive: "Archive conversation history", vault_restore: "Restore history archive",
  vault_search: "Search archive summaries", vault_status: "View archive status",
};
const analysisNames: Record<string, string> = {
  source_extract: "Extract source evidence", crypto_decode: "Decode content", traffic_list: "List captured traffic",
  traffic_view: "View captured traffic", traffic_sitemap: "Summarize captured sitemap", topology_build: "Build network topology",
  compliance_map: "Map compliance controls", findings_report: "Summarize finding risks", findings_diff: "Compare assessments",
  remediation_advice: "Generate remediation advice", attack_map: "Map attack techniques",
};
const jobNames: Record<string, string> = { list: "List subagent tasks", collect: "Collect subagent results",
  message: "Send subagent message", cancel: "Cancel subagent task" };
export interface ExecutionPresentation {
  title: string; receipt: string; status: ExecutionStatus; category: "internal" | "analysis" | "execution";
  expandable: boolean; sections: ExecutionSection[]; error?: string; expandedTitle?: string;
}
const object = (value: unknown): Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value)
  ? value as Record<string, unknown> : {};
const string = (value: unknown): string => typeof value === "string" || typeof value === "number" ? String(value) : "";
const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
function argumentsOf(record: ExecutionRecord): Record<string, unknown> {
  try { return object(JSON.parse(record.arguments)); } catch { return {}; }
}
function businessText(value: Record<string, unknown>, fallback: string): string {
  if (typeof value.text === "string") return value.text;
  return Object.keys(value).length ? Object.entries(value).map(([key, item]) =>
    `${key}: ${typeof item === "string" ? item : JSON.stringify(item)}`).join("\n") : fallback;
}
function agentStatus(value: unknown): ExecutionStatus | undefined {
  return ({ pending: "running", running: "running", completed: "completed", failed: "failed", killed: "cancelled" } as Record<string, ExecutionStatus>)[string(value)];
}
export function executionPresentation(record: ExecutionRecord): ExecutionPresentation {
  if (record.members) {
    const members = [...record.members.values()];
    const views = members.map(executionPresentation);
    const failed = views.filter(view => failedExecution(view.status)).length;
    const skipped = views.filter(view => view.status === "skipped").length;
    const dispatched = members.filter((member, index) => member.structuredContent?.background === true && views[index]!.status === "completed").length;
    const completed = views.filter(view => view.status === "completed").length - dispatched;
    const pending = members.filter(member => !executionComplete(member)).length;
    const status: ExecutionStatus = pending ? "running" : failed ? failed < members.length ? "partial" : "failed"
      : skipped === members.length ? "skipped" : views.some(view => view.status === "returned") ? "returned" : "completed";
    const counts = [[completed, "{count} completed"], [dispatched, "{count} dispatched"], [failed, "{count} failed"],
      [skipped, "{count} skipped"], [pending, "{count} pending"]] as const;
    return { category: "internal", title: `${t("Dispatch subagents")} · ${t("{count} tasks", { count: members.length })}`,
      status, receipt: counts.filter(([count]) => count > 0).map(([count, label]) => t(label, { count })).join(" · ") || executionStatus(status),
      expandable: true, sections: views.map(view => ({ label: view.title,
        text: [view.receipt, view.error, ...view.sections.map(section => `${section.label}: ${section.text}`)].filter(Boolean).join("\n") })) };
  }
  const args = argumentsOf(record), data = record.structuredContent ?? {};
  const name = internalNames[record.tool] ?? analysisNames[record.tool];
  if (!name) return { title: executionTitle(record), expandedTitle: record.operations.size === 1 ? operationLabel([...record.operations.values()][0]!) : undefined,
    receipt: executionReceipt(record), status: record.status,
    category: "execution", expandable: true, error: record.error, sections: [] };
  const category = internalNames[record.tool] ? "internal" : "analysis";
  const action = string(args.action), sections: ExecutionSection[] = [];
  let status = record.status, error = record.error, receipt = executionStatus(status), subject = "";
  const add = (label: string, text: unknown) => { const value = string(text); if (value) sections.push({ label: t(label), text: value }); };
  switch (record.tool) {
    case "submit_finding": {
      subject = string(args.title);
      const actions: Record<string, string> = { created: "Candidate recorded", updated: "Evidence added", duplicate: "Already recorded" };
      const verification: Record<string, string> = { pending: "Pending verification", verified: "Verified", rejected: "Rejected",
        manual_review: "Needs review", suspected_high: "Needs review", suspected_low: "Needs review" };
      if (!failedExecution(status) && status !== "skipped") receipt = [actions[string(data.action)] ? t(actions[string(data.action)]!) : receipt,
        args.severity ? statusLabel(string(args.severity)) : "", data.verification_status ? t(verification[string(data.verification_status)] ?? string(data.verification_status)) : "",
        strings(args.evidence_ids).length ? t("{count} evidence references", { count: strings(args.evidence_ids).length }) : ""].filter(Boolean).join(" · ");
      add("Location", [string(args.method), string(args.endpoint || args.code_location)].filter(Boolean).join(" "));
      add("Description", args.description); add("Evidence", strings(args.evidence_ids).join(", "));
      break;
    }
    case "agent_run":
    case "agent_job": {
      subject = string(args.description || data.name || args.name || args.job_id);
      if (!activeExecution(status) && !failedExecution(status) && status !== "skipped") {
        if (data.ok === false) status = "failed";
        else if (record.tool === "agent_run" && data.background === true && data.ok === true) { status = "completed"; receipt = t("Dispatched"); }
        else if (record.tool === "agent_job" && action === "message" && data.ok === true) { status = "completed"; receipt = t("Message sent"); }
        else if (record.tool === "agent_job" && action !== "cancel") receipt = [executionStatus(status), statusLabel(string(data.status))].filter(Boolean).join(" · ");
        else { status = agentStatus(data.status) ?? status; receipt = executionStatus(status); }
      }
      error = error || string(data.error) || string(object(data.result).error) || undefined;
      if (failedExecution(status)) receipt = executionStatus(status);
      add("Role", args.agent_type ? t(string(args.agent_type)) : "");
      if (action === "message") add("Message", args.message);
      if (action === "cancel") add("Reason", args.reason);
      const result = Object.keys(object(data.result)).length ? object(data.result) : data;
      add("Summary", result.summary);
      if (result.content !== result.summary) add("Agent result", result.content);
      add("Evidence", strings(result.parent_evidence_ids).join(", "));
      if (Array.isArray(data.tasks)) add("Task list", data.tasks.map(item => { const task = object(item);
        return [string(task.name || task.description || task.task_id), statusLabel(string(task.status))].filter(Boolean).join(" · "); }).join("\n"));
      if (!record.received && !sections.length) add("Assignment", args.description);
      break;
    }
    case "load_skill_reference": {
      subject = [string(args.skill_name), string(args.reference_name)].filter(Boolean).join("/");
      receipt = status === "completed" ? t("Loaded successfully") : status === "failed" ? t("Loading failed")
        : activeExecution(status) ? t("Loading") : executionStatus(status);
      if (error) receipt += ` · ${error}`;
      return { category, title: `${t(name)}${subject ? ` · ${subject}` : ""}`, status, receipt, expandable: false, sections: [] };
    }
    case "evidence_list": subject = t("Latest {count} records", { count: string(args.limit) || 20 }); break;
    case "evidence_view": subject = [string(args.evidence_id), t("Offset {offset}", { offset: string(args.offset) || 0 })].join(" · "); break;
    case "evidence_search": case "memory_search": case "vault_search": subject = string(args.query); break;
    case "vault_archive": case "vault_restore": subject = [string(args.start), string(args.end)].filter(Boolean).join("—"); break;
    case "source_extract": subject = string(args.evidence_id) || t("Inline content"); break;
    case "crypto_decode": subject = string(args.operation); break;
    case "traffic_list": subject = string(args.host || args.method); break;
    case "traffic_view": subject = string(args.request_id); break;
    case "traffic_sitemap": subject = t("Captured traffic"); break;
    case "topology_build": subject = t("Existing scan results"); break;
    case "compliance_map": subject = strings(args.frameworks).join(", "); break;
    case "findings_report": case "attack_map": subject = t("Current assessment"); break;
    case "findings_diff": subject = t("Baseline and current"); break;
    case "remediation_advice": subject = string(args.query) || t("Current assessment"); break;
  }
  if (!["submit_finding", "agent_run", "agent_job"].includes(record.tool)) {
    add("Result", businessText(data, record.result ?? ""));
  }
  if (record.duration !== undefined) receipt += ` · ${record.duration} ms`;
  return { category, title: `${t(record.tool === "agent_job" ? jobNames[action] ?? name : name)}${subject ? ` · ${subject}` : ""}`,
    status, receipt, error, sections, expandable: sections.length > 0 };
}
