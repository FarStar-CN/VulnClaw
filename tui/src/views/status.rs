use ratatui::{
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::{Paragraph, Wrap},
};

use crate::app::{App, OperationKind};
use crate::theme;

pub fn build_lines(app: &App) -> Vec<Line<'static>> {
    let receipt = app.active_receipt.as_ref().or(app.last_receipt.as_ref());
    let kind = receipt.map_or(OperationKind::Conversation, |receipt| receipt.kind);
    let mut lines = vec![
        Line::from(Span::styled(
            match kind {
                OperationKind::Conversation => "Conversation",
                OperationKind::Task => "Task execution",
            },
            Style::default()
                .fg(theme::ACTION)
                .add_modifier(Modifier::BOLD),
        )),
        Line::from(Span::styled(
            format!(
                "State       {}",
                if app.pending_execution.is_some() {
                    "Awaiting approval"
                } else if kind == OperationKind::Conversation
                    && app.active_receipt.is_none()
                    && receipt.is_some_and(|receipt| receipt.phase == "Completed")
                {
                    "Ready"
                } else {
                    receipt.map_or("Ready", |receipt| receipt.phase.as_str())
                }
            ),
            Style::default().fg(if app.worker_active {
                theme::GOLD
            } else {
                theme::TEXT_SOFT
            }),
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
    ];
    if kind == OperationKind::Task {
        if receipt.is_some_and(|receipt| receipt.phase != "Submitting") {
            lines.push(Line::from(Span::styled(
                format!("Phase       {}", app.phase),
                Style::default().fg(theme::TEXT_MUTED),
            )));
        }
        lines.push(Line::from(Span::styled(
            format!(
                "Found       {}",
                receipt.map_or(0, |receipt| receipt.findings)
            ),
            Style::default().fg(theme::SUCCESS),
        )));
        lines.push(Line::from(Span::styled(
            format!("Last run    {}", last_run_label(app.last_run.as_ref())),
            Style::default().fg(theme::TEXT_MUTED),
        )));
    }
    lines.push(Line::from(""));
    if let Some(receipt) = receipt {
        lines.push(Line::from(Span::styled(
            match (kind, app.active_receipt.is_some()) {
                (OperationKind::Conversation, true) => "Current message",
                (OperationKind::Conversation, false) => "Last message",
                (OperationKind::Task, true) => "Current command",
                (OperationKind::Task, false) => "Last command",
            },
            Style::default().fg(theme::SEAFOAM),
        )));
        lines.push(Line::from(Span::styled(
            truncate(&receipt.input, 22),
            Style::default().fg(theme::TEXT_MUTED),
        )));
    } else {
        lines.push(Line::from(Span::styled(
            "Natural language or / commands",
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
