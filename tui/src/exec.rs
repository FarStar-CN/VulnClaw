use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::Sender;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use crate::protocol::{parse_backend_line, AppEvent, ClientRequest};

#[derive(Clone)]
pub struct BackendHandle {
    child: Arc<Mutex<Option<Child>>>,
    stdin: Arc<Mutex<ChildStdin>>,
}

impl BackendHandle {
    pub fn send(&self, request: &ClientRequest) -> std::io::Result<()> {
        let mut input = self
            .stdin
            .lock()
            .map_err(|_| std::io::Error::other("backend stdin lock poisoned"))?;
        serde_json::to_writer(&mut *input, request).map_err(std::io::Error::other)?;
        input.write_all(b"\n")?;
        input.flush()
    }

    /// Wait briefly for a graceful protocol shutdown, then force-reap the child.
    pub fn wait_or_kill(&self, timeout: Duration) {
        let deadline = Instant::now() + timeout;
        loop {
            let exited = {
                let mut guard = match self.child.lock() {
                    Ok(guard) => guard,
                    Err(_) => return,
                };
                match guard.as_mut() {
                    Some(child) => matches!(child.try_wait(), Ok(Some(_))),
                    None => true,
                }
            };
            if exited || Instant::now() >= deadline {
                break;
            }
            thread::sleep(Duration::from_millis(25));
        }
        if let Ok(mut guard) = self.child.lock() {
            if let Some(mut child) = guard.take() {
                if matches!(child.try_wait(), Ok(None)) {
                    let _ = child.kill();
                }
                let _ = child.wait();
            }
        }
    }
}

#[cfg_attr(test, allow(dead_code))]
pub fn spawn_backend(sender: Sender<AppEvent>) -> std::io::Result<BackendHandle> {
    let python = std::env::var("VULNCLAW_PYTHON").unwrap_or_else(|_| "python".to_owned());
    let mut command = if cfg!(windows) && python == "python" {
        let mut command = Command::new("py");
        command.arg("-3");
        command
    } else {
        Command::new(python)
    };
    command.arg("-m").arg("vulnclaw.tui_backend");
    spawn_backend_process(command, sender)
}

fn spawn_backend_process(
    mut command: Command,
    sender: Sender<AppEvent>,
) -> std::io::Result<BackendHandle> {
    let mut child = command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    let stdin = child.stdin.take().expect("stdin is piped");
    let stdout = child.stdout.take().expect("stdout is piped");
    let stderr = child.stderr.take().expect("stderr is piped");
    let child_handle = Arc::new(Mutex::new(Some(child)));
    let handle = BackendHandle {
        child: child_handle.clone(),
        stdin: Arc::new(Mutex::new(stdin)),
    };

    let output_sender = sender.clone();
    thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            match parse_backend_line(&line) {
                Ok(event) => {
                    let _ = output_sender.send(AppEvent::Backend(event));
                }
                Err(error) => {
                    let _ = output_sender.send(AppEvent::BackendDiagnostic(format!(
                        "invalid backend protocol line ({error}): {line}"
                    )));
                }
            }
        }
    });

    let diagnostic_sender = sender.clone();
    thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            let _ = diagnostic_sender.send(AppEvent::BackendDiagnostic(line));
        }
    });

    thread::spawn(move || loop {
        let next = {
            let mut guard = match child_handle.lock() {
                Ok(guard) => guard,
                Err(_) => break,
            };
            match guard.as_mut() {
                Some(child) => child.try_wait(),
                None => break,
            }
        };
        match next {
            Ok(Some(status)) => {
                let _ = sender.send(AppEvent::BackendExited(status.success()));
                break;
            }
            Ok(None) => thread::sleep(Duration::from_millis(100)),
            Err(error) => {
                let _ = sender.send(AppEvent::BackendDiagnostic(format!(
                    "could not wait for VulnClaw backend: {error}"
                )));
                break;
            }
        }
    });

    Ok(handle)
}

#[cfg(test)]
mod tests {
    use std::process::Command;
    use std::sync::mpsc;
    use std::time::Duration;

    use super::spawn_backend_process;
    use crate::protocol::{AppEvent, BackendEvent, ClientRequest};

    const FAKE_BACKEND: &str = r#"
import json, os, sys
constraints = {"allowed_ports": [], "blocked_ports": [], "allowed_hosts": [], "blocked_hosts": [], "allowed_paths": [], "blocked_paths": [], "allowed_actions": [], "blocked_actions": [], "notes": [], "strict_mode": False}
def state(active=False, task_id=None):
    return {"target": "target.test", "phase": "idle", "task_constraints": constraints, "task": {"active": active, "task_id": task_id}, "last_run": None, "findings": [], "evidence": [], "constraint_violations": []}
for line in sys.stdin:
    msg = json.loads(line)
    base = {"protocol_version": 1}
    if msg["type"] == "initialize":
        print(json.dumps(base | {"type": "ready", "request_id": msg["request_id"], "backend": {"pid": os.getpid(), "version": "test", "protocol_version": 1}, "capabilities": {"commands": ["run"], "control_operations": ["example.inspect"], "cancellation": True, "authoritative_state": True}, "runtime": {"config_ready": True, "provider": "test", "model": "test", "mcp_started": 0, "skills": []}, "state": state()}), flush=True)
    elif msg["type"] == "control":
        print(json.dumps(base | {"type": "control_result", "request_id": msg["request_id"], "operation": msg["payload"]["operation"], "result": {"backend_pid": os.getpid()}}), flush=True)
    elif msg["type"] == "start_task":
        task_id = msg["task_id"]
        print(json.dumps(base | {"type": "task_started", "request_id": msg["request_id"], "task_id": task_id, "command": "run", "normalized_command": msg["payload"]["command_line"], "target": "target.test", "resume": True, "constraints": constraints, "state": state(True, task_id)}), flush=True)
        print(json.dumps(base | {"type": "task_completed", "request_id": msg["request_id"], "task_id": task_id, "result": {"backend_pid": os.getpid()}, "findings": [], "state": state()}), flush=True)
    elif msg["type"] == "shutdown":
        print(json.dumps(base | {"type": "shutdown_complete", "request_id": msg["request_id"]}), flush=True)
        break
"#;

    #[test]
    fn one_transport_process_serves_two_sequential_tasks() {
        let python = if std::path::Path::new("../.venv/bin/python").exists() {
            "../.venv/bin/python"
        } else {
            "python"
        };
        let mut command = Command::new(python);
        command.arg("-u").arg("-c").arg(FAKE_BACKEND);
        let (sender, receiver) = mpsc::channel();
        let backend = spawn_backend_process(command, sender).unwrap();

        backend
            .send(&ClientRequest::initialize("r-init".into(), serde_json::json!({})))
            .unwrap();
        let ready_pid = loop {
            match receiver.recv_timeout(Duration::from_secs(3)).unwrap() {
                AppEvent::Backend(BackendEvent::Ready { backend, .. }) => break backend.pid,
                _ => continue,
            }
        };

        backend
            .send(&ClientRequest::control(
                "r-control".into(),
                "example.inspect",
                serde_json::json!({}),
            ))
            .unwrap();
        loop {
            match receiver.recv_timeout(Duration::from_secs(3)).unwrap() {
                AppEvent::Backend(BackendEvent::ControlResult { result, .. }) => {
                    assert_eq!(result["backend_pid"], ready_pid);
                    break;
                }
                _ => continue,
            }
        }

        for index in 1..=2 {
            backend
                .send(&ClientRequest::start_task(
                    format!("r-{index}"),
                    format!("t-{index}"),
                    format!("/run target-{index}.test"),
                ))
                .unwrap();
            loop {
                match receiver.recv_timeout(Duration::from_secs(3)).unwrap() {
                    AppEvent::Backend(BackendEvent::TaskCompleted {
                        task_id, result, ..
                    }) if task_id == format!("t-{index}") => {
                        assert_eq!(result["backend_pid"], ready_pid);
                        break;
                    }
                    _ => continue,
                }
            }
        }

        backend
            .send(&ClientRequest::shutdown("r-shutdown".into()))
            .unwrap();
        backend.wait_or_kill(Duration::from_secs(2));
    }
}
