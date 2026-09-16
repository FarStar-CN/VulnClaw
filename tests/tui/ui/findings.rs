use std::sync::mpsc;

use ratatui::{backend::TestBackend, Terminal};

use vulnclaw_tui::{
    app::App,
    protocol::{EvidenceRef, Finding},
    theme,
    ui::findings::{finding_at_row, render},
};

fn finding(id: &str, severity: &str, title: &str, evidence: &[&str]) -> Finding {
    Finding {
        id: id.into(),
        severity: severity.into(),
        title: title.into(),
        target: "src/app.py".into(),
        line: Some(12),
        code_location: None,
        chain_depends_on: Vec::new(),
        evidence_refs: evidence
            .iter()
            .map(|path| EvidenceRef {
                kind: "http".into(),
                path: (*path).into(),
                request_id: None,
            })
            .collect(),
    }
}

fn app_with(findings: Vec<Finding>) -> App {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.findings = findings;
    app
}

fn draw(app: &App, width: u16, height: u16) -> String {
    let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
    terminal
        .draw(|frame| render(frame, app, frame.area()))
        .unwrap();
    terminal
        .backend()
        .buffer()
        .content
        .iter()
        .map(|cell| cell.symbol())
        .collect()
}

#[test]
fn renders_findings_with_their_severity_color() {
    let app = app_with(vec![finding(
        "critical-1",
        "critical",
        "Hardcoded credential",
        &[],
    )]);
    let mut terminal = Terminal::new(TestBackend::new(80, 8)).unwrap();

    terminal
        .draw(|frame| render(frame, &app, frame.area()))
        .unwrap();

    let buffer = terminal.backend().buffer();
    let rendered = buffer
        .content
        .iter()
        .map(|cell| cell.symbol())
        .collect::<String>();
    assert!(rendered.contains("Findings inspector (1)"));
    assert!(buffer.content.iter().any(|cell| cell.fg == theme::ROSE));
}

#[test]
fn a_finding_with_evidence_collapses_and_expands_its_references() {
    let mut app = app_with(vec![finding(
        "high-1",
        "high",
        "SQL injection",
        &["evidence/req_001.http", "evidence/resp_001.http"],
    )]);

    let collapsed = draw(&app, 80, 10);
    assert!(
        collapsed.contains("▶ [HIGH] SQL injection"),
        "got {collapsed:?}"
    );
    assert!(!collapsed.contains("req_001.http"));

    app.expanded_finding = Some("high-1".into());
    let expanded = draw(&app, 80, 10);
    assert!(
        expanded.contains("▼ [HIGH] SQL injection"),
        "got {expanded:?}"
    );
    assert!(expanded.contains("└ http evidence/req_001.http"));
    assert!(expanded.contains("└ http evidence/resp_001.http"));
}

#[test]
fn a_finding_without_evidence_keeps_the_title_indent_and_never_expands() {
    let mut app = app_with(vec![finding("med-1", "medium", "Missing HSTS", &[])]);
    app.expanded_finding = Some("med-1".into());

    // `toggle_selected_finding` refuses an evidence-less finding, so the id can
    // only be set directly; rendering must still not produce a child row.
    assert_eq!(app.finding_rows(), 1);
    let rendered = draw(&app, 80, 10);
    assert!(
        rendered.contains("  [MEDIUM] Missing HSTS"),
        "got {rendered:?}"
    );
    assert!(!rendered.contains("   └ "), "no evidence row may appear");
}

#[test]
fn only_one_finding_is_expanded_at_a_time() {
    let mut app = app_with(vec![
        finding("a", "high", "First", &["evidence/a.http"]),
        finding("b", "high", "Second", &["evidence/b.http"]),
    ]);

    app.select_finding(0);
    assert!(app.toggle_selected_finding());
    assert_eq!(app.expanded_finding.as_deref(), Some("a"));
    assert_eq!(app.finding_rows(), 3, "two headers plus one reference");

    app.select_finding(1);
    assert!(app.toggle_selected_finding());
    assert_eq!(app.expanded_finding.as_deref(), Some("b"));
    assert_eq!(app.finding_rows(), 3);

    app.toggle_selected_finding();
    assert_eq!(app.expanded_finding, None);
    assert_eq!(app.finding_rows(), 2);
}

#[test]
fn rows_map_back_to_findings_around_the_expanded_evidence() {
    let mut app = app_with(vec![
        finding(
            "a",
            "high",
            "First",
            &["evidence/a1.http", "evidence/a2.http"],
        ),
        finding("b", "high", "Second", &["evidence/b1.http"]),
    ]);

    // Collapsed: row N is finding N.
    assert_eq!(finding_at_row(&app, 0), Some(0));
    assert_eq!(finding_at_row(&app, 1), Some(1));
    assert_eq!(finding_at_row(&app, 2), None);

    app.expanded_finding = Some("a".into());
    assert_eq!(finding_at_row(&app, 0), Some(0), "header");
    assert_eq!(finding_at_row(&app, 1), None, "first reference");
    assert_eq!(finding_at_row(&app, 2), None, "second reference");
    assert_eq!(
        finding_at_row(&app, 3),
        Some(1),
        "the second header shifted down"
    );
}

#[test]
fn the_selected_row_offset_accounts_for_a_preceding_expansion() {
    let mut app = app_with(vec![
        finding(
            "a",
            "high",
            "First",
            &["evidence/a1.http", "evidence/a2.http"],
        ),
        finding("b", "high", "Second", &[]),
    ]);

    app.select_finding(1);
    assert_eq!(app.selected_finding_row(), 1, "collapsed above");
    app.expanded_finding = Some("a".into());
    assert_eq!(
        app.selected_finding_row(),
        3,
        "the expansion pushes it down"
    );
}
