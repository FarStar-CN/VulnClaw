import type { SessionTransport } from "../transport/backend";
import type {
  ApprovalRequiredEvent, Bootstrap, ClientMessage, Finding, MessageSnapshot,
  ServerEvent, StateSnapshot, SubagentEvent, TaskRequest,
} from "../transport/protocol.generated";
import type { ThemeId } from "../theme/palette";

export function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}

function completionText(value: unknown): string {
  const result = record(value);
  const lines: string[] = [];
  if (typeof result.status === "string") lines.push(`Status: ${result.status}`);
  if (typeof result.command === "string") lines.push(`${result.command} ${result.target ?? ""}`.trim());
  const run = record(result.run);
  if (typeof run.name === "string") lines.push(`Run: ${run.name}`);
  if (typeof run.directory === "string") lines.push(`Files: ${run.directory}`);
  if (typeof result.summary === "string") lines.push(result.summary);
  const counts = record(record(result.summary).findings);
  if (typeof counts.verified === "number" && typeof counts.candidates === "number") {
    lines.push(`Findings: ${counts.verified} verified, ${counts.candidates} candidates`);
  } else if (Array.isArray(result.findings)) lines.push(`Recorded findings: ${result.findings.length}`);
  const reports = [result.report, ...(Array.isArray(result.reports) ? result.reports : [])];
  for (const report of reports) {
    const paths = record(report);
    for (const [key, label] of [["path", "Report"], ["findings_json", "Findings JSON"], ["findings_sarif", "Findings SARIF"]]) {
      if (typeof paths[key!] === "string") lines.push(`${label}: ${paths[key!]}`);
    }
  }
  if (typeof result.report_error === "string") lines.push(`Report failed: ${result.report_error}`);
  // Assistant output already belongs to its message, including partial/final state.
  return lines.join("\n") || "Ready for another request.";
}

export type Card = {
  id: string; source: string; kind: "tool_call" | "tool_result" | "log" | "status" | "result" | "error";
  title: string; text: string; format: "text" | "markdown" | "code" | "json" | "http";
};
export type Entry = { kind: "message"; id: string } | { kind: "card"; id: string };
export type Overlay =
  | { kind: "task"; preview: Record<string, unknown>; original: string }
  | { kind: "config"; document: Record<string, unknown> }
  | { kind: "wizard"; document: Record<string, unknown> }
  | { kind: "reader"; messageId: string; raw: boolean }
  | { kind: "messages" | "agents" | "findings" | "chain" }
  | { kind: "data"; title: string; data: unknown; format: "text" | "markdown" };

export class SessionController {
  version = 0;
  connected = false;
  state: StateSnapshot | null = null;
  commands: Record<string, string> = {};
  taskCommands: string[] = [];
  skills: string[] = [];
  messages = new Map<string, MessageSnapshot>();
  cards = new Map<string, Card>();
  entries: Entry[] = [];
  agents = new Map<string, SubagentEvent>();
  findings: Finding[] = [];
  approvals: ApprovalRequiredEvent[] = [];
  permission = "ask";
  source = "main";
  activity: "task" | "conversation" | null = null;
  taskLabel = "";
  taskStatus = "";
  theme: ThemeId = "blue";
  showReasoning = true;
  overlay: Overlay | null = null;
  notice = "Starting Python session…";
  draft = "";
  history: string[] = [];
  historyIndex = 0;
  historyDraft = "";
  onExit: () => void = () => {};
  onPreferences: () => void = () => {};
  onHistoryRetry: (() => void) | null = null;
  private hidden = new Set<string>();
  private listeners = new Set<() => void>();
  private nextId = 0;
  private transport: SessionTransport | null = null;
  private requests = new Map<string, { resolve: (value: Record<string, unknown>) => void; reject: (error: Error) => void }>();

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  snapshot = (): number => this.version;
  changed(): void { this.version++; for (const listener of this.listeners) listener(); }
  attach(transport: SessionTransport, bootstrap: Bootstrap = {}): void {
    this.transport = transport;
    this.send({ protocol_version: 2, type: "initialize", request_id: this.id(),
      payload: { client: { name: "opentui", version: "0.4.1" }, bootstrap } });
  }
  id(): string { return `terminal-${++this.nextId}`; }
  private send(request: ClientMessage): void {
    if (!this.transport) throw new Error("Backend is disconnected");
    this.transport.send(request);
  }
  control(operation: string, args: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    const request_id = this.id();
    return new Promise((resolve, reject) => {
      this.requests.set(request_id, { resolve, reject });
      try { this.send({ protocol_version: 2, type: "control", request_id, payload: { operation, arguments: args } }); }
      catch (error) { this.requests.delete(request_id); reject(error); }
    });
  }
  action(operation: string, args: Record<string, unknown> = {}): void {
    void this.control(operation, args).then((result) => { this.showControl(operation, result); this.changed(); }).catch((error: unknown) => this.notify(String(error)));
  }
  notify(text: string): void { this.notice = text; this.changed(); }
  dismiss(): void { this.overlay = null; this.changed(); }
  setTheme(theme: ThemeId): void { this.theme = theme; this.onPreferences(); this.changed(); }
  visibleEntries(source = this.source): Entry[] {
    return this.entries.filter((entry) => {
      const item = entry.kind === "message" ? this.messages.get(entry.id) : this.cards.get(entry.id);
      return item && item.source === source && !this.hidden.has(entry.id)
        && !(entry.kind === "message" && (item as MessageSnapshot).role === "reasoning" && !this.showReasoning);
    });
  }
  clearDisplay(): void { for (const entry of this.entries) this.hidden.add(entry.id); this.changed(); }
  recall(previous: boolean): void {
    if (this.historyIndex === this.history.length) this.historyDraft = this.draft;
    this.historyIndex = Math.max(0, Math.min(this.history.length, this.historyIndex + (previous ? -1 : 1)));
    this.draft = this.history[this.historyIndex] ?? this.historyDraft;
    this.changed();
  }
  async submit(text: string): Promise<void> {
    if (!text.trim()) return;
    const command = text.trim().split(/\s+/, 1)[0];
    if (command === "/history-retry" && this.onHistoryRetry) {
      this.onHistoryRetry(); this.draft = ""; this.changed(); return;
    }
    if (command === "/reports") { this.action("report.read"); this.draft = ""; this.changed(); return; }
    if (["/messages", "/agents", "/findings", "/chain"].includes(command)) {
      this.overlay = { kind: command.slice(1) as "messages" | "agents" | "findings" | "chain" };
      this.draft = ""; this.changed(); return;
    }
    if (command === "/theme") {
      const theme = text.trim().split(/\s+/)[1];
      if (!theme) this.setTheme(this.theme === "blue" ? "orange" : "blue");
      else if (theme === "blue" || theme === "orange") this.setTheme(theme);
      else this.notify("Use /theme blue or /theme orange");
      this.draft = ""; this.changed(); return;
    }
    if (command === "/quit") { this.onExit(); return; }
    if (command === "/clear") { this.clearDisplay(); this.draft = ""; this.changed(); return; }
    if (!this.connected) { this.notify("Backend is disconnected"); return; }
    const executing = !command.startsWith("/") || this.taskCommands.includes(command.slice(1));
    const answering = !command.startsWith("/") && this.state?.task.waiting_for_input;
    if (this.state?.task.active && executing && !answering) {
      this.notify("An operation is active. Cancel it or wait for completion."); return;
    }
    if (this.history.at(-1) !== text) this.history.push(text);
    this.historyIndex = this.history.length;
    try {
      if (command.startsWith("/") && this.taskCommands.includes(command.slice(1))) {
        const preview = await this.control("task.preview", { text });
        this.overlay = { kind: "task", preview, original: text };
      } else {
        this.send({ protocol_version: 2, type: "submit_input", request_id: this.id(),
          task_id: this.id(), payload: { text } });
        this.draft = "";
      }
      this.changed();
    } catch (error) { this.notify(String(error)); }
  }
  startTask(task: TaskRequest): void {
    try {
      this.send({ protocol_version: 2, type: "start_task", request_id: this.id(),
        task_id: this.id(), payload: { task } });
      this.overlay = null; this.draft = ""; this.changed();
    } catch (error) { this.notify(String(error)); }
  }
  cancel(): void {
    const task_id = this.state?.task.task_id;
    if (!task_id || !this.state?.task.active) return;
    this.send({ protocol_version: 2, type: "cancel_task", request_id: this.id(), task_id, payload: {} });
  }
  resolveApproval(approve: boolean): void {
    const approval = this.approvals[0];
    if (!approval?.request_hash) { this.notify("Approval has no resolvable request ID"); return; }
    this.action("execution.approval.resolve", { request_hash: approval.request_hash, decision: approve ? "approve" : "deny" });
  }
  readMessage = (messageId: string): void => { this.overlay = { kind: "reader", messageId, raw: false }; this.changed(); };

  private showControl(operation: string, result: Record<string, unknown>): void {
    if (operation === "config.document") this.overlay = { kind: "config", document: result };
    else if (operation === "setup.status") this.overlay = { kind: "wizard", document: result };
    else if (operation === "report.export" || operation === "report.read") {
      const report = record(result.report);
      this.overlay = { kind: "data", title: String(report.path ?? "Report"), data: report.text ?? result, format: report.text && report.format !== "text" ? "markdown" : "text" };
    } else if (operation.startsWith("experience.") || ["learn", "feedback", "session.tools", "session.help", "session.status", "session.scope.read"].includes(operation)) {
      this.overlay = { kind: "data", title: operation, data: result, format: "text" };
    }
    if (typeof result.message === "string") this.notice = result.message;
  }
  private hydrate(state: StateSnapshot): void {
    this.state = state;
    this.findings = state.findings;
    if (state.messages) {
      const current = new Set(state.messages.map((message) => message.message_id));
      for (const identifier of this.messages.keys()) {
        if (!current.has(identifier)) this.messages.delete(identifier);
      }
      for (const message of state.messages) {
        if (!this.messages.has(message.message_id) && !this.entries.some((entry) => entry.id === message.message_id)) {
          this.entries.push({ kind: "message", id: message.message_id });
        }
        this.messages.set(message.message_id, { ...message });
      }
    }
  }
  private card(source: string, kind: Card["kind"], title: string, text: string, format: Card["format"] = "text"): void {
    const id = this.id();
    this.cards.set(id, { id, source, kind, title, text, format });
    this.entries.push({ kind: "card", id });
  }
  receive = (event: ServerEvent): void => {
    if (event.type === "ready") {
      this.connected = true; this.commands = event.capabilities.input_commands ?? {};
      this.taskCommands = event.capabilities.commands;
      this.permission = event.capabilities.permission_mode ?? "ask";
      this.skills = event.runtime.skills; this.notice = `Python ${event.backend.pid} · ${event.runtime.provider}/${event.runtime.model}`;
    }
    if ("state" in event && event.state) this.hydrate(event.state);
    switch (event.type) {
      case "task_started": this.activity = "task"; this.taskLabel = `${event.task.command} ${event.task.target}`; this.taskStatus = "Running"; break;
      case "turn_started": this.activity = "conversation"; break;
      case "message": {
        let message = this.messages.get(event.message_id);
        if (event.operation === "append") {
          if (!message) {
            message = { message_id: event.message_id, source: event.source, role: event.role,
              format: event.format, task_id: event.task_id, request_id: event.request_id,
              text: "", status: "streaming" };
            this.messages.set(event.message_id, message);
            this.entries.push({ kind: "message", id: event.message_id });
          }
          message.text += event.text;
        } else if (message) message.status = event.status;
        break;
      }
      case "tool_call": this.card(event.agent_id ?? "main", "tool_call", event.tool, event.arguments, "json"); break;
      case "tool_result": this.card(event.agent_id ?? "main", "tool_result", "Tool result", event.result, event.format ?? "text"); break;
      case "log": this.card(event.agent_id ?? "main", "log", "Log", event.message); break;
      case "status": this.notice = event.status; break;
      case "subagent": this.agents.set(event.agent_id, event); break;
      case "finding": {
        const index = this.findings.findIndex((finding) => finding.id === event.finding.id);
        if (index < 0) this.findings.push(event.finding); else this.findings[index] = event.finding;
        break;
      }
      case "approval_required": this.approvals.push(event); break;
      case "approval_closed": this.approvals = this.approvals.filter((item) => item.request_hash !== event.request_hash); this.notice = `Approval ${event.status}`; break;
      case "control_result": {
        const result = record(event.result);
        const requested = event.request_id ? this.requests.has(event.request_id) : false;
        if (event.request_id) { this.requests.get(event.request_id)?.resolve(result); this.requests.delete(event.request_id); }
        if (event.operation === "session.permission.set" && typeof result.mode === "string") this.permission = result.mode;
        if (event.operation === "session.new") { this.messages.clear(); this.cards.clear(); this.entries = []; this.agents.clear(); this.source = "main"; this.activity = null; this.taskLabel = ""; this.taskStatus = ""; }
        if (!requested) this.showControl(event.operation, result);
        break;
      }
      case "task_completed":
      case "turn_completed": {
        this.card("main", "result", event.type === "task_completed" ? "Task completed" : "Turn completed", completionText(event.result));
        if (event.type === "task_completed") this.taskStatus = "Completed";
        this.notice = "Ready"; break;
      }
      case "task_cancelled": case "turn_cancelled": this.taskStatus = "Cancelled"; this.notice = "Cancelled. Enter another request."; break;
      case "task_failed": case "turn_failed": this.taskStatus = "Failed"; this.card("main", "error", "Failed", event.error.message); this.notice = event.error.message; break;
      case "error": {
        if (event.request_id) { this.requests.get(event.request_id)?.reject(new Error(event.message)); this.requests.delete(event.request_id); }
        this.notice = event.message; break;
      }
      case "shutdown_complete": this.connected = false; break;
    }
    this.changed();
  };
  disconnect = (error: Error): void => {
    this.connected = false; this.notice = error.message; this.approvals = [];
    for (const message of this.messages.values()) if (message.status === "streaming") message.status = "failed";
    for (const pending of this.requests.values()) pending.reject(error);
    this.requests.clear(); this.changed();
  };
}
