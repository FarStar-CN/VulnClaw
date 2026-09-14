use ratatui::{
    layout::Rect,
    style::Style,
    text::{Line, Span},
    widgets::{List, ListItem, ListState},
    Frame,
};

use crate::app::App;
use crate::theme;
use crate::workbench::ViewId;

/// A finding that has evidence collapses and expands; one that has none keeps
/// the same indent so titles stay aligned.
const COLLAPSED: &str = "▶ ";
const EXPANDED: &str = "▼ ";
const PLAIN: &str = "  ";
/// Indent for an evidence reference under its finding.
const EVIDENCE_PREFIX: &str = "   └ ";

fn finding_line(app: &App, index: usize) -> Line<'static> {
    let finding = &app.findings[index];
    let location = finding
        .line
        .map(|line| format!("{}:{line}", finding.target))
        .unwrap_or_else(|| finding.target.clone());
    let marker = if finding.evidence_refs.is_empty() {
        PLAIN
    } else if app.expanded_finding.as_deref() == Some(finding.id.as_str()) {
        EXPANDED
    } else {
        COLLAPSED
    };
    Line::from(vec![
        Span::raw(marker),
        Span::raw(format!(
            "[{}] {} - {}",
            finding.severity.to_uppercase(),
            finding.title,
            location
        )),
    ])
    .style(theme::severity_style(&finding.severity))
}

/// Index of the finding whose header sits on `row` of the flattened list, or
/// `None` when that row holds an evidence reference.
pub fn finding_at_row(app: &App, row: usize) -> Option<usize> {
    let expanded = app.expanded_finding.as_deref();
    let mut cursor = 0usize;
    for (index, finding) in app.findings.iter().enumerate() {
        if cursor == row {
            return Some(index);
        }
        cursor += 1;
        if expanded == Some(finding.id.as_str()) {
            if row < cursor + finding.evidence_refs.len() {
                return None;
            }
            cursor += finding.evidence_refs.len();
        }
    }
    None
}

pub fn render(frame: &mut Frame, app: &App, area: Rect) {
    // The selection only matters while the view owns the keyboard, so the
    // highlight follows focus instead of sitting on permanently.
    let focused = app.layout.focus == ViewId::Findings;
    let expanded = app.expanded_finding.as_deref();
    let mut items: Vec<ListItem> = Vec::with_capacity(app.finding_rows());
    for (index, finding) in app.findings.iter().enumerate() {
        let mut item = ListItem::new(finding_line(app, index));
        if focused && index == app.findings_selection {
            item = item.style(Style::default().bg(theme::PLATE));
        }
        items.push(item);
        if expanded == Some(finding.id.as_str()) {
            for evidence in &finding.evidence_refs {
                items.push(ListItem::new(Line::from(Span::styled(
                    format!("{EVIDENCE_PREFIX}{}", evidence.label()),
                    Style::default().fg(theme::TEXT_SOFT),
                ))));
            }
        }
    }
    let list = List::new(items).block(crate::ui::layout::view_block(app, ViewId::Findings));
    let mut state =
        ListState::default().with_offset(app.layout.view(ViewId::Findings).scroll as usize);
    frame.render_stateful_widget(list, area, &mut state);
}
