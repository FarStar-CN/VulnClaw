use serde::{Deserialize, Deserializer, Serialize};
use serde_json::Value;
use std::sync::OnceLock;

pub const PROTOCOL_VERSION: u8 = 1;

#[derive(Clone, Debug)]
pub enum AppEvent {
    Backend(BackendEvent),
    BackendDiagnostic(String),
    BackendExited(bool),
}

#[derive(Clone, Debug, Serialize)]
pub struct ClientRequest {
    pub protocol_version: u8,
    #[serde(rename = "type")]
    pub kind: &'static str,
    pub request_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub task_id: Option<String>,
    pub payload: Value,
}

impl ClientRequest {
    pub fn initialize(request_id: String, bootstrap: Value) -> Self {
        Self {
            protocol_version: PROTOCOL_VERSION,
            kind: "initialize",
            request_id,
            task_id: None,
            payload: serde_json::json!({
                "client": {
                    "name": "vulnclaw-tui-native",
                    "version": env!("CARGO_PKG_VERSION")
                },
                "bootstrap": bootstrap
            }),
        }
    }

    pub fn start_task(request_id: String, task_id: String, task: Value) -> Self {
        Self {
            protocol_version: PROTOCOL_VERSION,
            kind: "start_task",
            request_id,
            task_id: Some(task_id),
            payload: serde_json::json!({"task": task}),
        }
    }

    pub fn cancel_task(request_id: String, task_id: String) -> Self {
        Self {
            protocol_version: PROTOCOL_VERSION,
            kind: "cancel_task",
            request_id,
            task_id: Some(task_id),
            payload: serde_json::json!({}),
        }
    }

    #[allow(dead_code)]
    pub fn get_state(request_id: String) -> Self {
        Self {
            protocol_version: PROTOCOL_VERSION,
            kind: "get_state",
            request_id,
            task_id: None,
            payload: serde_json::json!({}),
        }
    }

    pub fn control(request_id: String, operation: &str, arguments: Value) -> Self {
        Self {
            protocol_version: PROTOCOL_VERSION,
            kind: "control",
            request_id,
            task_id: None,
            payload: serde_json::json!({
                "operation": operation,
                "arguments": arguments
            }),
        }
    }

    pub fn shutdown(request_id: String) -> Self {
        Self {
            protocol_version: PROTOCOL_VERSION,
            kind: "shutdown",
            request_id,
            task_id: None,
            payload: serde_json::json!({}),
        }
    }
}

#[derive(Clone, Debug, Default, Deserialize)]
pub struct BackendInfo {
    pub pid: u32,
    pub version: String,
    #[serde(rename = "protocol_version")]
    pub _protocol_version: u8,
}

#[derive(Clone, Debug, Default, Deserialize)]
pub struct RuntimeInfo {
    pub config_ready: bool,
    pub provider: String,
    pub model: String,
    #[serde(rename = "mcp_started")]
    pub _mcp_started: u64,
    pub skills: Vec<String>,
}

#[derive(Clone, Debug, Default, Deserialize)]
pub struct BackendCapabilities {
    pub commands: Vec<String>,
    #[serde(default)]
    pub control_operations: Vec<String>,
    pub cancellation: bool,
    pub authoritative_state: bool,
}

#[derive(Clone, Debug, Default, Deserialize)]
pub struct BackendTaskState {
    pub active: bool,
    #[serde(deserialize_with = "deserialize_required_option")]
    pub task_id: Option<String>,
}

#[derive(Clone, Debug, Default, Deserialize)]
pub struct StateSnapshot {
    pub target: String,
    pub phase: String,
    pub task_constraints: Value,
    pub findings: Vec<Finding>,
    pub task: BackendTaskState,
    #[serde(deserialize_with = "deserialize_required_option")]
    pub last_run: Option<Value>,
    pub evidence: Vec<Value>,
    pub constraint_violations: Vec<String>,
}

fn deserialize_required_option<'de, D, T>(deserializer: D) -> Result<Option<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(deserializer)
}

#[derive(Clone, Debug, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum BackendEvent {
    Ready {
        request_id: String,
        backend: BackendInfo,
        capabilities: BackendCapabilities,
        runtime: RuntimeInfo,
        state: StateSnapshot,
    },
    State {
        request_id: Option<String>,
        state: StateSnapshot,
    },
    TaskStarted {
        request_id: String,
        task_id: String,
        task: Value,
        state: StateSnapshot,
    },
    Status {
        task_id: String,
        status: String,
    },
    Reasoning {
        task_id: String,
        text: String,
    },
    Log {
        task_id: String,
        message: String,
    },
    ToolCall {
        task_id: String,
        tool: String,
        arguments: String,
    },
    ToolResult {
        task_id: String,
        result: String,
    },
    Finding {
        task_id: String,
        finding: Finding,
    },
    ApprovalRequired {
        task_id: String,
        question: String,
    },
    TaskCompleted {
        request_id: String,
        task_id: String,
        result: Value,
        findings: Vec<Finding>,
        state: StateSnapshot,
    },
    TaskCancelled {
        request_id: String,
        task_id: String,
        state: StateSnapshot,
    },
    TaskFailed {
        request_id: String,
        task_id: String,
        error: Value,
        state: StateSnapshot,
    },
    ControlResult {
        request_id: String,
        operation: String,
        result: Value,
        state: Option<StateSnapshot>,
    },
    Error {
        request_id: Option<String>,
        task_id: Option<String>,
        code: String,
        message: String,
    },
    ShutdownComplete {
        request_id: String,
    },
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct Finding {
    pub id: String,
    pub severity: String,
    pub title: String,
    pub target: String,
    pub line: Option<u64>,
    pub code_location: Option<String>,
    #[serde(default)]
    pub chain_depends_on: Vec<String>,
}

impl Finding {
    pub fn summary(&self) -> String {
        let location = self
            .line
            .map(|line| format!("{}:{line}", self.target))
            .or_else(|| self.code_location.clone())
            .unwrap_or_else(|| self.target.clone());
        format!(
            "[{}] {} ({})",
            self.severity.to_uppercase(),
            self.title,
            location
        )
    }
}

static PROTOCOL_VALIDATOR: OnceLock<jsonschema::Validator> = OnceLock::new();

fn protocol_validator() -> &'static jsonschema::Validator {
    PROTOCOL_VALIDATOR.get_or_init(|| {
        let schema = serde_json::from_str(include_str!("../../protocol/tui-v1.schema.json"))
            .expect("embedded TUI protocol schema must be valid JSON");
        jsonschema::draft202012::new(&schema)
            .expect("embedded TUI protocol schema must be a valid Draft 2020-12 schema")
    })
}

pub(crate) fn validate_protocol_value(value: &Value) -> Result<(), String> {
    protocol_validator()
        .validate(value)
        .map_err(|error| format!("protocol schema violation: {error}"))
}

pub fn parse_backend_line(line: &str) -> Result<BackendEvent, String> {
    let value: Value = serde_json::from_str(line).map_err(|error| error.to_string())?;
    if value.get("protocol_version").and_then(Value::as_u64)
        != Some(PROTOCOL_VERSION.into())
    {
        return Err(format!(
            "unsupported backend protocol version; expected {PROTOCOL_VERSION}"
        ));
    }
    validate_protocol_value(&value)?;
    serde_json::from_value(value).map_err(|error| error.to_string())
}

#[cfg(test)]
mod tests {
    use super::{
        parse_backend_line, validate_protocol_value, BackendEvent, ClientRequest,
        PROTOCOL_VERSION,
    };

    fn complete_state() -> serde_json::Value {
        serde_json::json!({
            "target": "app.test",
            "phase": "reporting",
            "task_constraints": {},
            "task": {"active": false, "task_id": null},
            "last_run": null,
            "findings": [],
            "evidence": [],
            "constraint_violations": []
        })
    }

    #[test]
    fn start_request_has_version_and_task_identity() {
        let request = ClientRequest::start_task(
            "r1".into(),
            "t1".into(),
            serde_json::json!({"command": "run", "target": "host"}),
        );
        let value = serde_json::to_value(request).unwrap();
        assert_eq!(value["protocol_version"], PROTOCOL_VERSION);
        assert_eq!(value["type"], "start_task");
        assert_eq!(value["request_id"], "r1");
        assert_eq!(value["task_id"], "t1");
        assert_eq!(value["payload"]["task"]["command"], "run");
        assert_eq!(value["payload"]["task"]["target"], "host");
    }

    #[test]
    fn control_request_and_result_use_the_generic_management_envelope() {
        let request = ClientRequest::control(
            "r-control".into(),
            "example.inspect",
            serde_json::json!({"detail": true}),
        );
        let value = serde_json::to_value(request).unwrap();
        assert_eq!(value["type"], "control");
        assert_eq!(value["payload"]["operation"], "example.inspect");

        let event = parse_backend_line(
            r#"{"protocol_version":1,"type":"control_result","request_id":"r-control","operation":"example.inspect","result":{"ok":true}}"#,
        )
        .unwrap();
        match event {
            BackendEvent::ControlResult {
                operation, result, ..
            } => {
                assert_eq!(operation, "example.inspect");
                assert_eq!(result["ok"], true);
            }
            other => panic!("unexpected event: {other:?}"),
        }
    }

    #[test]
    fn completion_deserializes_authoritative_findings() {
        let line = serde_json::json!({
            "protocol_version": 1,
            "type": "task_completed",
            "request_id": "r1",
            "task_id": "t1",
            "findings": [{
                "id": "f1",
                "severity": "high",
                "title": "SQLi",
                "target": "app.test"
            }],
            "result": {},
            "state": complete_state()
        })
        .to_string();
        let event = parse_backend_line(&line).unwrap();
        match event {
            BackendEvent::TaskCompleted { task_id, findings, .. } => {
                assert_eq!(task_id, "t1");
                assert_eq!(findings[0].id, "f1");
            }
            other => panic!("unexpected event: {other:?}"),
        }
    }

    #[test]
    fn rejects_every_missing_authoritative_state_field() {
        for field in [
            "target",
            "phase",
            "task_constraints",
            "task",
            "last_run",
            "findings",
            "evidence",
            "constraint_violations",
        ] {
            let mut state = complete_state();
            state.as_object_mut().unwrap().remove(field);
            let line = serde_json::json!({
                "protocol_version": 1,
                "type": "state",
                "state": state
            })
            .to_string();
            assert!(
                parse_backend_line(&line).is_err(),
                "missing state.{field} must be rejected"
            );
        }

        let mut state = complete_state();
        state["task"].as_object_mut().unwrap().remove("task_id");
        let line = serde_json::json!({
            "protocol_version": 1,
            "type": "state",
            "state": state
        })
        .to_string();
        assert!(
            parse_backend_line(&line).is_err(),
            "missing state.task.task_id must be rejected"
        );
    }

    #[test]
    fn rejects_incompatible_backend_protocol() {
        let error = parse_backend_line(r#"{"protocol_version":2,"type":"shutdown_complete"}"#)
            .unwrap_err();
        assert!(error.contains("expected 1"));
    }

    #[test]
    fn every_rust_client_request_follows_the_authoritative_schema() {
        let requests = [
            ClientRequest::initialize("r-init".into(), serde_json::json!({})),
            ClientRequest::start_task(
                "r-start".into(),
                "t1".into(),
                serde_json::json!({"command": "run", "target": "target.test"}),
            ),
            ClientRequest::cancel_task("r-cancel".into(), "t1".into()),
            ClientRequest::get_state("r-state".into()),
            ClientRequest::control(
                "r-control".into(),
                "example.inspect",
                serde_json::json!({}),
            ),
            ClientRequest::shutdown("r-shutdown".into()),
        ];

        for request in requests {
            let value = serde_json::to_value(request).unwrap();
            validate_protocol_value(&value).unwrap();
        }
    }

    #[test]
    fn rust_accepts_every_server_event_in_the_shared_example_session() {
        const SERVER_EVENT_TYPES: &[&str] = &[
            "ready",
            "state",
            "task_started",
            "status",
            "reasoning",
            "log",
            "tool_call",
            "tool_result",
            "finding",
            "approval_required",
            "task_completed",
            "task_cancelled",
            "task_failed",
            "control_result",
            "error",
            "shutdown_complete",
        ];

        for line in include_str!("../../protocol/examples/tui-v1-session.jsonl").lines() {
            let value: serde_json::Value = serde_json::from_str(line).unwrap();
            validate_protocol_value(&value).unwrap();
            if value["type"]
                .as_str()
                .is_some_and(|kind| SERVER_EVENT_TYPES.contains(&kind))
            {
                parse_backend_line(line).unwrap();
            }
        }
    }

    #[test]
    fn rejects_schema_invalid_required_event_fields_before_deserializing() {
        let ready = serde_json::json!({
            "protocol_version": 1,
            "type": "ready",
            "request_id": "r1",
            "backend": {"pid": 7, "version": "test", "protocol_version": 1},
            "capabilities": {
                "commands": ["run"],
                "control_operations": [],
                "cancellation": true,
                "authoritative_state": true
            },
            "runtime": {
                "config_ready": true,
                "provider": "test",
                "model": "test",
                "mcp_started": 0,
                "skills": []
            },
            "state": complete_state()
        });

        for path in [
            &["request_id"][..],
            &["backend", "protocol_version"][..],
            &["runtime", "mcp_started"][..],
            &["capabilities", "commands"][..],
        ] {
            let mut invalid = ready.clone();
            let (field, parents) = path.split_last().unwrap();
            let mut parent = &mut invalid;
            for segment in parents {
                parent = parent.get_mut(*segment).unwrap();
            }
            parent.as_object_mut().unwrap().remove(*field);
            let error = parse_backend_line(&invalid.to_string()).unwrap_err();
            assert!(error.contains("protocol schema violation"), "{path:?}: {error}");
        }
    }
}
