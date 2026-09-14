use ratatui::{
    layout::Rect,
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::{Paragraph, Wrap},
    Frame,
};

use crate::app::{App, TranscriptKind};
use crate::theme;

/// Build the rendered transcript lines, honouring the `show_reasoning` filter.
/// Shared by [`render`] and the auto-follow scroll logic in `app.rs` so both
/// compute the exact same wrapped line count.
pub fn build_lines(app: &App) -> Vec<Line<'static>> {
    app.visible_transcript()
        .iter()
        .filter(|item| app.show_reasoning || !matches!(item.kind, TranscriptKind::Reasoning))
        .map(|item| {
            let label = match item.kind {
                TranscriptKind::User => "You  ",
                TranscriptKind::System => "VulnClaw  ",
                TranscriptKind::Status => "Status  ",
                TranscriptKind::Log => "Log  ",
                TranscriptKind::Reasoning => "Thinking  ",
                TranscriptKind::Error => "Error  ",
                TranscriptKind::Finding => "Finding  ",
            };
            // The user's own turns carry no kind colour, so they read as plain text.
            let style = if matches!(item.kind, TranscriptKind::User) {
                Style::default()
            } else {
                theme::transcript_style(&item.kind)
            };
            Line::from(vec![
                Span::styled(label, style.add_modifier(Modifier::BOLD)),
                Span::styled(item.text.clone(), style),
            ])
        })
        .collect::<Vec<_>>()
}

pub fn render(frame: &mut Frame, app: &App, area: Rect) {
    let lines = build_lines(app);
    frame.render_widget(
        Paragraph::new(lines)
            .scroll((app.layout.output.scroll, 0))
            .wrap(Wrap { trim: false })
            .block(crate::ui::layout::view_block(
                app,
                crate::workbench::ViewId::Output,
            )),
        area,
    );
}
