use std::sync::mpsc;

use ratatui::{backend::TestBackend, Terminal};

use vulnclaw_tui::{app::App, skills::catalog::SkillNode, views::capabilities::build_lines};

fn lines_of(app: &App) -> Vec<String> {
    build_lines(app)
        .iter()
        .map(|line| {
            line.spans
                .iter()
                .map(|span| span.content.as_ref())
                .collect::<String>()
        })
        .collect()
}

fn app_with_skills(skills: Vec<SkillNode>) -> App {
    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.skills = skills;
    app
}

fn node(name: &str, children: Vec<SkillNode>) -> SkillNode {
    SkillNode {
        name: name.into(),
        children,
    }
}

#[test]
fn empty_capabilities_render_a_placeholder() {
    let app = app_with_skills(Vec::new());

    assert_eq!(lines_of(&app), ["No capabilities advertised"]);
}

#[test]
fn nested_capabilities_render_as_a_box_drawing_tree() {
    let app = app_with_skills(vec![node(
        "Python skills",
        vec![
            node(
                "Recon",
                vec![node("subdomain", vec![]), node("port", vec![])],
            ),
            node("Scan", vec![]),
        ],
    )]);

    assert_eq!(
        lines_of(&app),
        [
            "Python skills",
            "├── Recon",
            "│   ├── subdomain",
            "│   └── port",
            "└── Scan",
        ]
    );
}

#[test]
fn the_last_sibling_closes_its_branch_without_a_continuation_rail() {
    let app = app_with_skills(vec![node(
        "root",
        vec![
            node("first", vec![]),
            node("last", vec![node("leaf", vec![])]),
        ],
    )]);

    let lines = lines_of(&app);
    assert_eq!(lines[1], "├── first");
    assert_eq!(lines[2], "└── last");
    // The last sibling's child is indented by a gap, not a rail.
    assert_eq!(lines[3], "    └── leaf");
}

#[test]
fn the_view_renders_its_own_tree_inside_its_container() {
    let mut app = app_with_skills(vec![node("Python skills", vec![node("Recon", vec![])])]);
    app.layout.focus = vulnclaw_tui::workbench::ViewId::Capabilities;
    let mut terminal = Terminal::new(TestBackend::new(60, 24)).unwrap();

    terminal
        .draw(|frame| vulnclaw_tui::ui::layout::render(frame, &app))
        .unwrap();

    let rendered = terminal
        .backend()
        .buffer()
        .content
        .iter()
        .map(|cell| cell.symbol())
        .collect::<String>();
    assert!(rendered.contains("Capabilities"), "the view owns its title");
    assert!(rendered.contains("└── Recon"));
}

#[test]
fn the_status_view_no_longer_carries_the_capability_tree() {
    let (sender, _) = mpsc::channel();
    let app = App::new_disconnected(sender);
    let status: String = vulnclaw_tui::views::status::build_lines(&app)
        .iter()
        .map(|line| {
            line.spans
                .iter()
                .map(|span| span.content.as_ref())
                .collect::<String>()
        })
        .collect::<Vec<_>>()
        .join("\n");

    assert!(status.contains("Workspace"));
    assert!(!status.contains("Capabilities"));
    assert!(!status.contains("├──"));
}
