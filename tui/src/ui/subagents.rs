use ratatui::{
    layout::Rect,
    style::{Modifier, Style},
    text::Line,
    widgets::Paragraph,
    Frame,
};

use crate::{app::App, theme, workbench::ViewId};

pub fn build_lines(app: &App) -> Vec<Line<'static>> {
    app.subagents
        .rows()
        .into_iter()
        .map(|row| {
            let selected = row.selectable && row.agent_id == app.subagents.selection;
            let viewing = row.selectable && row.agent_id == app.subagents.viewing;
            let style = if selected {
                Style::default()
                    .fg(theme::ACTION)
                    .add_modifier(Modifier::BOLD)
            } else {
                Style::default().fg(theme::TEXT_HINT)
            };
            Line::styled(
                format!(
                    "{}{}{}",
                    if selected { "› " } else { "  " },
                    row.label,
                    if viewing { " ◀" } else { "" }
                ),
                style,
            )
        })
        .collect()
}

pub fn render(frame: &mut Frame, app: &App, area: Rect) {
    frame.render_widget(
        Paragraph::new(build_lines(app))
            .scroll((app.layout.view(ViewId::Subagents).scroll, 0))
            .block(crate::ui::layout::view_block(app, ViewId::Subagents)),
        area,
    );
}
