/* Generated from protocol/session-v2.schema.json. Run bun run generate. */

/**
 * One UTF-8 JSON object per line over a local stdio session.
 */
export type VulnClawLocalSessionProtocolV2 = ClientMessage | ServerEvent;
export type ClientMessage =
  | SubmitInputRequest
  | InitializeRequest
  | StartTaskRequest
  | CancelTaskRequest
  | GetStateRequest
  | ControlRequest
  | ShutdownRequest;
export type SubmitInputRequest = TaskRequestEnvelope & {
  type: "submit_input";
  payload?: {
    text: string;
  };
};
export type TaskRequestEnvelope = RequestEnvelope & {
  task_id: Identifier;
};
export type RequestEnvelope = Envelope & {
  payload: {};
  request_id: Identifier;
};
export type Identifier = string;
export type InitializeRequest = RequestEnvelope & {
  type: "initialize";
  payload?: InitializePayload;
};
export type StartTaskRequest = TaskRequestEnvelope & {
  type: "start_task";
  payload?: StartTaskPayload;
};
export type CancelTaskRequest = TaskRequestEnvelope & {
  type: "cancel_task";
  payload?: EmptyPayload;
};
export type GetStateRequest = RequestEnvelope & {
  type: "get_state";
  payload?: EmptyPayload;
};
export type ControlRequest = RequestEnvelope & {
  type: "control";
  payload?: ControlPayload;
};
export type ShutdownRequest = RequestEnvelope & {
  type: "shutdown";
  payload?: EmptyPayload;
};
export type ServerEvent =
  | TurnStartedEvent
  | TurnCompletedEvent
  | TurnCancelledEvent
  | TurnFailedEvent
  | ReadyEvent
  | StateEvent
  | TaskStartedEvent
  | SubagentEvent
  | StatusEvent
  | MessageEvent
  | LogEvent
  | ToolCallEvent
  | ToolResultEvent
  | FindingEvent
  | ApprovalRequiredEvent
  | ApprovalClosedEvent
  | TaskCompletedEvent
  | TaskCancelledEvent
  | TaskFailedEvent
  | ControlResultEvent
  | ErrorEvent
  | ShutdownCompleteEvent
  | CommandResultEvent;
export type TurnStartedEvent = TaskEventEnvelope & {
  type: "turn_started";
  text: string;
  state: StateSnapshot;
};
export type TaskEventEnvelope = Envelope & {
  task_id: Identifier;
};
export type TurnCompletedEvent = TaskEventEnvelope & {
  type: "turn_completed";
  result: {};
  state: StateSnapshot;
};
export type TurnCancelledEvent = TaskEventEnvelope & {
  type: "turn_cancelled";
  state: StateSnapshot;
};
export type TurnFailedEvent = TaskEventEnvelope & {
  type: "turn_failed";
  error: StructuredError;
  state: StateSnapshot;
};
export type ReadyEvent = Envelope & {
  type: "ready";
  backend: BackendInfo;
  capabilities: Capabilities;
  runtime: RuntimeInfo;
  state: StateSnapshot;
};
export type StateEvent = Envelope & {
  type: "state";
  state: StateSnapshot;
};
export type TaskStartedEvent = TaskEventEnvelope & {
  type: "task_started";
  task: TaskRequest;
  state: StateSnapshot;
};
export type SubagentEvent = TaskEventEnvelope & {
  type: "subagent";
  agent_id: Identifier;
  parent_id: string;
  group_id: string;
  name: string;
  agent_type: string;
  status: "pending" | "running" | "completed" | "failed" | "killed";
};
export type StatusEvent = TaskEventEnvelope & {
  agent_id?: Identifier;
  type: "status";
  status: string;
};
export type MessageEvent = TaskEventEnvelope & {
  type: "message";
  message_id: Identifier;
  source: Identifier;
  agent_id?: Identifier;
  role: "assistant" | "reasoning" | "user" | "tool";
  format: "markdown" | "text" | "code" | "json" | "http";
  operation: "append" | "end";
  text?: string;
  status?: "completed" | "cancelled" | "failed";
} & (
    | {
        operation: "append";
        text: string;
      }
    | {
        operation: "end";
        status: "completed" | "cancelled" | "failed";
      }
  );
export type LogEvent = TaskEventEnvelope & {
  agent_id?: Identifier;
  type: "log";
  append?: boolean;
  message: string;
};
export type ToolCallEvent = TaskEventEnvelope & {
  agent_id?: Identifier;
  type: "tool_call";
  tool: string;
  arguments: string;
};
export type ToolResultEvent = TaskEventEnvelope & {
  agent_id?: Identifier;
  type: "tool_result";
  result: string;
  format?: "markdown" | "text" | "code" | "json" | "http";
};
export type FindingEvent = TaskEventEnvelope & {
  type: "finding";
  finding: Finding;
};
export type ApprovalRequiredEvent = TaskEventEnvelope & {
  type: "approval_required";
  question: string;
  request_hash?: string;
  kind?: string;
  cwd?: string;
  detail?: string;
  expires_at?: string;
  expires_in_seconds?: number;
  risk?: string;
};
export type ApprovalClosedEvent = TaskEventEnvelope & {
  type: "approval_closed";
  request_hash: string;
  status: "approved" | "denied" | "expired" | "cancelled";
};
export type TaskCompletedEvent = TaskEventEnvelope & {
  type: "task_completed";
  result: TaskResult;
  findings: Finding[];
  state: StateSnapshot;
};
export type TaskCancelledEvent = TaskEventEnvelope & {
  type: "task_cancelled";
  state: StateSnapshot;
};
export type TaskFailedEvent = TaskEventEnvelope & {
  type: "task_failed";
  error: StructuredError;
  state: StateSnapshot;
};
export type ControlResultEvent = Envelope & {
  type: "control_result";
  operation: string;
  result: {};
  state?: StateSnapshot;
};
export type ErrorEvent = Envelope & {
  type: "error";
  code: string;
  message: string;
};
export type ShutdownCompleteEvent = Envelope & {
  type: "shutdown_complete";
};
export type CommandResultEvent = Envelope & {
  type: "command_result";
  request_id: Identifier;
  status: "completed" | "failed" | "cancelled" | "requires_input" | "requires_approval";
  exit_code: 0 | 1 | 2 | 3;
  error: StructuredError | null;
  findings?: {
    verified: number;
    candidates: number;
  };
  artifacts?: {
    kind: string;
    path: string;
  }[];
  result?: {} | unknown[] | string | number | boolean | null;
};

export interface Envelope {
  protocol_version: 2;
  type: string;
  request_id?: Identifier;
  task_id?: Identifier;
}
export interface InitializePayload {
  client?: ClientInfo;
  bootstrap?: Bootstrap;
}
export interface ClientInfo {
  name?: string;
  version?: string;
}
export interface Bootstrap {
  target?: string;
  mode?: string;
  command?: "run" | "scan" | "recon" | "exploit" | "persistent" | "codescan";
  only_host?: string | null;
  allowed_ports?: number[] | null;
  blocked_ports?: number[] | null;
  only_port?: number | null;
  only_path?: string | null;
  blocked_host?: string | null;
  blocked_path?: string | null;
  /**
   * @maxItems 20
   */
  allow_actions?:
    | []
    | [string]
    | [string, string]
    | [string, string, string]
    | [string, string, string, string]
    | [string, string, string, string, string]
    | [string, string, string, string, string, string]
    | [string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string, string, string, string]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ];
  /**
   * @maxItems 20
   */
  block_actions?:
    | []
    | [string]
    | [string, string]
    | [string, string, string]
    | [string, string, string, string]
    | [string, string, string, string, string]
    | [string, string, string, string, string, string]
    | [string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string, string, string, string]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ];
  resume?: boolean;
}
export interface StartTaskPayload {
  task: TaskRequest;
}
export interface TaskRequest {
  command: "run" | "scan" | "recon" | "exploit" | "persistent" | "codescan";
  target: string;
  prompt?: string | null;
  resume?: boolean;
  snapshot_id?: string | null;
  run_name?: string | null;
  resume_run_name?: string | null;
  runs_dir?: string | null;
  /**
   * @maxItems 20
   */
  additional_targets?:
    | []
    | [string]
    | [string, string]
    | [string, string, string]
    | [string, string, string, string]
    | [string, string, string, string, string]
    | [string, string, string, string, string, string]
    | [string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string, string, string, string]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ];
  target_type?: string | null;
  mount?: boolean;
  repair?: boolean;
  force_fresh?: boolean;
  no_import?: boolean;
  options?: TaskOptions;
}
export interface TaskOptions {
  continuous?: boolean;
  engine?: "solve" | "team" | "rounds";
  scope?: string | null;
  ports?: string | null;
  max_steps?: number | null;
  max_directions?: number | null;
  max_tool_rounds?: number | null;
  max_parallel?: number | null;
  max_rounds?: number | null;
  rounds_per_cycle?: number | null;
  max_cycles?: number | null;
  poc_verify?: boolean | null;
  auto_report?: boolean | null;
  cve?: string | null;
  cmd?: string | null;
  allowed_ports?: number[] | null;
  blocked_ports?: number[] | null;
  only_port?: number | null;
  only_host?: string | null;
  only_path?: string | null;
  blocked_host?: string | null;
  blocked_path?: string | null;
  /**
   * @maxItems 20
   */
  allow_actions?:
    | []
    | [string]
    | [string, string]
    | [string, string, string]
    | [string, string, string, string]
    | [string, string, string, string, string]
    | [string, string, string, string, string, string]
    | [string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string, string, string, string]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | null;
  /**
   * @maxItems 20
   */
  block_actions?:
    | []
    | [string]
    | [string, string]
    | [string, string, string]
    | [string, string, string, string]
    | [string, string, string, string, string]
    | [string, string, string, string, string, string]
    | [string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string, string, string, string]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string
      ]
    | null;
  report_output?: string | null;
}
export interface EmptyPayload {}
export interface ControlPayload {
  operation: string;
  arguments: {};
}
export interface StateSnapshot {
  execution_mode?: "plan" | "agent";
  target: string;
  phase: string;
  task_constraints: TaskConstraints;
  task: BackendTaskState;
  last_run: {} | null;
  findings: Finding[];
  evidence: EvidenceRef[];
  constraint_violations: string[];
  messages?: MessageSnapshot[];
}
export interface TaskConstraints {
  allowed_ports?: number[];
  blocked_ports?: number[];
  allowed_hosts?: string[];
  blocked_hosts?: string[];
  allowed_paths?: string[];
  blocked_paths?: string[];
  allowed_actions?: string[];
  blocked_actions?: string[];
  notes?: string[];
  strict_mode?: boolean;
}
export interface BackendTaskState {
  active: boolean;
  waiting_for_input?: boolean;
  task_id: string | null;
}
export interface Finding {
  id: string;
  severity: string;
  title: string;
  target: string;
  vuln_type?: string;
  description?: string;
  impact?: string;
  evidence?: string;
  cve?: string | null;
  cvss?: number | null;
  cwe?: string | null;
  remediation?: string;
  endpoint?: string | null;
  method?: string | null;
  line?: number | null;
  code_location?: string | null;
  evidence_refs?: EvidenceRef[];
  skill_provenance?: {} | null;
  subagent_provenance?: {} | null;
  poc_script?: string | null;
  evidence_level?: string;
  lifecycle_status?: string;
  verified?: boolean;
  verification_status?: string;
  verified_at?: string | null;
  verification_note?: string;
  chain_depends_on?: string[];
}
export interface EvidenceRef {
  kind?: string;
  path?: string;
  request_id?: string | null;
}
export interface MessageSnapshot {
  message_id: Identifier;
  source: Identifier;
  request_id?: Identifier;
  task_id?: Identifier;
  role: "assistant" | "reasoning" | "user" | "tool";
  format: "markdown" | "text" | "code" | "json" | "http";
  text: string;
  status: "streaming" | "completed" | "cancelled" | "failed";
}
export interface StructuredError {
  code: string;
  message: string;
}
export interface BackendInfo {
  pid: number;
  version: string;
  protocol_version: 2;
}
export interface Capabilities {
  natural_language?: boolean;
  input_commands?: {
    [k: string]: string;
  };
  commands: string[];
  control_operations?: string[];
  cancellation: boolean;
  authoritative_state: boolean;
  permission_mode?: "ask" | "auto_review" | "full_access";
}
export interface RuntimeInfo {
  config_ready: boolean;
  provider: string;
  model: string;
  mcp_started: number;
  skills: string[];
}
export interface TaskResult {
  report?: {
    path: string;
    findings_json: string;
    findings_sarif: string;
  } | null;
  report_error?: string | null;
  command?: string;
  target?: string;
  status?: string;
  exit_code?: number;
  summary?: {};
  run?: {
    name?: string;
    directory?: string;
    manifest?: {};
  } | null;
  findings?: Finding[];
}
