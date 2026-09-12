use std::sync::mpsc;
use std::time::Instant;

use ratatui::{backend::TestBackend, Terminal};

use vulnclaw_tui::{
    app::{App, OperationReceipt, PendingExecution},
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
fn header_row_carries_only_brand_state_and_provider() {
    let app = app_with_provider(Some("DeepSeek"), Some("deepseek-chat"), true);
    let mut terminal = Terminal::new(TestBackend::new(120, 28)).unwrap();

    terminal.draw(|frame| render(frame, &app)).unwrap();

    let header = row_text(&terminal, 0);
    assert!(header.contains("VulnClaw"));
    assert!(header.contains("idle"));
    assert!(header.contains("provider: DeepSeek"));
    // Mode, guard and model moved down to the composer status line.
    assert!(!header.contains("Agent"), "mode must leave the header");
    assert!(!header.contains("Ask"), "guard must leave the header");
    assert!(
        !header.contains("deepseek-chat"),
        "model must leave the header"
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
fn header_shows_live_timer_and_running_state_while_a_worker_is_active() {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.worker_active = true;
    app.worker_started_at = Some(Instant::now());
    app.active_receipt = Some(OperationReceipt {
        command: "task run https://example.com".into(),
        phase: "Running".into(),
        findings: 0,
    });
    let mut terminal = Terminal::new(TestBackend::new(120, 28)).unwrap();

    terminal.draw(|frame| render(frame, &app)).unwrap();

    let rendered = terminal
        .backend()
        .buffer()
        .content
        .iter()
        .map(|cell| cell.symbol())
        .collect::<String>();
    assert!(rendered.contains("running"), "header must read 'running'");
    assert!(
        rendered.contains("⏱"),
        "header must show the live elapsed timer"
    );
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
