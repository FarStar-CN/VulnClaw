use serde::{Deserialize, Deserializer, Serialize};
use serde_json::Value;

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

    pub fn start_task(request_id: String, task_id: String, command_line: String) -> Self {
        Self {
            protocol_version: PROTOCOL_VERSION,
            kind: "start_task",
            request_id,
            task_id: Some(task_id),
            payload: serde_json::json!({"command_line": command_line}),
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
    #[serde(default)]
    pub pid: u32,
    #[serde(default)]
    pub version: String,
}

#[derive(Clone, Debug, Default, Deserialize)]
pub struct RuntimeInfo {
    #[serde(default)]
    pub config_ready: bool,
    #[serde(default)]
    pub provider: String,
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub skills: Vec<String>,
}

#[derive(Clone, Debug, Default, Deserialize)]
pub struct BackendCapabilities {
    #[serde(default)]
    pub commands: Vec<String>,
    #[serde(default)]
    pub control_operations: Vec<String>,
    #[serde(default)]
    pub cancellation: bool,
    #[serde(default)]
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
        #[serde(default)]
        backend: BackendInfo,
        #[serde(default)]
        capabilities: BackendCapabilities,
        #[serde(default)]
        runtime: RuntimeInfo,
        state: StateSnapshot,
    },
    State {
        state: StateSnapshot,
    },
    TaskStarted {
        task_id: String,
        #[serde(default)]
        command: String,
        #[serde(default)]
        normalized_command: String,
        #[serde(default, rename = "target")]
        _target: String,
        #[serde(default)]
        #[serde(rename = "resume")]
        _resume: bool,
        #[serde(default, rename = "constraints")]
        _constraints: Value,
        state: StateSnapshot,
    },
    Status {
        task_id: String,
        #[serde(default)]
        status: String,
    },
    Reasoning {
        task_id: String,
        #[serde(default)]
        text: String,
    },
    Log {
        task_id: String,
        #[serde(default)]
        message: String,
    },
    ToolCall {
        task_id: String,
        #[serde(default)]
        tool: String,
        #[serde(default)]
        arguments: String,
    },
    ToolResult {
        task_id: String,
        #[serde(default)]
        result: String,
    },
    Finding {
        task_id: String,
        finding: Finding,
    },
    ApprovalRequired {
        task_id: String,
        #[serde(default)]
        question: String,
    },
    TaskCompleted {
        task_id: String,
        #[serde(default)]
        result: Value,
        #[serde(default)]
        findings: Vec<Finding>,
        state: StateSnapshot,
    },
    TaskCancelled {
        task_id: String,
        state: StateSnapshot,
    },
    TaskFailed {
        task_id: String,
        #[serde(default)]
        error: Value,
        state: StateSnapshot,
    },
    ControlResult {
        #[serde(rename = "request_id")]
        _request_id: String,
        operation: String,
        #[serde(default)]
        result: Value,
        #[serde(default)]
        state: Option<StateSnapshot>,
    },
    Error {
        #[serde(default)]
        task_id: Option<String>,
        #[serde(default)]
        code: String,
        #[serde(default)]
        message: String,
    },
    ShutdownComplete,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct Finding {
    #[serde(default, alias = "finding_id")]
    pub id: String,
    #[serde(default)]
    pub severity: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub target: String,
    #[serde(default)]
    pub line: Option<u64>,
    #[serde(default)]
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

pub fn parse_backend_line(line: &str) -> Result<BackendEvent, String> {
    let value: Value = serde_json::from_str(line).map_err(|error| error.to_string())?;
    if value.get("protocol_version").and_then(Value::as_u64)
        != Some(PROTOCOL_VERSION.into())
    {
        return Err(format!(
            "unsupported backend protocol version; expected {PROTOCOL_VERSION}"
        ));
    }
    serde_json::from_value(value).map_err(|error| error.to_string())
}

#[cfg(test)]
mod tests {
    use super::{parse_backend_line, BackendEvent, ClientRequest, PROTOCOL_VERSION};

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
        let request = ClientRequest::start_task("r1".into(), "t1".into(), "/run host".into());
        let value = serde_json::to_value(request).unwrap();
        assert_eq!(value["protocol_version"], PROTOCOL_VERSION);
        assert_eq!(value["type"], "start_task");
        assert_eq!(value["request_id"], "r1");
        assert_eq!(value["task_id"], "t1");
        assert_eq!(value["payload"]["command_line"], "/run host");
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
            "task_id": "t1",
            "findings": [{
                "finding_id": "f1",
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
}
