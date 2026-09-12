use ratatui::{
    layout::Rect,
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::{Paragraph, Wrap},
    Frame,
};

use crate::app::App;
use crate::skills::catalog::SkillNode;
use crate::theme;
use crate::workbench::ViewId;

/// Box-drawing rails for the capability tree: an interior node, the last node of
/// a level, and the continuation rail under an interior node.
const BRANCH: &str = "├── ";
const LAST: &str = "└── ";
const RAIL: &str = "│   ";
const GAP: &str = "    ";

pub fn build_lines(app: &App) -> Vec<Line<'static>> {
    if app.skills.is_empty() {
        return vec![Line::from(Span::styled(
            "No capabilities advertised",
            Style::default().fg(theme::TEXT_HINT),
        ))];
    }
    let mut lines = Vec::new();
    append_nodes(&app.skills, "", 0, &mut lines);
    lines
}

pub fn render(frame: &mut Frame, app: &App, area: Rect) {
    let scroll = app.layout.view(ViewId::Capabilities).scroll;
    frame.render_widget(
        Paragraph::new(build_lines(app))
            .wrap(Wrap { trim: false })
            .scroll((scroll, 0))
            .block(crate::ui::layout::view_block(app, ViewId::Capabilities)),
        area,
    );
}

fn append_nodes(nodes: &[SkillNode], prefix: &str, depth: usize, lines: &mut Vec<Line<'static>>) {
    for (index, node) in nodes.iter().enumerate() {
        let is_last = index + 1 == nodes.len();
        // The roots carry no branch; every level below is drawn as a tree.
        // Depth, not the prefix, decides this: a root's children also start
        // with an empty prefix.
        let branch = if depth == 0 {
            ""
        } else if is_last {
            LAST
        } else {
            BRANCH
        };
        let style = if node.children.is_empty() {
            Style::default().fg(theme::TEXT_SOFT)
        } else {
            Style::default()
                .fg(theme::TEXT_BODY)
                .add_modifier(Modifier::BOLD)
        };
        lines.push(Line::from(Span::styled(
            format!("{prefix}{branch}{}", node.name),
            style,
        )));
        if !node.children.is_empty() {
            let next_prefix = if depth == 0 {
                String::new()
            } else if is_last {
                format!("{prefix}{GAP}")
            } else {
                format!("{prefix}{RAIL}")
            };
            append_nodes(&node.children, &next_prefix, depth + 1, lines);
        }
    }
}
