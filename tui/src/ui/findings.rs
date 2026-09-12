use ratatui::{
    layout::Rect,
    widgets::{List, ListItem, ListState},
    Frame,
};

use crate::app::App;
use crate::theme;
use crate::workbench::ViewId;

pub fn render(frame: &mut Frame, app: &App, area: Rect) {
    let items = app
        .findings
        .iter()
        .map(|finding| {
            let location = finding
                .line
                .map(|line| format!("{}:{line}", finding.target))
                .unwrap_or_else(|| finding.target.clone());
            let label = format!(
                "[{}] {} - {}",
                finding.severity.to_uppercase(),
                finding.title,
                location
            );
            ListItem::new(label).style(theme::severity_style(&finding.severity))
        })
        .collect::<Vec<_>>();
    let list = List::new(items).block(crate::ui::layout::view_block(app, ViewId::Findings));
    let mut state =
        ListState::default().with_offset(app.layout.view(ViewId::Findings).scroll as usize);
    frame.render_stateful_widget(list, area, &mut state);
}
