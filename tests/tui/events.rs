use std::sync::mpsc;
use vulnclaw_tui::workbench::ViewId;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};

use vulnclaw_tui::{
    app::{App, ExecutionMode, PermissionMode},
    events::{handle_key, handle_mouse},
};

#[test]
fn tab_cycles_execution_mode_and_shift_tab_cycles_permission() {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);

    handle_key(&mut app, KeyEvent::new(KeyCode::Tab, KeyModifiers::NONE));
    handle_key(
        &mut app,
        KeyEvent::new(KeyCode::BackTab, KeyModifiers::SHIFT),
    );

    // Default posture is Agent; one Tab cycles to the read-only Plan.
    // Offline permission cycling is rejected (backend-owned policy).
    assert_eq!(app.mode, ExecutionMode::Plan);
    assert_eq!(app.permission, PermissionMode::Ask);
}

fn mouse(app: &mut App, kind: MouseEventKind, x: u16, y: u16) {
    handle_mouse(
        app,
        MouseEvent {
            kind,
            column: x,
            row: y,
            modifiers: KeyModifiers::NONE,
        },
    );
}

#[test]
fn wheel_routes_to_hovered_content_and_preserves_other_view_scrolls_and_focus() {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.terminal_size = ratatui::layout::Rect::new(0, 0, 120, 30);
    app.findings = (0..30)
        .map(|_| vulnclaw_tui::protocol::Finding::default())
        .collect();
    app.layout.primary.push(app.layout.secondary.remove(0));
    app.layout.focus = ViewId::Output;
    app.layout.output.scroll = 2;
    app.layout.output.follow = false;
    let geometry = app.geometry(app.terminal_size);
    let findings = geometry.view(ViewId::Findings).unwrap();
    let status = geometry.view(ViewId::Status).unwrap();
    mouse(
        &mut app,
        MouseEventKind::ScrollDown,
        findings.content.x,
        findings.content.y,
    );
    assert_eq!(app.layout.view(ViewId::Findings).scroll, 1);
    assert_eq!(app.layout.view(ViewId::Status).scroll, 0);
    assert_eq!(app.layout.output.scroll, 2);
    assert_eq!(app.layout.focus, ViewId::Output);
    mouse(
        &mut app,
        MouseEventKind::ScrollDown,
        status.content.x,
        status.content.y,
    );
    assert_eq!(app.layout.view(ViewId::Status).scroll, 1);
    assert_eq!(app.layout.view(ViewId::Findings).scroll, 1);
    for rect in [findings.title, geometry.header, geometry.sashes[0].rect] {
        mouse(&mut app, MouseEventKind::ScrollDown, rect.x, rect.y);
    }
    assert_eq!(app.layout.view(ViewId::Findings).scroll, 1);
    assert_eq!(app.layout.output.scroll, 2);
}

#[test]
fn dragging_commits_on_release_and_escape_restores_sash_sizes() {
    use vulnclaw_tui::workbench::{ContainerId, Gesture, SashId};
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.terminal_size = ratatui::layout::Rect::new(0, 0, 120, 30);
    let geometry = app.geometry(app.terminal_size);
    let title = geometry.view(ViewId::Status).unwrap().title;
    let target = geometry.container(ContainerId::Secondary);
    mouse(
        &mut app,
        MouseEventKind::Down(MouseButton::Left),
        title.x + 3,
        title.y,
    );
    mouse(
        &mut app,
        MouseEventKind::Drag(MouseButton::Left),
        target.x + 3,
        target.y,
    );
    assert_eq!(app.layout.primary[0].id, ViewId::Status);
    assert!(matches!(
        app.layout_gesture,
        Some(Gesture::Move {
            target: Some(_),
            ..
        })
    ));
    mouse(
        &mut app,
        MouseEventKind::Up(MouseButton::Left),
        target.x + 3,
        target.y,
    );
    assert!(app.layout.primary.is_empty());
    assert_eq!(app.layout.secondary[0].id, ViewId::Status);
    assert_eq!(app.layout.focus, ViewId::Status);
    assert!(app.layout_gesture.is_none());

    let geometry = app.geometry(app.terminal_size);
    let sash = geometry
        .sashes
        .iter()
        .find(|sash| sash.id == SashId::Primary)
        .unwrap()
        .rect;
    let width = app.layout.primary_width;
    mouse(
        &mut app,
        MouseEventKind::Down(MouseButton::Left),
        sash.x,
        sash.y,
    );
    mouse(
        &mut app,
        MouseEventKind::Drag(MouseButton::Left),
        sash.x + 4,
        sash.y,
    );
    assert_eq!(app.layout.primary_width, width + 4);
    handle_key(&mut app, KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE));
    assert_eq!(app.layout.primary_width, width);
    assert!(app.layout_gesture.is_none());

    // Resize at the upper view's bottom edge, then reorder using the next title.
    let geometry = app.geometry(app.terminal_size);
    let upper = geometry.view(ViewId::Status).unwrap().rect;
    let lower = geometry.view(ViewId::Findings).unwrap().rect;
    assert_eq!(upper.bottom(), lower.y);
    mouse(
        &mut app,
        MouseEventKind::Down(MouseButton::Left),
        upper.x + 3,
        upper.bottom() - 1,
    );
    assert!(matches!(
        app.layout_gesture,
        Some(Gesture::Resize {
            sash: SashId::Views(ContainerId::Secondary, 0),
            ..
        })
    ));
    mouse(
        &mut app,
        MouseEventKind::Drag(MouseButton::Left),
        upper.x + 3,
        upper.bottom(),
    );
    mouse(
        &mut app,
        MouseEventKind::Up(MouseButton::Left),
        upper.x + 3,
        upper.bottom(),
    );
    let geometry = app.geometry(app.terminal_size);
    assert_eq!(
        geometry.view(ViewId::Status).unwrap().rect.height,
        upper.height + 1
    );
    assert_eq!(
        geometry.view(ViewId::Findings).unwrap().rect.height,
        lower.height - 1
    );
    let title = geometry.view(ViewId::Findings).unwrap().title;
    mouse(
        &mut app,
        MouseEventKind::Down(MouseButton::Left),
        title.x + 3,
        title.y,
    );
    assert!(matches!(
        app.layout_gesture,
        Some(Gesture::Move {
            id: ViewId::Findings,
            ..
        })
    ));
    mouse(
        &mut app,
        MouseEventKind::Drag(MouseButton::Left),
        upper.x + 3,
        upper.y,
    );
    mouse(
        &mut app,
        MouseEventKind::Up(MouseButton::Left),
        upper.x + 3,
        upper.y,
    );
    assert_eq!(app.layout.secondary[0].id, ViewId::Findings);

    // A collapsed title remains both clickable and draggable next to a resize edge.
    let geometry = app.geometry(app.terminal_size);
    let title = geometry.view(ViewId::Status).unwrap().title;
    mouse(
        &mut app,
        MouseEventKind::Down(MouseButton::Left),
        title.x + 1,
        title.y,
    );
    mouse(
        &mut app,
        MouseEventKind::Up(MouseButton::Left),
        title.x + 1,
        title.y,
    );
    assert!(app.layout.view(ViewId::Status).collapsed);
    mouse(
        &mut app,
        MouseEventKind::Down(MouseButton::Left),
        title.x + 3,
        title.y,
    );
    assert!(matches!(
        app.layout_gesture,
        Some(Gesture::Move {
            id: ViewId::Status,
            ..
        })
    ));
    let target = app
        .geometry(app.terminal_size)
        .container(ContainerId::Primary);
    mouse(
        &mut app,
        MouseEventKind::Drag(MouseButton::Left),
        target.x + 3,
        target.y,
    );
    mouse(
        &mut app,
        MouseEventKind::Up(MouseButton::Left),
        target.x + 3,
        target.y,
    );
    assert_eq!(app.layout.primary[0].id, ViewId::Status);
    assert!(app.layout.primary[0].collapsed);
    assert_eq!(app.layout.focus, ViewId::Status);
}

#[test]
fn modal_and_attack_chain_capture_workbench_mouse_and_paste_events() {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.terminal_size = ratatui::layout::Rect::new(0, 0, 120, 30);
    app.findings = (0..30)
        .map(|_| vulnclaw_tui::protocol::Finding::default())
        .collect();
    let content = app
        .geometry(app.terminal_size)
        .view(ViewId::Findings)
        .unwrap()
        .content;
    app.pending_task = Some("/run target.test".into());
    mouse(&mut app, MouseEventKind::ScrollDown, content.x, content.y);
    assert_eq!(app.layout.view(ViewId::Findings).scroll, 0);
    app.pending_task = None;
    app.show_attack_chain = true;
    mouse(&mut app, MouseEventKind::ScrollDown, content.x, content.y);
    assert_eq!(app.layout.view(ViewId::Findings).scroll, 0);
    app.show_attack_chain = false;
    app.active_task_id = Some("task-1".into());
    app.apply_event(approval_event("task-1", "whoami"));
    mouse(
        &mut app,
        MouseEventKind::Down(MouseButton::Left),
        content.x,
        content.y,
    );
    mouse(&mut app, MouseEventKind::ScrollDown, content.x, content.y);
    assert_eq!(app.layout.view(ViewId::Findings).scroll, 0);
    assert_eq!(app.layout.focus, ViewId::Output);
    assert!(app.pending_execution.is_some());
    vulnclaw_tui::events::handle_paste(&mut app, "/run pasted.example");
    assert!(app.input.is_empty());
    app.pending_execution = None;
    vulnclaw_tui::events::handle_paste(&mut app, "/help");
    assert_eq!(app.input, "/help");
}

#[test]
fn arrows_scroll_the_selected_inspector() {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.terminal_size = ratatui::layout::Rect::new(0, 0, 120, 28);
    app.findings = (0..30)
        .map(|_| vulnclaw_tui::protocol::Finding::default())
        .collect();
    app.layout.focus = ViewId::Findings;

    handle_key(&mut app, KeyEvent::new(KeyCode::Down, KeyModifiers::NONE));

    assert_eq!(app.layout.view(ViewId::Findings).scroll, 1);
    assert_eq!(app.layout.output.scroll, 0);
}

#[test]
fn task_confirmation_captures_shortcuts() {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.pending_task = Some("/run target.test".into());

    handle_key(&mut app, KeyEvent::new(KeyCode::Tab, KeyModifiers::NONE));

    // While a task confirmation is pending, Tab is swallowed by the
    // confirmation prompt and must not change the execution mode.
    assert_eq!(app.mode, ExecutionMode::Agent);
    assert!(app.pending_task.is_some());
}

#[test]
fn enter_executes_an_exact_command_instead_of_refilling_it() {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.input = "/plan".into();

    handle_key(&mut app, KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));

    assert!(app.input.is_empty());
    assert!(app.transcript.iter().any(|item| item.text == "> /plan"));
}

#[test]
fn cursor_and_history_shortcuts_edit_the_composer() {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.input = "/helpx".into();
    app.input_cursor = app.input.len();

    handle_key(&mut app, KeyEvent::new(KeyCode::Left, KeyModifiers::NONE));
    handle_key(&mut app, KeyEvent::new(KeyCode::Delete, KeyModifiers::NONE));
    handle_key(&mut app, KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
    handle_key(
        &mut app,
        KeyEvent::new(KeyCode::Char('p'), KeyModifiers::CONTROL),
    );

    assert_eq!(app.input, "/help");
}

#[test]
fn ctrl_c_requests_task_cancel_without_quitting_or_killing_backend() {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.worker_active = true;
    app.active_task_id = Some("t1".into());

    handle_key(
        &mut app,
        KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL),
    );

    assert!(
        app.worker_active,
        "task remains active until Python acknowledges cancellation"
    );
    assert!(
        app.running,
        "TUI should stay open after requesting cancellation"
    );
}

#[test]
fn ctrl_c_quits_when_no_worker_is_running() {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.worker_active = false;

    handle_key(
        &mut app,
        KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL),
    );

    assert!(!app.running);
}

// ── Execution approval modal (C-1/C-2) ─────────────────────────────────

use vulnclaw_tui::app::PendingExecution;

fn approval_event(task_id: &str, command: &str) -> vulnclaw_tui::protocol::AppEvent {
    vulnclaw_tui::protocol::AppEvent::backend(
        vulnclaw_tui::protocol::BackendEvent::ApprovalRequired {
            task_id: task_id.to_string(),
            question: command.to_string(),
            request_hash: "a".repeat(64),
            kind: "shell".to_string(),
            cwd: "/tmp/target".to_string(),
            detail: "auto-review: unknown command".to_string(),
            expires_at: "2026-08-23T07:00:00+00:00".to_string(),
            expires_in_seconds: 300,
            risk: "not sandboxed".to_string(),
        },
    )
}

#[test]
fn structured_approval_opens_modal_and_swallows_typing() {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.active_task_id = Some("task-1".into());
    app.apply_event(approval_event("task-1", "whoami"));

    assert!(app.pending_execution.is_some(), "modal must open");

    // While the modal is open, ordinary typing must NOT reach the composer.
    handle_key(
        &mut app,
        KeyEvent::new(KeyCode::Char('a'), KeyModifiers::NONE),
    );
    assert_eq!(app.input, "");
}

#[test]
fn approval_modal_supports_line_and_page_scrolling() {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.terminal_size = ratatui::layout::Rect::new(0, 0, 60, 18);
    app.active_task_id = Some("task-1".into());
    let command = (0..30)
        .map(|index| format!("line-{index:02}"))
        .collect::<Vec<_>>()
        .join("\n");
    app.apply_event(approval_event("task-1", &command));

    handle_key(&mut app, KeyEvent::new(KeyCode::Down, KeyModifiers::NONE));
    assert_eq!(app.pending_execution.as_ref().unwrap().scroll_offset, 1);
    handle_key(&mut app, KeyEvent::new(KeyCode::Up, KeyModifiers::NONE));
    assert_eq!(app.pending_execution.as_ref().unwrap().scroll_offset, 0);
    handle_key(
        &mut app,
        KeyEvent::new(KeyCode::PageDown, KeyModifiers::NONE),
    );
    assert!(app.pending_execution.as_ref().unwrap().scroll_offset > 1);
    handle_key(&mut app, KeyEvent::new(KeyCode::PageUp, KeyModifiers::NONE));
    assert_eq!(app.pending_execution.as_ref().unwrap().scroll_offset, 0);
}

#[test]
fn y_approves_and_clears_modal() {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.active_task_id = Some("task-1".into());
    app.apply_event(approval_event("task-1", "id"));

    handle_key(
        &mut app,
        KeyEvent::new(KeyCode::Char('y'), KeyModifiers::NONE),
    );

    assert!(app.pending_execution.is_none());
    assert!(
        app.transcript.iter().any(|i| i.text.contains("已提交批准")),
        "approval submission must be visible"
    );
}

#[test]
fn esc_denies_by_default() {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.active_task_id = Some("task-1".into());
    app.apply_event(approval_event("task-1", "sudo rm -rf /"));

    handle_key(&mut app, KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE));
    assert!(app.pending_execution.is_none());
    assert!(
        app.transcript.iter().any(|i| i.text.contains("已提交拒绝")),
        "deny must be the default decision"
    );
}

#[test]
fn legacy_question_only_event_does_not_open_modal() {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.active_task_id = Some("task-1".into());
    app.apply_event(vulnclaw_tui::protocol::AppEvent::backend(
        vulnclaw_tui::protocol::BackendEvent::ApprovalRequired {
            task_id: "task-1".into(),
            question: "old style ask_user".into(),
            request_hash: String::new(),
            expires_in_seconds: 0,
            kind: String::new(),
            cwd: String::new(),
            detail: String::new(),
            expires_at: String::new(),
            risk: String::new(),
        },
    ));
    assert!(app.pending_execution.is_none());

    // Typing still reaches the composer for legacy questions.
    handle_key(
        &mut app,
        KeyEvent::new(KeyCode::Char('x'), KeyModifiers::NONE),
    );
    assert_eq!(app.input, "x");
}

#[test]
fn task_completion_clears_pending_modal() {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.active_task_id = Some("task-1".into());
    app.apply_event(approval_event("task-1", "id"));
    assert!(app.pending_execution.is_some());

    app.clear_task_requests("task-1");
    assert!(app.pending_execution.is_none());
}

#[test]
fn pending_execution_struct_roundtrip() {
    let p = PendingExecution {
        request_hash: "h".into(),
        kind: "shell".into(),
        command: "ls".into(),
        cwd: "/".into(),
        detail: String::new(),
        expires_at: String::new(),
        expires_in_secs: 300,
        received_at: std::time::Instant::now(),
        risk: String::new(),
        scroll_offset: 0,
    };
    assert_eq!(p.kind, "shell");
    assert_eq!(p.remaining_secs(), 300);
}

#[test]
fn approval_closed_event_clears_modal() {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.active_task_id = Some("task-1".into());
    app.apply_event(approval_event("task-1", "whoami"));

    assert!(app.pending_execution.is_some());
    app.apply_event(vulnclaw_tui::protocol::AppEvent::backend(
        vulnclaw_tui::protocol::BackendEvent::ApprovalClosed {
            task_id: "task-1".into(),
            request_hash: "a".repeat(64),
            status: "expired".into(),
        },
    ));
    // 超时关闭:弹窗消失,并留下原因
    assert!(app.pending_execution.is_none());
    assert!(app
        .transcript
        .iter()
        .any(|i| i.text.contains("审批超时,已自动拒绝")));
}

#[test]
fn approval_closed_ignores_non_matching_hash() {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.active_task_id = Some("task-1".into());
    app.apply_event(approval_event("task-1", "whoami"));

    app.apply_event(vulnclaw_tui::protocol::AppEvent::backend(
        vulnclaw_tui::protocol::BackendEvent::ApprovalClosed {
            task_id: "task-1".into(),
            request_hash: "b".repeat(64),
            status: "approved".into(),
        },
    ));
    assert!(app.pending_execution.is_some(), "hash 不匹配不得误关");
}
