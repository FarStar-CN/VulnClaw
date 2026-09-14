use ratatui::{
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::{Paragraph, Wrap},
};

use crate::app::App;
use crate::theme;

pub fn build_lines(app: &App) -> Vec<Line<'static>> {
    let mut lines = vec![
        Line::from(Span::styled(
            "Workspace",
            Style::default()
                .fg(theme::ACTION)
                .add_modifier(Modifier::BOLD),
        )),
        Line::from(Span::styled(
            format!("Mode        {}", app.mode.label()),
            Style::default().fg(theme::TEXT_MUTED),
        )),
        Line::from(Span::styled(
            format!("Guard       {}", app.permission.label()),
            Style::default().fg(theme::TEXT_MUTED),
        )),
        Line::from(Span::styled(
            format!(
                "Target      {}",
                if app.target.is_empty() {
                    "not selected"
                } else {
                    app.target.as_str()
                }
            ),
            Style::default().fg(theme::TEXT_MUTED),
        )),
        Line::from(Span::styled(
            format!("Phase       {}", app.phase),
            Style::default().fg(theme::TEXT_MUTED),
        )),
        Line::from(Span::styled(
            format!(
                "Scope       H{} P{} A{}",
                json_array_len(&app.task_constraints, "allowed_hosts"),
                json_array_len(&app.task_constraints, "allowed_ports"),
                json_array_len(&app.task_constraints, "allowed_actions")
            ),
            Style::default().fg(theme::TEXT_MUTED),
        )),
        Line::from(Span::styled(
            format!("Violations  {}", app.constraint_violations.len()),
            Style::default().fg(if app.constraint_violations.is_empty() {
                theme::TEXT_MUTED
            } else {
                theme::CORAL
            }),
        )),
        Line::from(Span::styled(
            format!("Last run    {}", last_run_label(app.last_run.as_ref())),
            Style::default().fg(theme::TEXT_MUTED),
        )),
        Line::from(""),
        Line::from(Span::styled(
            "Execution receipt",
            Style::default()
                .fg(theme::SEAFOAM)
                .add_modifier(Modifier::BOLD),
        )),
    ];
    if let Some(receipt) = app.active_receipt.as_ref() {
        lines.push(Line::from(Span::styled(
            format!("LIVE  {}", receipt.phase),
            Style::default()
                .fg(theme::GOLD)
                .add_modifier(Modifier::BOLD),
        )));
        lines.push(Line::from(Span::styled(
            format!("Found {}", receipt.findings),
            Style::default().fg(theme::SUCCESS),
        )));
        lines.push(Line::from(Span::styled(
            truncate(&receipt.command, 22),
            Style::default().fg(theme::TEXT_MUTED),
        )));
    } else if let Some(receipt) = app.last_receipt.as_ref() {
        lines.push(Line::from(Span::styled(
            format!("{}  {} finding(s)", receipt.phase, receipt.findings),
            Style::default().fg(theme::TEXT_SOFT),
        )));
        lines.push(Line::from(Span::styled(
            truncate(&receipt.command, 22),
            Style::default().fg(theme::TEXT_MUTED),
        )));
    } else {
        lines.push(Line::from(Span::styled(
            "No command started",
            Style::default().fg(theme::TEXT_HINT),
        )));
    }
    lines
}

pub fn render(app: &App) -> Paragraph<'static> {
    Paragraph::new(build_lines(app))
        .wrap(Wrap { trim: false })
        .scroll((app.layout.view(crate::workbench::ViewId::Status).scroll, 0))
}

fn json_array_len(value: &serde_json::Value, key: &str) -> usize {
    value
        .get(key)
        .and_then(serde_json::Value::as_array)
        .map_or(0, Vec::len)
}

fn last_run_label(value: Option<&serde_json::Value>) -> &str {
    let Some(run) = value else {
        return "none";
    };
    ["/run/name", "/status"]
        .iter()
        .find_map(|pointer| run.pointer(pointer).and_then(serde_json::Value::as_str))
        .unwrap_or("none")
}

fn truncate(text: &str, max_chars: usize) -> String {
    let mut characters = text.chars();
    let preview = characters.by_ref().take(max_chars).collect::<String>();
    if characters.next().is_some() {
        format!("{preview}...")
    } else {
        preview
    }
}
