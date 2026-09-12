use std::sync::mpsc;
use std::time::Instant;

use ratatui::{backend::TestBackend, Terminal};

use vulnclaw_tui::{
    app::{App, PendingExecution},
    ui::layout::render,
};

fn pending_execution(command: String) -> PendingExecution {
    PendingExecution {
        request_hash: "a".repeat(64),
        kind: "shell".into(),
        command,
        cwd: "/tmp".into(),
        detail: "operator review required".into(),
        expires_at: String::new(),
        expires_in_secs: 300,
        received_at: Instant::now(),
        risk: "not sandboxed".into(),
        scroll_offset: 0,
    }
}

fn rendered_text(terminal: &Terminal<TestBackend>) -> String {
    terminal
        .backend()
        .buffer()
        .content
        .iter()
        .map(|cell| cell.symbol())
        .collect::<String>()
}

#[test]
fn renders_a_composer_centered_security_workbench() {
    let (sender, _) = mpsc::channel();
    let app = App::new_disconnected(sender);
    let mut terminal = Terminal::new(TestBackend::new(120, 28)).unwrap();

    terminal.draw(|frame| render(frame, &app)).unwrap();

    let rendered = terminal
        .backend()
        .buffer()
        .content
        .iter()
        .map(|cell| cell.symbol())
        .collect::<String>();
    assert!(rendered.contains("Session transcript"));
    assert!(rendered.contains("Findings inspector (0)"));
    assert!(rendered.contains("Status"));
    assert!(rendered.contains("Subagents"));
    assert!(rendered.contains("Type / for commands"));
    assert!(rendered.contains("Tab mode"));
    assert!(rendered.contains("ready"));
    assert!(!rendered.contains("[Skills] [Findings] [Output]"));
}

#[test]
fn collapsed_secondary_previews_docking_until_release_and_escape_cancels() {
    use crossterm::event::{
        KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind,
    };
    use ratatui::layout::Rect;
    use vulnclaw_tui::{
        events::{handle_key, handle_mouse},
        workbench::{ContainerId, Gesture, ViewId},
    };
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.terminal_size = Rect::new(0, 0, 120, 30);
    app.layout.primary.append(&mut app.layout.secondary);
    let mut terminal = Terminal::new(TestBackend::new(120, 30)).unwrap();
    let geometry = app.geometry(app.terminal_size);
    let title = geometry.view(ViewId::Findings).unwrap().title;
    let send = |app: &mut App, kind, column, row| {
        handle_mouse(
            app,
            MouseEvent {
                kind,
                column,
                row,
                modifiers: KeyModifiers::NONE,
            },
        )
    };
    send(
        &mut app,
        MouseEventKind::Down(MouseButton::Left),
        title.x + 3,
        title.y,
    );
    send(&mut app, MouseEventKind::Drag(MouseButton::Left), 60, 10);
    terminal.draw(|frame| render(frame, &app)).unwrap();
    assert!(rendered_text(&terminal).contains("Drag to right edge"));
    send(&mut app, MouseEventKind::Drag(MouseButton::Left), 119, 10);
    let Some(Gesture::Move {
        target: Some(target),
        ..
    }) = app.layout_gesture
    else {
        panic!("edge must offer a docking preview");
    };
    assert_eq!(target.container, ContainerId::Secondary);
    assert!(app.layout.secondary.is_empty());
    assert_eq!(
        app.geometry(app.terminal_size)
            .container(ContainerId::Center),
        geometry.container(ContainerId::Center)
    );
    terminal.draw(|frame| render(frame, &app)).unwrap();
    assert_eq!(
        terminal.backend().buffer()[(target.indicator.x + 1, target.indicator.y + 1)].bg,
        vulnclaw_tui::theme::DOCK_PREVIEW
    );
    // The visible preview remains a drop target when the pointer moves off the edge.
    send(
        &mut app,
        MouseEventKind::Drag(MouseButton::Left),
        target.indicator.x + 2,
        10,
    );
    assert!(matches!(
        app.layout_gesture,
        Some(Gesture::Move {
            target: Some(_),
            ..
        })
    ));
    handle_key(&mut app, KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE));
    assert!(app.layout.secondary.is_empty());
    assert!(app.layout_gesture.is_none());
    assert_eq!(
        app.geometry(app.terminal_size)
            .container(ContainerId::Center),
        geometry.container(ContainerId::Center)
    );
    send(
        &mut app,
        MouseEventKind::Down(MouseButton::Left),
        title.x + 3,
        title.y,
    );
    send(&mut app, MouseEventKind::Drag(MouseButton::Left), 119, 10);
    send(
        &mut app,
        MouseEventKind::Up(MouseButton::Left),
        target.indicator.x + 2,
        10,
    );
    assert_eq!(app.layout.secondary[0].id, ViewId::Findings);
    assert_eq!(app.layout.focus, ViewId::Findings);
    assert_eq!(
        app.geometry(app.terminal_size)
            .container(ContainerId::Secondary),
        target.indicator
    );
}

#[test]
fn module_drag_previews_match_cross_sidebar_and_reordered_placements() {
    use crossterm::event::{KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
    use ratatui::layout::Rect;
    use vulnclaw_tui::{
        events::handle_mouse,
        theme,
        workbench::{ContainerId, Gesture, ViewId},
    };

    for (id, destination, at_top, collapsed) in [
        (ViewId::Status, ContainerId::Secondary, false, false),
        (ViewId::Findings, ContainerId::Primary, false, false),
        (ViewId::Capabilities, ContainerId::Primary, true, false),
        (ViewId::Findings, ContainerId::Secondary, false, false),
        (ViewId::Findings, ContainerId::Primary, true, true),
    ] {
        let (sender, _) = mpsc::channel();
        let mut app = App::new_disconnected(sender);
        app.terminal_size = Rect::new(0, 0, 120, 30);
        app.layout.view_mut(id).collapsed = collapsed;
        app.layout.view_mut(id).scroll = 7;
        let original = serde_json::to_value(&app.layout).unwrap();
        let geometry = app.geometry(app.terminal_size);
        let title = geometry.view(id).unwrap().title;
        let container = geometry.container(destination);
        let send = |app: &mut App, kind, column, row| {
            handle_mouse(
                app,
                MouseEvent {
                    kind,
                    column,
                    row,
                    modifiers: KeyModifiers::NONE,
                },
            );
        };
        send(
            &mut app,
            MouseEventKind::Down(MouseButton::Left),
            title.x + 3,
            title.y,
        );
        let top = container.y;
        let bottom = container.bottom() - 1;
        send(
            &mut app,
            MouseEventKind::Drag(MouseButton::Left),
            container.x + 4,
            if at_top { bottom } else { top },
        );
        let Some(Gesture::Move {
            target: Some(first),
            ..
        }) = app.layout_gesture
        else {
            panic!("initial destination must offer a preview");
        };
        send(
            &mut app,
            MouseEventKind::Drag(MouseButton::Left),
            container.x + 4,
            if at_top { top } else { bottom },
        );
        let Some(Gesture::Move {
            target: Some(target),
            ..
        }) = app.layout_gesture
        else {
            panic!("destination must offer a preview");
        };
        assert_eq!(target.container, destination);
        assert_ne!(
            first.index, target.index,
            "drag must allow changing insertion position"
        );
        assert_eq!(serde_json::to_value(&app.layout).unwrap(), original);
        assert_eq!(target.indicator.height == 1, collapsed);
        let mut terminal = Terminal::new(TestBackend::new(120, 30)).unwrap();
        let gesture = app.layout_gesture.take();
        terminal.draw(|frame| render(frame, &app)).unwrap();
        let underneath = terminal.backend().buffer().clone();
        app.layout_gesture = gesture;
        terminal.draw(|frame| render(frame, &app)).unwrap();
        let border_x = if collapsed {
            target.indicator.right() - 1
        } else {
            target.indicator.x
        };
        let corner = &terminal.backend().buffer()[(border_x, target.indicator.y)];
        assert_eq!(corner.fg, theme::ACTION);
        assert_eq!(corner.bg, theme::DOCK_PREVIEW);
        assert_eq!(corner.symbol(), if collapsed { "━" } else { "┏" });
        for y in target.indicator.y + 1..target.indicator.bottom().saturating_sub(1) {
            for x in target.indicator.x + 1..target.indicator.right() - 1 {
                let cell = &terminal.backend().buffer()[(x, y)];
                assert_eq!(cell.symbol(), underneath[(x, y)].symbol());
                assert_eq!(cell.fg, underneath[(x, y)].fg);
                assert_eq!(cell.bg, theme::DOCK_PREVIEW);
            }
        }
        // Releasing inside the displayed preview commits that placement.
        send(
            &mut app,
            MouseEventKind::Up(MouseButton::Left),
            target.indicator.x + 3,
            target.indicator.bottom() - 1,
        );
        assert!(app.layout_gesture.is_none());
        assert_eq!(app.layout.focus, id);
        let views = app.layout.views(destination);
        assert_eq!(
            if at_top { views.first() } else { views.last() }
                .unwrap()
                .id,
            id
        );
        assert_eq!(app.layout.view(id).scroll, 7);
        assert_eq!(app.layout.view(id).collapsed, collapsed);
        assert_eq!(
            app.geometry(app.terminal_size).view(id).unwrap().rect,
            target.indicator
        );
    }
}

fn app_with_provider(provider: Option<&str>, model: Option<&str>, config_ready: bool) -> App {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.config_ready = Some(config_ready);
    app.provider = provider.map(str::to_owned);
    app.model = model.map(str::to_owned);
    app
}

fn row_text(terminal: &Terminal<TestBackend>, y: u16) -> String {
    let buffer = terminal.backend().buffer();
    (0..buffer.area.width)
        .map(|x| buffer[(x, y)].symbol())
        .collect()
}

#[test]
fn header_boxes_the_brand_and_provider_without_duplicating_the_status_view() {
    let app = app_with_provider(Some("DeepSeek"), Some("deepseek-chat"), true);
    let mut terminal = Terminal::new(TestBackend::new(120, 28)).unwrap();

    terminal.draw(|frame| render(frame, &app)).unwrap();

    // Rows 0 and 2 are the header box; the content sits on row 1.
    assert!(row_text(&terminal, 0).starts_with('┌'));
    assert!(row_text(&terminal, 2).starts_with('└'));
    let header = row_text(&terminal, 1);
    assert!(header.contains("VulnClaw"));
    assert!(header.contains("provider: DeepSeek"));
    // Nothing here repeats the composer status line or the Status view.
    assert!(
        !header.contains("Agent"),
        "mode lives on the composer status line"
    );
    assert!(
        !header.contains("Ask"),
        "guard lives on the composer status line"
    );
    assert!(
        !header.contains("idle"),
        "worker state lives in the Status view"
    );
    assert!(
        !header.contains("deepseek-chat"),
        "the model is not shown here"
    );
}

#[test]
fn composer_is_framed_and_followed_by_the_mode_guard_model_line() {
    let app = app_with_provider(Some("DeepSeek"), Some("deepseek-chat"), true);
    let mut terminal = Terminal::new(TestBackend::new(120, 28)).unwrap();

    terminal.draw(|frame| render(frame, &app)).unwrap();

    let buffer = terminal.backend().buffer();
    let input_row = (1..buffer.area.height)
        .find(|&y| row_text(&terminal, y).contains("Type / for commands"))
        .expect("composer placeholder must render");

    assert!(
        row_text(&terminal, input_row - 1).contains('─'),
        "the input must be framed above"
    );
    assert!(
        row_text(&terminal, input_row + 1).contains('─'),
        "the input must be framed below"
    );
    let status = row_text(&terminal, input_row + 2);
    assert!(status.contains("Agent"), "mode belongs under the frame");
    assert!(status.contains("Ask"), "guard belongs under the frame");
    // The marker is an ambiguous-width glyph, so the rendered row may pad it by
    // a cell; assert on order rather than on an exact adjacency.
    let marker = status.find('◈').expect("the model marker must render");
    let name = status.find("deepseek-chat").expect("the model must render");
    assert!(marker < name, "the marker precedes the model name");
    assert!(
        status.trim_end().ends_with("deepseek-chat"),
        "the model is right-aligned"
    );
}

#[test]
fn header_badge_falls_back_to_a_placeholder_without_credentials() {
    let app = app_with_provider(Some("DeepSeek"), Some("deepseek-chat"), false);
    let mut terminal = Terminal::new(TestBackend::new(120, 28)).unwrap();

    terminal.draw(|frame| render(frame, &app)).unwrap();

    let rendered = rendered_text(&terminal);
    assert!(rendered.contains("provider: not configured"));
    assert!(!rendered.contains("deepseek-chat"));
}

#[test]
fn header_badge_is_absent_before_the_backend_reports() {
    let app = app_with_provider(None, None, true);
    let mut terminal = Terminal::new(TestBackend::new(120, 28)).unwrap();

    terminal.draw(|frame| render(frame, &app)).unwrap();

    let rendered = rendered_text(&terminal);
    assert!(!rendered.contains("provider:"));
    assert!(rendered.contains("VulnClaw"));
}

#[test]
fn header_drops_the_badge_before_truncating_the_left_cluster() {
    let app = app_with_provider(Some("DeepSeek"), Some("deepseek-chat"), true);
    // Wide enough for the left cluster plus the badge minimum, but not both.
    let mut terminal = Terminal::new(TestBackend::new(40, 28)).unwrap();

    terminal.draw(|frame| render(frame, &app)).unwrap();

    let rendered = rendered_text(&terminal);
    assert!(!rendered.contains("provider:"));
    assert!(rendered.contains("VulnClaw"));
}

#[test]
fn approval_modal_stays_inside_a_small_terminal() {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.pending_execution = Some(pending_execution("whoami".into()));
    for (width, height) in [(1, 1), (10, 3), (30, 8)] {
        let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
        terminal.draw(|frame| render(frame, &app)).unwrap();
        if width == 30 {
            let rendered = rendered_text(&terminal);
            assert!(rendered.contains("[Y]"));
            assert!(rendered.contains("[N/Esc]"));
        }
    }
}

#[test]
fn approval_modal_scrolls_full_code_with_fixed_footer() {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.terminal_size = ratatui::layout::Rect::new(0, 0, 60, 18);
    let mut rows = vec!["FIRST-LINE".to_string()];
    rows.extend((1..29).map(|index| format!("middle-{index:02}")));
    rows.push("LAST-LINE".to_string());
    app.pending_execution = Some(pending_execution(rows.join("\n")));
    let mut terminal = Terminal::new(TestBackend::new(60, 18)).unwrap();

    terminal.draw(|frame| render(frame, &app)).unwrap();
    let first = rendered_text(&terminal);
    assert!(first.contains("FIRST-LINE"));
    assert!(!first.contains("LAST-LINE"));
    assert!(first.contains("[Y]"));

    for _ in 0..10 {
        app.scroll_pending_execution(true, true);
    }
    terminal.draw(|frame| render(frame, &app)).unwrap();
    let last = rendered_text(&terminal);
    assert!(last.contains("LAST-LINE"));
    assert!(last.contains("[Y]"));
    assert!(last.contains("[N/Esc]"));
}

#[test]
fn slash_input_renders_the_command_palette() {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.backend_commands = vec!["scan".into()];
    app.insert_text("/");
    let mut terminal = Terminal::new(TestBackend::new(120, 28)).unwrap();

    terminal.draw(|frame| render(frame, &app)).unwrap();

    let rendered = terminal
        .backend()
        .buffer()
        .content
        .iter()
        .map(|cell| cell.symbol())
        .collect::<String>();
    assert!(rendered.contains("Commands"));
    assert!(rendered.contains("/scan "));
}

#[test]
fn composer_cursor_advances_by_display_cells_not_characters() {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    let mut terminal = Terminal::new(TestBackend::new(120, 28)).unwrap();

    app.insert_text("ab");
    terminal.draw(|frame| render(frame, &app)).unwrap();
    let ascii = terminal.get_cursor_position().unwrap();

    // A CJK glyph is one character but two terminal cells. Counting characters
    // would advance the caret by one and leave it trailing the text.
    app.insert_text("中");
    terminal.draw(|frame| render(frame, &app)).unwrap();
    let wide = terminal.get_cursor_position().unwrap();
    assert_eq!(
        wide.x - ascii.x,
        2,
        "one CJK glyph must advance the caret by two cells"
    );

    app.insert_text("文");
    terminal.draw(|frame| render(frame, &app)).unwrap();
    let wider = terminal.get_cursor_position().unwrap();
    assert_eq!(wider.x - ascii.x, 4, "two CJK glyphs advance four cells");
}

#[test]
fn composer_placeholder_renders_on_a_single_row() {
    let (sender, _) = mpsc::channel();
    let app = App::new_disconnected(sender); // empty input -> placeholder path
    let mut terminal = Terminal::new(TestBackend::new(80, 24)).unwrap();
    terminal.draw(|frame| render(frame, &app)).unwrap();
    let buf = terminal.backend().buffer();
    let mut rows_with_placeholder = 0;
    for y in 0..24u16 {
        let line: String = (0..80u16)
            .map(|x| {
                buf.cell((x, y))
                    .map(|c| c.symbol().to_string())
                    .unwrap_or_default()
            })
            .collect();
        if line.contains("Type / for commands") {
            rows_with_placeholder += 1;
        }
    }
    assert_eq!(
        rows_with_placeholder, 1,
        "the composer placeholder must render on exactly one row"
    );
}

#[test]
fn task_confirmation_replaces_the_composer() {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.pending_task = Some("/run target.test".into());
    let mut terminal = Terminal::new(TestBackend::new(120, 28)).unwrap();

    terminal.draw(|frame| render(frame, &app)).unwrap();

    let rendered = terminal
        .backend()
        .buffer()
        .content
        .iter()
        .map(|cell| cell.symbol())
        .collect::<String>();
    assert!(rendered.contains("Task confirmation required"));
    assert!(rendered.contains("Y confirm"));
}

#[test]
fn a_collapsed_view_renders_as_a_rule_with_its_title_set_in() {
    use ratatui::layout::Rect;
    use vulnclaw_tui::workbench::ViewId;

    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.layout.view_mut(ViewId::Findings).collapsed = true;
    let area = Rect::new(0, 0, 120, 24);
    let rect = app.geometry(area).view(ViewId::Findings).unwrap().rect;
    let mut terminal = Terminal::new(TestBackend::new(120, 24)).unwrap();

    terminal.draw(|frame| render(frame, &app)).unwrap();

    let row = row_text(&terminal, rect.y);
    let cell: String = row
        .chars()
        .skip(usize::from(rect.x))
        .take(usize::from(rect.width))
        .collect();
    assert!(cell.starts_with("─▶ Findings inspector"), "got {cell:?}");
    // A collapsed view is a bare rule: no box corners and no side rails.
    assert!(!cell.contains('┌'), "got {cell:?}");
    assert!(!cell.contains('┐'), "got {cell:?}");
    assert!(!cell.contains('│'), "got {cell:?}");
}

#[test]
fn header_centres_the_live_cluster_between_brand_and_badge() {
    let mut app = app_with_provider(Some("DeepSeek"), Some("deepseek-chat"), true);
    app.worker_active = true;
    app.worker_started_at = Some(Instant::now());
    let mut terminal = Terminal::new(TestBackend::new(120, 24)).unwrap();

    terminal.draw(|frame| render(frame, &app)).unwrap();

    let header = row_text(&terminal, 1);
    assert!(header.contains("running"), "got {header:?}");
    assert!(header.contains('⏱'), "the elapsed readout must be back");

    // Centred in the gap between the brand and the badge.
    let brand_end = header.find("VulnClaw").expect("brand") + "VulnClaw".len();
    let badge_start = header.find("provider:").expect("badge");
    let cluster_start = header.find("running").expect("cluster");
    let cluster_end = header.find("00:").expect("elapsed") + 5;
    let cluster_mid = (cluster_start + cluster_end) / 2;
    let gap_mid = (brand_end + badge_start) / 2;
    assert!(
        cluster_mid.abs_diff(gap_mid) <= 4,
        "cluster mid {cluster_mid} should sit near {gap_mid}: {header:?}"
    );

    // An idle header carries no cluster and no idle placeholder.
    app.worker_active = false;
    terminal.draw(|frame| render(frame, &app)).unwrap();
    let idle = row_text(&terminal, 1);
    assert!(!idle.contains("running"), "got {idle:?}");
    assert!(!idle.contains("idle"), "got {idle:?}");
    assert!(idle.contains("VulnClaw"));
    assert!(idle.contains("provider: DeepSeek"));
}
