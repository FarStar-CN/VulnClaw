use std::fs;
use std::sync::mpsc;
use std::time::{SystemTime, UNIX_EPOCH};

use ratatui::layout::{Position, Rect};
use vulnclaw_tui::{
    app::{App, COMPOSER_FRAME_ROWS, COMPOSER_STATUS_ROWS},
    preferences,
    sessions::SessionState,
    workbench::{resize, ContainerId, LayoutGeometry, LayoutState, SashId, ViewId, ViewInstance},
};

const COMPOSER_ROWS: u16 = COMPOSER_FRAME_ROWS + COMPOSER_STATUS_ROWS;

fn geometry(state: &LayoutState) -> LayoutGeometry {
    LayoutGeometry::compute(Rect::new(0, 0, 120, 30), state, COMPOSER_ROWS)
}

#[test]
fn geometry_restores_preferences_after_temporary_terminal_compression() {
    let state = LayoutState::default();
    let original = geometry(&state);
    let narrow = LayoutGeometry::compute(Rect::new(0, 0, 56, 14), &state, COMPOSER_ROWS);
    assert!(!narrow.too_small);
    assert_eq!(narrow.container(ContainerId::Primary).width, 16);
    assert_eq!(narrow.container(ContainerId::Center).width, 24);
    assert_eq!(narrow.container(ContainerId::Secondary).width, 16);
    for area in [Rect::new(0, 0, 55, 30), Rect::new(0, 0, 120, 12)] {
        let small = LayoutGeometry::compute(area, &state, COMPOSER_ROWS);
        assert!(small.too_small);
        assert!(small.views.is_empty());
    }
    assert_eq!(geometry(&state).containers, original.containers);
    let primary = original.container(ContainerId::Primary);
    let center = original.container(ContainerId::Center);
    let secondary = original.container(ContainerId::Secondary);
    assert_eq!(primary.right(), center.x);
    assert_eq!(center.right(), secondary.x);
    for container in [ContainerId::Primary, ContainerId::Secondary] {
        let stack: Vec<_> = original
            .views
            .iter()
            .filter(|view| view.container == container)
            .collect();
        for pair in stack.windows(2) {
            assert_eq!(pair[0].rect.bottom(), pair[1].rect.y);
        }
    }
    for sash in &original.sashes {
        for view in &original.views {
            assert!(sash.rect.intersection(view.content).is_empty());
        }
    }
    let views = &original.views;
    for (index, view) in views.iter().enumerate() {
        for other in &views[index + 1..] {
            assert!(view.rect.intersection(other.rect).is_empty());
        }
    }
}

#[test]
fn container_sashes_clamp_the_pair_and_preserve_the_third_column() {
    for (sash, untouched) in [
        (SashId::Primary, ContainerId::Secondary),
        (SashId::Secondary, ContainerId::Primary),
    ] {
        for delta in [-1000, 1000] {
            let mut state = LayoutState::default();
            let before = geometry(&state);
            resize(&mut state, &before, sash, delta, 0);
            let after = geometry(&state);
            assert_eq!(before.container(untouched), after.container(untouched));
            assert!(after.container(ContainerId::Primary).width >= 16);
            assert!(after.container(ContainerId::Secondary).width >= 16);
            assert!(after.container(ContainerId::Center).width >= 24);
            assert_eq!(
                after.container(ContainerId::Primary).width
                    + after.container(ContainerId::Center).width
                    + after.container(ContainerId::Secondary).width,
                120
            );
        }
    }
}

#[test]
fn stack_resize_changes_only_the_adjacent_pair_and_collapse_restores_height() {
    let mut state = LayoutState::default();
    state.secondary.insert(0, state.primary.remove(0));
    let before = geometry(&state);
    let third = before.view(ViewId::Subagents).unwrap().rect;
    let pair_height = before.view(ViewId::Status).unwrap().rect.height
        + before.view(ViewId::Findings).unwrap().rect.height;
    resize(
        &mut state,
        &before,
        SashId::Views(ContainerId::Secondary, 0),
        0,
        100,
    );
    let resized = geometry(&state);
    assert_eq!(resized.view(ViewId::Subagents).unwrap().rect, third);
    assert_eq!(resized.view(ViewId::Findings).unwrap().rect.height, 3);
    assert_eq!(
        resized.view(ViewId::Status).unwrap().rect.height,
        pair_height - 3
    );
    state.toggle_collapsed(ViewId::Status, &resized);
    let collapsed = geometry(&state);
    assert_eq!(collapsed.view(ViewId::Status).unwrap().rect.height, 1);
    assert!(collapsed.view(ViewId::Status).unwrap().content.is_empty());
    assert!(
        !collapsed
            .sashes
            .iter()
            .find(|s| s.id == SashId::Views(ContainerId::Secondary, 0))
            .unwrap()
            .enabled
    );
    state.toggle_collapsed(ViewId::Status, &collapsed);
    assert_eq!(
        geometry(&state).view(ViewId::Status).unwrap().rect,
        resized.view(ViewId::Status).unwrap().rect
    );
}

#[test]
fn move_and_reorder_preserve_the_instance_and_offer_empty_container_targets() {
    let mut state = LayoutState::default();
    state.view_mut(ViewId::Status).scroll = 4;
    state.view_mut(ViewId::Status).collapsed = true;
    state.view_mut(ViewId::Status).expanded_height = 9;
    let before = geometry(&state);
    let secondary = before.container(ContainerId::Secondary);
    let target = before
        .drop_target(Position::new(secondary.x, secondary.y))
        .unwrap();
    assert!(state.move_view(ViewId::Status, target, &before));
    assert_eq!(
        state.primary.iter().map(|v| v.id).collect::<Vec<_>>(),
        [ViewId::Capabilities],
        "the primary container keeps its other view"
    );
    assert!(!state.move_view(ViewId::Capabilities, target, &before));
    assert_eq!(state.primary[0].id, ViewId::Capabilities);
    assert_eq!(state.secondary[0].id, ViewId::Status);
    assert_eq!(state.focus, ViewId::Status);
    assert_eq!(state.view(ViewId::Status).scroll, 4);
    assert_eq!(state.view(ViewId::Status).expanded_height, 9);
    let moved = geometry(&state);
    let last = moved
        .drop_target(Position::new(secondary.x, secondary.bottom() - 1))
        .unwrap();
    assert!(state.move_view(ViewId::Status, last, &moved));
    assert_eq!(state.secondary.last().unwrap().id, ViewId::Status);
    let reordered = geometry(&state);
    let primary = reordered.container(ContainerId::Primary);
    let empty = reordered
        .drop_target(Position::new(primary.x, primary.y))
        .unwrap();
    assert_eq!(empty.index, 0);
    assert!(state.move_view(ViewId::Status, empty, &reordered));
    assert_eq!(
        state.primary.iter().map(|v| v.id).collect::<Vec<_>>(),
        [ViewId::Status, ViewId::Capabilities]
    );
    for container in [ContainerId::Center, ContainerId::Bottom] {
        let rect = reordered.container(container);
        assert!(reordered
            .drop_target(Position::new(rect.x, rect.y))
            .is_none());
    }
}

#[test]
fn empty_secondary_collapses_and_redocking_restores_its_width() {
    let mut state = LayoutState {
        secondary_width: 32,
        ..Default::default()
    };
    while let Some(id) = state.secondary.first().map(|view| view.id) {
        let before = geometry(&state);
        let primary = before.container(ContainerId::Primary);
        let target = before
            .drop_target(Position::new(primary.x + 3, primary.y))
            .unwrap();
        assert!(state.move_view(id, target, &before));
    }
    let closed = geometry(&state);
    assert_eq!(closed.container(ContainerId::Secondary).width, 0);
    assert_eq!(closed.container(ContainerId::Center).right(), 120);
    assert_eq!(
        closed.container(ContainerId::Center).width,
        120 - state.primary_width
    );
    resize(&mut state, &closed, SashId::Primary, 4, 0);
    assert_eq!(state.secondary_width, 32);
    let closed = geometry(&state);
    let target = closed.drop_target(Position::new(119, 10)).unwrap();
    assert_eq!(target.container, ContainerId::Secondary);
    assert_eq!(target.indicator.width, 32);
    assert_eq!(
        target.indicator.height,
        closed.container(ContainerId::Center).height
    );
    assert!(state.move_view(ViewId::Findings, target, &closed));
    assert_eq!(
        geometry(&state).container(ContainerId::Secondary),
        target.indicator
    );
    assert_eq!(state.focus, ViewId::Findings);

    state.primary.append(&mut state.secondary);
    let narrow = LayoutGeometry::compute(Rect::new(0, 0, 40, 30), &state, COMPOSER_ROWS);
    assert!(!narrow.too_small);
    assert_eq!(narrow.container(ContainerId::Primary).width, 16);
    assert_eq!(narrow.container(ContainerId::Center).width, 24);
    assert!(narrow.drop_target(Position::new(39, 10)).is_none());
}

#[test]
fn bottom_container_sizes_to_the_composer_and_leaves_no_separator_row() {
    let state = LayoutState::default();
    // Idle composer: frame (3 rows) plus the mode/guard line.
    let idle = geometry(&state);
    assert_eq!(idle.container(ContainerId::Bottom).height, COMPOSER_ROWS);
    // With the palette open the bottom grows by the palette's own rows.
    let palette = LayoutGeometry::compute(Rect::new(0, 0, 120, 30), &state, COMPOSER_ROWS + 6);
    assert_eq!(
        palette.container(ContainerId::Bottom).height,
        COMPOSER_ROWS + 6
    );
    // The composer sits directly under the work area: the bottom sash is gone.
    assert_eq!(
        idle.container(ContainerId::Bottom).y,
        idle.container(ContainerId::Primary).bottom()
    );
    // Only the column rails and the in-sidebar view rails remain; nothing spans
    // the full workbench width the way the bottom sash did.
    assert!(
        !idle
            .sashes
            .iter()
            .any(|sash| sash.rect.width == idle.workbench.width),
        "the full-width bottom sash is gone"
    );
}

#[test]
fn preferences_round_trip_and_recover_layout_data_independently_of_sessions() {
    let directory = std::env::temp_dir().join(format!(
        "vulnclaw-layout-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    let path = directory.join("tui/layout.json");
    assert_eq!(preferences::load(&path).unwrap().primary_width, 28);
    let mut state = LayoutState {
        primary_width: 33,
        focus: ViewId::Status,
        ..Default::default()
    };
    state.secondary.reverse();
    state.secondary[0].collapsed = true;
    state.secondary[0].expanded_height = 8;
    state.secondary[0].scroll = 5;
    state.output.follow = false;
    preferences::save(&path, &state).unwrap();
    let loaded = preferences::load(&path).unwrap();
    assert_eq!(loaded.primary_width, 33);
    assert_eq!(loaded.secondary[0].id, ViewId::Subagents);
    assert!(loaded.secondary[0].collapsed);
    assert_eq!(loaded.secondary[0].expanded_height, 8);
    assert_eq!(loaded.secondary[0].scroll, 0);
    assert_eq!(loaded.focus, ViewId::Output);
    assert!(loaded.output.follow);

    let (sender, _) = mpsc::channel();
    let mut app = App::new_disconnected(sender);
    app.layout = loaded;
    let session = SessionState::from_app(&app);
    app.layout.primary_width = 35;
    session.apply(&mut app);
    assert_eq!(app.layout.primary_width, 35);
    assert_eq!(app.layout.secondary[0].id, ViewId::Subagents);

    state.primary_width = 0;
    state.primary = vec![
        ViewInstance::new(ViewId::Findings),
        ViewInstance::new(ViewId::Findings),
    ];
    state.secondary.clear();
    preferences::save(&path, &state).unwrap();
    let recovered = preferences::load(&path).unwrap();
    assert_eq!(recovered.primary_width, 16);
    assert_eq!(
        recovered.primary.iter().map(|v| v.id).collect::<Vec<_>>(),
        [ViewId::Findings, ViewId::Status, ViewId::Capabilities]
    );
    assert_eq!(recovered.secondary[0].id, ViewId::Subagents);

    let mut all_right = LayoutState::default();
    all_right.secondary.append(&mut all_right.primary);
    all_right.secondary[0].collapsed = true;
    all_right.secondary[0].expanded_height = 8;
    preferences::save(&path, &all_right).unwrap();
    let recovered = preferences::load(&path).unwrap();
    assert_eq!(recovered.primary[0].id, ViewId::Findings);
    assert!(recovered.primary[0].collapsed);
    assert_eq!(recovered.primary[0].expanded_height, 8);
    assert_eq!(recovered.secondary.len(), 3);

    let mut closed = recovered;
    closed.primary.append(&mut closed.secondary);
    preferences::save(&path, &closed).unwrap();
    let loaded = preferences::load(&path).unwrap();
    assert_eq!(loaded.primary.len(), 4);
    assert_eq!(geometry(&loaded).container(ContainerId::Secondary).width, 0);

    fs::write(&path, b"invalid json").unwrap();
    app.load_layout(path.clone());
    assert!(app.toast.contains("Layout load failed"));
    assert_eq!(app.layout.primary_width, 28);
    // A file in place of the parent directory makes persistence unavailable.
    app.layout_path = Some(path.join("layout.json"));
    app.layout.primary_width = 37;
    app.save_layout();
    assert!(app.toast.contains("Layout save failed"));
    assert_eq!(app.layout.primary_width, 37);
    fs::remove_dir_all(directory).unwrap();
}
