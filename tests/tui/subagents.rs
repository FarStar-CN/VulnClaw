use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::layout::Rect;
use serde_json::{json, Value};
use std::sync::mpsc;
use vulnclaw_tui::{
    app::TranscriptKind,
    events::handle_key,
    protocol::parse_backend_line,
    ui::{subagents, transcript},
    workbench::ViewId,
    App, AppEvent,
};

fn app() -> App {
    let (tx, _) = mpsc::channel();
    let mut app = App::new_disconnected(tx);
    app.active_task_id = Some("t1".into());
    app.worker_active = true;
    app.terminal_size = Rect::new(0, 0, 140, 40);
    app.transcript.clear();
    app
}

fn emit(app: &mut App, mut value: Value) {
    value["protocol_version"] = json!(1);
    if value.get("task_id").is_none() {
        value["task_id"] = json!("t1");
    }
    app.apply_event(AppEvent::backend(
        parse_backend_line(&value.to_string()).unwrap(),
    ));
}

fn register(app: &mut App, id: &str, parent: &str, group: &str, role: &str) {
    emit(
        app,
        json!({"type":"subagent", "agent_id":id, "parent_id":parent,
        "group_id":group, "name":id, "agent_type":role, "status":"running"}),
    );
}

fn key(app: &mut App, code: KeyCode) {
    handle_key(app, KeyEvent::new(code, KeyModifiers::NONE));
}

#[test]
fn teams_form_expanded_trees_and_keys_select_agents_without_submitting_composer() {
    let mut app = app();
    register(&mut app, "team1", "main", "team1", "group-leader");
    register(&mut app, "sub3", "main", "", "general");
    register(&mut app, "sub1", "team1", "team1", "executor");
    register(&mut app, "sub2", "team1", "team1", "verifier");
    let rows = app.subagents.rows();
    assert_eq!(
        rows.iter().map(|r| r.label.as_str()).collect::<Vec<_>>(),
        vec![
            "main",
            "team1 [team1]",
            "  └─ team1 (leader) · running",
            "     ├─ sub1 (executor) · running",
            "     └─ sub2 (verifier) · running",
            "sub3 (general) · running",
        ]
    );
    app.layout.focus = ViewId::Subagents;
    app.input = "/run draft.test".into();
    key(&mut app, KeyCode::Down);
    assert_eq!(app.subagents.selection.as_deref(), Some("team1"));
    key(&mut app, KeyCode::Down);
    key(&mut app, KeyCode::Enter);
    assert_eq!(app.subagents.viewing.as_deref(), Some("sub1"));
    assert_eq!(app.input, "/run draft.test");
    assert!(app.pending_task.is_none());
    // New members do not move selection away from the selected identity.
    register(&mut app, "sub4", "team1", "team1", "executor");
    assert_eq!(app.subagents.selection.as_deref(), Some("sub1"));
    key(&mut app, KeyCode::Up);
    key(&mut app, KeyCode::Up);
    key(&mut app, KeyCode::Enter);
    assert!(app.subagents.viewing.is_none());
    assert!(subagents::build_lines(&app)[0]
        .to_string()
        .contains("main ◀"));
}

#[test]
fn interleaved_live_streams_use_the_same_transcript_renderer_and_stay_isolated() {
    let mut app = app();
    register(&mut app, "a", "main", "", "general");
    register(&mut app, "b", "main", "", "general");
    let events = [
        json!({"type":"reasoning", "text":"inspect ", "append":false}),
        json!({"type":"reasoning", "text":"headers", "append":true}),
        json!({"type":"tool_call", "tool":"fetch", "arguments":"{}"}),
        json!({"type":"tool_result", "result":"200 OK"}),
        json!({"type":"log", "message":"hello ", "append":false}),
        json!({"type":"log", "message":"world", "append":true}),
    ];
    for event in events {
        emit(&mut app, event.clone());
        let mut child = event;
        child["agent_id"] = json!("a");
        emit(&mut app, child);
        emit(
            &mut app,
            json!({"type":"reasoning", "agent_id":"b", "text":"other"}),
        );
    }
    assert_eq!(app.transcript.len(), 4);
    assert_eq!(app.transcript[0].text, "inspect headers");
    assert_eq!(app.transcript[3].text, "hello world");
    let main_lines = transcript::build_lines(&app);
    app.subagents.selection = Some("a".into());
    app.open_selected_subagent();
    assert_eq!(transcript::build_lines(&app), main_lines);
    assert!(matches!(
        app.visible_transcript()[0].kind,
        TranscriptKind::Reasoning
    ));
    app.show_reasoning = false;
    assert_eq!(transcript::build_lines(&app).len(), 3);
    emit(
        &mut app,
        json!({"type":"subagent", "agent_id":"a", "parent_id":"main",
        "group_id":"", "name":"a", "agent_type":"general", "status":"failed"}),
    );
    assert_eq!(app.visible_transcript().len(), 4);
    emit(
        &mut app,
        json!({"type":"log", "task_id":"old-task", "agent_id":"a", "message":"stale"}),
    );
    assert_eq!(app.visible_transcript().len(), 4);
    assert_eq!(app.subagents.agents[1].transcript.len(), 6);
}

#[test]
fn switching_transcripts_preserves_independent_scroll_and_selection_stays_visible() {
    let mut app = app();
    for i in 0..30 {
        register(&mut app, &format!("a{i}"), "main", "", "general");
    }
    app.layout.output.scroll = 7;
    app.layout.output.follow = false;
    app.subagents.selection = Some("a0".into());
    app.open_selected_subagent();
    assert!(app.layout.output.follow);
    app.layout.output.scroll = 2;
    app.layout.output.follow = false;
    app.subagents.selection = None;
    app.open_selected_subagent();
    assert_eq!(app.layout.output.scroll, 7);
    assert!(!app.layout.output.follow);
    app.subagents.selection = Some("a0".into());
    app.open_selected_subagent();
    assert_eq!(app.layout.output.scroll, 2);
    for _ in 0..35 {
        app.move_subagent_selection(true);
    }
    assert_eq!(app.subagents.selection.as_deref(), Some("a29"));
    assert!(app.layout.view(ViewId::Subagents).scroll > 0);
}
