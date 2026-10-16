use std::sync::mpsc;

use ratatui::{backend::TestBackend, Terminal};

use vulnclaw_tui::{
    app::{App, OperationKind, OperationReceipt},
    views::status::render,
};

use crate::support::AppHarness;

fn status_text(app: &App) -> String {
    vulnclaw_tui::views::status::build_lines(app)
        .iter()
        .map(ToString::to_string)
        .collect::<Vec<_>>()
        .join("\n")
}

#[test]
fn conversation_status_does_not_inherit_task_metadata() {
    let mut harness = AppHarness::connected();
    harness.app.insert_text("/run complete.test");
    harness.app.submit();
    harness.app.confirm_task();
    harness.apply_next();
    harness.apply_next();
    harness.app.last_run = Some(serde_json::json!({"run": {"name": "audit-1"}}));
    harness.app.phase = "reporting".into();

    harness.app.insert_text("解释之前的发现");
    harness.app.submit();
    for _ in 0..3 {
        let text = status_text(&harness.app);
        assert!(text.contains("Conversation"), "{text}");
        assert!(!text.contains("Execution receipt"), "{text}");
        assert!(!text.contains("Phase"), "{text}");
        assert!(!text.contains("Last run"), "{text}");
        assert!(!text.contains("Found"), "{text}");
        assert!(!text.contains("Running"), "{text}");
        harness.apply_next();
    }
    let text = status_text(&harness.app);
    assert!(text.contains("Conversation"), "{text}");
    assert!(text.contains("State       Ready"), "{text}");
    assert!(text.contains("Last message"), "{text}");
    assert!(!text.contains("finding(s)"), "{text}");

    harness.app.insert_text("/clear");
    harness.app.submit();
    assert_eq!(status_text(&harness.app), text);
}

#[test]
fn new_conversation_clears_the_previous_activity() {
    let mut harness = AppHarness::connected();
    harness.app.insert_text("/run complete.test");
    harness.app.submit();
    harness.app.confirm_task();
    harness.apply_next();
    harness.apply_next();
    harness
        .app
        .layout
        .view_mut(vulnclaw_tui::workbench::ViewId::Status)
        .scroll = 5;
    harness.app.insert_text("/new");
    harness.app.submit();
    harness.apply_next();

    let text = status_text(&harness.app);
    assert!(text.contains("Conversation"), "{text}");
    assert!(text.contains("Ready"), "{text}");
    assert!(!text.contains("complete.test"), "{text}");
    assert!(harness.app.last_receipt.is_none());
    assert_eq!(
        harness
            .app
            .layout
            .view(vulnclaw_tui::workbench::ViewId::Status)
            .scroll,
        0
    );
}

#[test]
fn renders_authoritative_backend_and_scope_state() {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.backend_ready = true;
    app.backend_pid = Some(42);
    app.target = "app.test".into();
    app.phase = "executing".into();
    app.worker_active = true;
    app.active_receipt = Some(OperationReceipt {
        kind: OperationKind::Task,
        input: "/run app.test".into(),
        phase: "Running".into(),
        findings: 2,
    });
    app.task_constraints = serde_json::json!({
        "allowed_hosts": ["app.test"],
        "allowed_ports": [80, 443],
        "allowed_actions": ["scan"]
    });
    app.evidence = vec![serde_json::json!({"path": "evidence.json"})];
    app.constraint_violations = vec!["blocked host".into()];
    app.last_run = Some(serde_json::json!({"run": {"name": "audit-1"}}));
    let mut terminal = Terminal::new(TestBackend::new(60, 30)).unwrap();

    terminal
        .draw(|frame| frame.render_widget(render(&app), frame.area()))
        .unwrap();

    let rendered = terminal
        .backend()
        .buffer()
        .content
        .iter()
        .map(|cell| cell.symbol())
        .collect::<String>();
    assert!(rendered.contains("Target      app.test"));
    assert!(rendered.contains("Task execution"));
    assert!(rendered.contains("Phase       executing"));
    assert!(rendered.contains("Found       2"));
    assert!(rendered.contains("Scope       H1 P2 A1"));
    assert!(rendered.contains("Violations  1"));
    assert!(rendered.contains("Last run    audit-1"));
}
