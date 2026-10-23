import { activeExecution, type ExecutionRecord, type ExecutionStatus } from "./executions";
import type { SessionTransport } from "../transport/backend";
import type {
  ApprovalRequiredEvent, Bootstrap, ClientMessage, Finding, MessageSnapshot,
  RuntimeInfo, ServerEvent, StateSnapshot, SubagentEvent, TaskRequest,
} from "../transport/protocol.generated";
import { configureThemes, palettes, permissionOrder, type ThemeId } from "../theme/palette";
import { setLanguage, statusLabel, t } from "../i18n";
import { helpText, mergeCommands, type Command } from "./commands";
import { experienceStatusLabels, type ExperienceResult } from "./experience";

export function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}

export function reportPaths(value: unknown): string[] {
  const report = record(value);
  return [["path", "Report"], ["findings_json", "Findings JSON"], ["findings_sarif", "Findings SARIF"]]
    .flatMap(([key, label]) => typeof report[key!] === "string" ? [`${t(label!)}: ${report[key!]}`] : []);
}

export function scopeSummary(value: unknown): string {
  const labels: Record<string, string> = { allowed_hosts: "Allow hosts", blocked_hosts: "Block hosts", allowed_ports: "Allow ports", blocked_ports: "Block ports", allowed_paths: "Allow paths", blocked_paths: "Block paths", allowed_actions: "Allow actions", blocked_actions: "Block actions" };
  return Object.entries(record(value)).filter(([, item]) => Array.isArray(item) ? item.length : item && typeof item !== "boolean")
    .map(([key, item]) => `${t(labels[key] ?? key)}: ${Array.isArray(item) ? item.join(", ") : String(item)}`).join("\n") || t("No additional constraints");
}

function completionText(value: unknown): string {
  const result = record(value);
  const lines: string[] = [];
  if (typeof result.status === "string") lines.push(`${t("Status")}: ${statusLabel(result.status)}`);
  if (typeof result.command === "string") lines.push(`${result.command} ${result.target ?? ""}`.trim());
  const run = record(result.run);
  if (typeof run.name === "string") lines.push(`${t("Run")}: ${run.name}`);
  if (typeof run.directory === "string") lines.push(`${t("Files")}: ${run.directory}`);
  if (typeof result.summary === "string") lines.push(result.summary);
  const counts = record(record(result.summary).findings);
  if (typeof counts.verified === "number" && typeof counts.candidates === "number") {
    lines.push(t("Findings: {verified} verified, {candidates} candidates", { verified: counts.verified, candidates: counts.candidates }));
  } else if (Array.isArray(result.findings)) lines.push(t("Recorded findings: {count}", { count: result.findings.length }));
  const reports = [result.report, ...(Array.isArray(result.reports) ? result.reports : [])];
  for (const report of reports) lines.push(...reportPaths(report));
  if (typeof result.report_error === "string") lines.push(t("Report failed: {error}", { error: result.report_error }));
  // Assistant output already belongs to its message, including partial/final state.
  return lines.join("\n") || t("Ready for another request.");
}

export type Card = {
  id: string; source: string; kind: "execution" | "log" | "status" | "result" | "error";
  execution?: ExecutionRecord;
  title: string; text: string; format: "text" | "markdown" | "code" | "json" | "http";
};
export type Entry = { kind: "message"; id: string } | { kind: "card"; id: string };
export type Overlay =
  | { kind: "experience"; data: ExperienceResult; loading: boolean; error?: string }
  | { kind: "scope"; preview: Record<string, unknown>; loading: boolean }
  | { kind: "task"; preview: Record<string, unknown>; original: string }
  | { kind: "config" | "llm"; document: Record<string, unknown> }
  | { kind: "data"; title: string; data: unknown };

export class SessionController {
  version = 0;
  connected = false;
  state: StateSnapshot | null = null;
  runtime: RuntimeInfo | null = null;
  commands: Record<string, string> = {};
  commandMetadata: Command[] = [];
  commandList(): Command[] { return mergeCommands(this.commands, this.commandMetadata, !!this.onHistoryRetry); }
  taskCommands: string[] = [];
  skills: string[] = [];
  messages = new Map<string, MessageSnapshot>();
  cards = new Map<string, Card>();
  entries: Entry[] = [];
  private executionCards = new Map<string, Card>();
  agents = new Map<string, SubagentEvent>();
  findings: Finding[] = [];
  approvals: ApprovalRequiredEvent[] = [];
  approvalDeadlines = new Map<string, number>();
  permission = "ask";
  source = "main";
  activity: "task" | "conversation" | null = null;
  taskLabel = "";
  taskStatus = "";
  startedAt: number | null = null;
  inputFocused = true;
  composerRows = 1;
  completionRows = 0;
  permissionPending = false;
  theme: ThemeId = "blue";
  showReasoning = true;
  overlay: Overlay | null = null;
  notice = t("Starting Python session…");
  draft = "";
  history: string[] = [];
  onExit: () => void = () => {};
  onHistoryRetry: (() => void) | null = null;
  private hidden = new Set<string>();
  private listeners = new Set<() => void>();
  private nextId = 0;
  private transport: SessionTransport | null = null;
  private reportRequests = new Set<string>();
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
    if (!this.transport) throw new Error(t("Backend is disconnected"));
    this.transport.send(request);
  }
  private request(message: ClientMessage): Promise<Record<string, unknown>> {
    const request_id = message.request_id!;
    return new Promise((resolve, reject) => {
      this.requests.set(request_id, { resolve, reject });
      try { this.send(message); }
      catch (error) { this.requests.delete(request_id); reject(error); }
    });
  }
  control(operation: string, args: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    return this.request({ protocol_version: 2, type: "control", request_id: this.id(), payload: { operation, arguments: args } });
  }
  action(operation: string, args: Record<string, unknown> = {}): void {
    void this.control(operation, args).then((result) => { this.showControl(operation, result); this.changed(); }).catch((error: unknown) => this.notify(String(error)));
  }
  notify(text: string): void { this.notice = text; this.changed(); }
  dismiss(): void { this.overlay = null; this.changed(); }
  output(title: string, text: string, format: Card["format"] = "text", kind: Card["kind"] = "result"): void {
    this.card("main", kind, title, text, format); this.source = "main"; this.changed();
  }
  scopeSaved(result: Record<string, unknown>): void {
    this.dismiss(); this.draft = "";
    this.output("Session scope defaults saved", scopeSummary(result.constraints));
  }
  applyConfigDocument(document: Record<string, unknown>): void {
    const mode = record(record(document.config).session).language ?? document.language;
    setLanguage(mode);
    const terminal = record(document.config).terminal;
    if (terminal !== undefined) this.theme = configureThemes(terminal).theme;
    this.changed();
  }
  visibleEntries(source = this.source): Entry[] {
    return this.entries.filter((entry) => {
      const item = entry.kind === "message" ? this.messages.get(entry.id) : this.cards.get(entry.id);
      return item && item.source === source && !this.hidden.has(entry.id)
        && !(entry.kind === "message" && (item as MessageSnapshot).role === "reasoning" && !this.showReasoning);
    });
  }
  clearDisplay(): void { for (const entry of this.entries) this.hidden.add(entry.id); this.changed(); }
  cyclePermission(): void {
    if (this.permissionPending || !this.connected) return;
    this.permissionPending = true;
    this.changed();
    const modes = permissionOrder;
    void this.control("session.permission.set", { mode: modes[(modes.indexOf(this.permission as typeof modes[number]) + 1) % modes.length] })
      .catch((error: unknown) => this.notify(String(error)))
      .finally(() => { this.permissionPending = false; this.changed(); });
  }
  async submit(text: string): Promise<void> {
    if (!text.trim()) return;
    const head = text.trim().split(/\s+/, 1)[0]!;
    const command = head.toLowerCase() === "/cfg" ? "/config" : head.toLowerCase();
    if (command === "/history-retry" && this.onHistoryRetry) {
      this.onHistoryRetry(); this.draft = ""; this.changed(); return;
    }
    if (command === "/theme") {
      const theme = text.trim().split(/\s+/)[1]?.toLowerCase();
      const themes = Object.keys(palettes);
      const selected = theme ?? themes[(themes.indexOf(this.theme) + 1) % themes.length];
      if (!Object.hasOwn(palettes, selected)) { this.notify(t("Unknown theme: {theme}. Available themes: {themes}", { theme: selected, themes: themes.join(", ") })); return; }
      this.draft = ""; this.changed();
      try { this.applyConfigDocument(await this.control("config.update", { changes: { "terminal.theme": selected } })); }
      catch (error) { if (!this.draft) this.draft = text; this.notify(String(error)); }
      return;
    }
    if (command === "/quit") { this.onExit(); return; }
    if (command === "/clear") { this.clearDisplay(); this.draft = ""; this.changed(); return; }
    if (!this.connected) { this.notify(t("Backend is disconnected")); return; }
    if (command === "/config" && text.trim() === head) { this.action("config.read"); this.draft = ""; this.changed(); return; }
    if (command === "/settings") { this.action("config.document"); this.draft = ""; this.changed(); return; }
    const executing = !command.startsWith("/") || this.taskCommands.includes(command.slice(1));
    const answering = !command.startsWith("/") && this.state?.task.waiting_for_input;
    if (this.state?.task.active && executing && !answering) {
      this.notify(t("An operation is active. Cancel it or wait for completion.")); return;
    }
    if (this.history.at(-1) !== text) this.history.push(text);
    this.history = this.history.slice(-50);
    if (command === "/experience" || command === "/exp") {
      const pending: Overlay = { kind: "experience", data: {}, loading: true };
      this.overlay = pending; this.changed();
      try {
        const result = await this.request({ protocol_version: 2, type: "submit_input", request_id: this.id(), task_id: this.id(), payload: { text } });
        if (this.overlay !== pending) return;
        this.overlay = { kind: "experience", data: result as ExperienceResult, loading: false }; this.draft = "";
      } catch (error) {
        if (this.overlay !== pending) return;
        this.overlay = { kind: "experience", data: {}, loading: false, error: String(error) };
      }
      this.changed(); return;
    }
    if (command === "/scope") {
      const pending: Overlay = { kind: "scope", preview: {}, loading: true };
      this.overlay = pending; this.changed();
      try {
        const preview = await this.control("session.scope.preview", { text });
        if (this.overlay !== pending) return;
        this.overlay = { kind: "scope", preview, loading: false }; this.draft = ""; this.changed();
      } catch (error) {
        if (this.overlay === pending) { this.dismiss(); this.notify(String(error)); }
      }
      return;
    }
    if (command === "/help") {
      try {
        const result = await this.control("session.help");
        this.commandMetadata = Array.isArray(result.commands) ? result.commands as Command[] : [];
        this.output("Help", helpText(this.commandList(), text.trim().slice(5).trim()), "markdown");
        this.draft = ""; this.changed();
      } catch (error) { this.notify(String(error)); }
      return;
    }
    try {
      if (command.startsWith("/") && this.taskCommands.includes(command.slice(1))) {
        const preview = await this.control("task.preview", { text });
        this.overlay = { kind: "task", preview, original: text };
      } else {
        if (!command.startsWith("/") && !answering) this.activity = null;
        const request_id = this.id();
        if (command === "/report") this.reportRequests.add(request_id);
        this.send({ protocol_version: 2, type: "submit_input", request_id,
          task_id: this.id(), payload: { text } });
        this.draft = "";
      }
      this.changed();
    } catch (error) { this.notify(String(error)); }
  }
  startTask(task: TaskRequest): void {
    if (this.permission === "read_only") { this.notify(t("Read-Only: change permission before running a task.")); return; }
    try {
      this.send({ protocol_version: 2, type: "start_task", request_id: this.id(),
        task_id: this.id(), payload: { task } });
      this.activity = "task"; this.taskStatus = "Submitting";
      this.taskLabel = `/${task.command} ${task.target}`;
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
    if (!approval?.request_hash) { this.notify(t("Approval has no resolvable request ID")); return; }
    this.action("execution.approval.resolve", { request_hash: approval.request_hash, decision: approve ? "approve" : "deny" });
  }
  private showControl(operation: string, result: Record<string, unknown>): void {
    this.applyConfigDocument(result);
    if (operation === "config.read") this.overlay = { kind: "llm", document: result };
    else if (operation === "config.document") this.overlay = { kind: "config", document: result };
    else if (operation === "report.export") {
      this.output("Report exported", reportPaths(result.report).join("\n"));
    } else if (operation === "session.help") {
      this.commandMetadata = Array.isArray(result.commands) ? result.commands as Command[] : [];
      this.output("Help", helpText(this.commandList(), String(result.topic ?? "")), "markdown");
    } else if (operation === "session.scope.read") {
      this.overlay = { kind: "scope", preview: result, loading: false };
    } else if (operation.startsWith("experience.")) {
      this.overlay = { kind: "experience", data: result as ExperienceResult, loading: false };
    } else if (["learn", "feedback"].includes(operation)) {
      this.overlay = { kind: "data", title: operation, data: result };
    }
    if (operation === "report.export") this.notice = t("Report exported");
    else if (typeof result.message === "string") this.notice = operation === "config.update" ? t("Configuration saved.") : result.message;
  }
  private hydrate(state: StateSnapshot): void {
    this.state = state;
    if (state.permission_mode) this.permission = state.permission_mode;
    if (state.runtime) this.runtime = state.runtime;
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
  private execution(event: Extract<ServerEvent, { type: "tool_call" | "tool_result" | "tool_execution" }>): ExecutionRecord {
    const source = event.agent_id ?? "main";
    const callId = event.call_id ?? this.id();
    const key = JSON.stringify([event.task_id, source, callId]);
    const batch = event.type === "tool_call" ? event.dispatch_batch : undefined;
    if (batch && batch.call_ids.includes(callId)) {
      const batchKey = JSON.stringify([event.task_id, source, "dispatch", batch.id]);
      if (!this.executionCards.has(batchKey)) {
        const id = this.id();
        const members = new Map<string, ExecutionRecord>(batch.call_ids.map(memberId => [memberId, {
          taskId: event.task_id, callId: memberId, tool: "agent_run", arguments: "",
          status: "preparing", operations: new Map(), received: false,
        }]));
        const card: Card = { id, source, kind: "execution", title: "Tool", text: "", format: "text",
          execution: { taskId: event.task_id, callId: batch.id, tool: "agent_run", arguments: "",
            status: "preparing", operations: new Map(), received: false, members } };
        this.executionCards.set(batchKey, card);
        this.cards.set(id, card); this.entries.push({ kind: "card", id });
        for (const memberId of batch.call_ids) this.executionCards.set(JSON.stringify([event.task_id, source, memberId]), card);
      }
    }
    let card = this.executionCards.get(key);
    if (!card) {
      const id = this.id();
      card = { id, source, kind: "execution", title: "Tool", text: "", format: "text",
        execution: { taskId: event.task_id, callId, tool: "Tool", arguments: "", status: "preparing",
          operations: new Map(), received: false } };
      this.executionCards.set(key, card); this.cards.set(id, card); this.entries.push({ kind: "card", id });
    }
    return card.execution!.members?.get(callId) ?? card.execution!;
  }
  private finishExecutions(taskId: string | undefined, status: ExecutionStatus): void {
    for (const card of new Set(this.executionCards.values())) {
      const execution = card.execution!;
      if (taskId && execution.taskId !== taskId) continue;
      for (const member of execution.members?.values() ?? [execution]) {
        if (activeExecution(member.status)) member.status = status;
        for (const operation of member.operations.values()) if (activeExecution(operation.status)) operation.status = status;
        member.received = true;
      }
    }
  }
  receive = (event: ServerEvent): void => {
    if (event.type === "ready") {
      this.connected = true; this.commands = event.capabilities.input_commands ?? {};
      this.taskCommands = event.capabilities.authoritative_state ? event.capabilities.commands : [];
      this.permission = event.capabilities.permission_mode ?? "ask";
      this.runtime = event.runtime;
      this.skills = event.runtime.skills; this.notice = "";
    }
    if ("state" in event && event.state) this.hydrate(event.state);
    switch (event.type) {
      case "task_started":
        this.activity = "task"; this.taskLabel = `/${event.task.command} ${event.task.target}`; this.taskStatus = "Running";
        this.startedAt = Date.now(); this.source = "main"; this.agents.clear(); break;
      case "turn_started": this.activity = "conversation"; this.startedAt = Date.now(); break;
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
      case "tool_call": {
        const execution = this.execution(event);
        execution.tool = event.tool; execution.arguments = event.arguments; break;
      }
      case "tool_execution": {
        const execution = this.execution(event);
        if (event.kind === "tool") {
          execution.status = event.status;
          if (event.error !== undefined) execution.error = event.error;
          if (event.duration_ms !== undefined) execution.duration = event.duration_ms;
        } else execution.operations.set(event.operation_id, { ...execution.operations.get(event.operation_id), ...event });
        break;
      }
      case "tool_result": {
        const execution = this.execution(event);
        execution.result = event.result;
        this.executionCards.get(JSON.stringify([event.task_id, event.agent_id ?? "main", event.call_id ?? execution.callId]))!.format = event.format ?? "text";
        execution.structuredContent = event.structured_content;
        execution.received = true;
        if (event.evidence_id) execution.evidenceId = event.evidence_id;
        if (activeExecution(execution.status)) execution.status = "returned";
        break;
      }
      case "log": this.card(event.agent_id ?? "main", "log", "Log", event.message); break;
      case "status": this.card(event.agent_id ?? "main", "status", "Status", event.status); break;
      case "subagent": this.agents.set(event.agent_id, event); break;
      case "finding": {
        const index = this.findings.findIndex((finding) => finding.id === event.finding.id);
        if (index < 0) this.findings.push(event.finding); else this.findings[index] = event.finding;
        break;
      }
      case "approval_required":
        this.approvals.push(event);
        if (event.request_hash) this.approvalDeadlines.set(event.request_hash,
          event.expires_in_seconds !== undefined ? Date.now() + event.expires_in_seconds * 1000 : Date.parse(event.expires_at ?? ""));
        break;
      case "approval_closed": this.approvals = this.approvals.filter((item) => item.request_hash !== event.request_hash); this.approvalDeadlines.delete(event.request_hash); this.notice = t("Approval {status}", { status: statusLabel(event.status) }); break;
      case "control_result": {
        if (event.request_id) this.reportRequests.delete(event.request_id);
        const result = record(event.result);
        const requested = event.request_id ? this.requests.has(event.request_id) : false;
        if (event.request_id) { this.requests.get(event.request_id)?.resolve(result); this.requests.delete(event.request_id); }
        if (["experience.approve", "experience.reject", "experience.edit"].includes(event.operation)) {
          const lesson = (result as ExperienceResult).lesson;
          if (lesson) this.card("main", "result", event.operation === "experience.edit" ? "Experience updated" : "Experience reviewed",
            `${t("Lesson ID")}: ${lesson.id}\n${t("Status")}: ${t(experienceStatusLabels[lesson.status])}`);
        }
        if (event.operation === "session.permission.set" && typeof result.mode === "string") this.permission = result.mode;
        if (event.operation === "session.think" && typeof result.show_thinking === "boolean") this.showReasoning = result.show_thinking;
        if (event.operation === "config.write" && this.runtime) this.runtime = { ...this.runtime,
          provider: String(result.provider ?? this.runtime.provider), model: String(result.model ?? this.runtime.model), config_ready: result.config_ready === true };
        if (event.operation === "config.update") {
          const config = record(result.config), llm = record(config.llm);
          if (this.runtime) this.runtime = { ...this.runtime, provider: String(llm.provider ?? this.runtime.provider), model: String(llm.model ?? this.runtime.model) };
          const mode = record(config.safety).permission_mode;
          if (typeof mode === "string") this.permission = mode;
          const thinking = record(config.session).show_thinking;
          if (typeof thinking === "boolean") this.showReasoning = thinking;
        }
        if (event.operation === "session.new") { this.executionCards.clear(); this.messages.clear(); this.cards.clear(); this.entries = []; this.agents.clear(); this.source = "main"; this.activity = null; this.taskLabel = ""; this.taskStatus = ""; }
        if (!requested) this.showControl(event.operation, result);
        break;
      }
      case "task_completed":
      case "turn_completed": {
        this.finishExecutions(event.task_id, "returned");
        this.card("main", "result", event.type === "task_completed" ? "Task completed" : "Turn completed", completionText(event.result));
        if (event.type === "task_completed") this.taskStatus = "Completed";
        this.notice = t("Ready"); break;
      }
      case "task_cancelled": case "turn_cancelled": this.finishExecutions(event.task_id, "cancelled"); this.taskStatus = "Cancelled"; this.notice = t("Cancelled. Enter another request."); break;
      case "task_failed": case "turn_failed": this.finishExecutions(event.task_id, "failed"); this.taskStatus = "Failed"; this.card("main", "error", "Failed", event.error.message); this.notice = event.error.message; break;
      case "error": {
        if (event.request_id && this.reportRequests.delete(event.request_id)) this.output("Report failed", event.message, "text", "error");
        if (event.request_id) { this.requests.get(event.request_id)?.reject(new Error(event.message)); this.requests.delete(event.request_id); }
        this.notice = event.message; break;
      }
      case "shutdown_complete": this.connected = false; break;
    }
    this.changed();
  };
  disconnect = (error: Error): void => {
    this.connected = false; this.notice = error.message; this.approvals = [];
    this.finishExecutions(undefined, "failed");
    for (const message of this.messages.values()) if (message.status === "streaming") message.status = "failed";
    for (const pending of this.requests.values()) pending.reject(error);
    this.requests.clear(); this.changed();
  };
}
