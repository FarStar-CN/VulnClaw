use std::collections::HashSet;

use ratatui::layout::{Position, Rect};
use serde::{Deserialize, Serialize};

pub const SIDE_MIN: u16 = 16;
pub const OUTPUT_MIN: u16 = 24;
pub const VIEW_MIN: u16 = 3;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ContainerId {
    Primary,
    Center,
    Secondary,
    Bottom,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ViewId {
    Status,
    Capabilities,
    Output,
    Findings,
    Subagents,
}

impl ViewId {
    pub fn label(self) -> &'static str {
        match self {
            Self::Status => "Status",
            Self::Capabilities => "Capabilities",
            Self::Output => "Session transcript",
            Self::Findings => "Findings inspector",
            Self::Subagents => "Subagents",
        }
    }

    pub fn movable(self) -> bool {
        matches!(
            self,
            Self::Status | Self::Capabilities | Self::Findings | Self::Subagents
        )
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ViewInstance {
    pub id: ViewId,
    #[serde(default)]
    pub collapsed: bool,
    /// Zero requests an equal share on the first layout.
    #[serde(default)]
    pub expanded_height: u16,
    #[serde(skip)]
    pub scroll: u16,
    #[serde(skip, default = "follow_default")]
    pub follow: bool,
}

fn follow_default() -> bool {
    true
}

impl ViewInstance {
    pub fn new(id: ViewId) -> Self {
        Self {
            id,
            collapsed: false,
            expanded_height: 0,
            scroll: 0,
            follow: true,
        }
    }
}

/// Scroll a view the least amount that brings `row` into a `height`-row viewport.
pub fn scroll_to_row(view: &mut ViewInstance, row: usize, height: usize) {
    if height == 0 {
        return;
    }
    let top = usize::from(view.scroll);
    let next = if row < top {
        row
    } else if row >= top + height {
        row + 1 - height
    } else {
        top
    };
    view.scroll = u16::try_from(next).unwrap_or(u16::MAX);
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct LayoutState {
    pub primary_width: u16,
    pub secondary_width: u16,
    pub primary: Vec<ViewInstance>,
    pub secondary: Vec<ViewInstance>,
    #[serde(skip)]
    pub output: ViewInstance,
    #[serde(skip)]
    pub focus: ViewId,
}

impl Default for LayoutState {
    fn default() -> Self {
        let mut state = Self {
            primary_width: 28,
            secondary_width: 40,
            primary: vec![ViewInstance::new(ViewId::Status)],
            secondary: vec![
                ViewInstance::new(ViewId::Findings),
                ViewInstance::new(ViewId::Subagents),
            ],
            output: ViewInstance::new(ViewId::Output),
            focus: ViewId::Output,
        };
        // Normalize here so a first run, a UI preview and a loaded file all
        // start from the same view set: `normalize` stays the single place that
        // decides which views must exist.
        state.normalize();
        state
    }
}

impl LayoutState {
    /// The views a container holds. `Bottom` is the composer's chrome, not a
    /// view container, so it has none.
    pub fn views(&self, container: ContainerId) -> &[ViewInstance] {
        match container {
            ContainerId::Primary => &self.primary,
            ContainerId::Secondary => &self.secondary,
            ContainerId::Center => std::slice::from_ref(&self.output),
            ContainerId::Bottom => &[],
        }
    }

    fn free_views_mut(&mut self, container: ContainerId) -> &mut Vec<ViewInstance> {
        match container {
            ContainerId::Primary => &mut self.primary,
            ContainerId::Secondary => &mut self.secondary,
            _ => unreachable!("only free containers have ordered view collections"),
        }
    }

    pub fn view(&self, id: ViewId) -> &ViewInstance {
        self.primary
            .iter()
            .chain(&self.secondary)
            .chain([&self.output])
            .find(|view| view.id == id)
            .expect("every view has one instance")
    }

    pub fn view_mut(&mut self, id: ViewId) -> &mut ViewInstance {
        self.primary
            .iter_mut()
            .chain(&mut self.secondary)
            .chain([&mut self.output])
            .find(|view| view.id == id)
            .expect("every view has one instance")
    }

    pub fn normalize(&mut self) {
        self.primary_width = self.primary_width.max(SIDE_MIN);
        self.secondary_width = self.secondary_width.max(SIDE_MIN);
        let mut seen = HashSet::new();
        for views in [&mut self.primary, &mut self.secondary] {
            views.retain(|view| view.id.movable() && seen.insert(view.id));
            for view in views {
                if view.expanded_height != 0 {
                    view.expanded_height = view.expanded_height.max(VIEW_MIN);
                }
            }
        }
        for id in [
            ViewId::Status,
            ViewId::Capabilities,
            ViewId::Findings,
            ViewId::Subagents,
        ] {
            if !seen.contains(&id) {
                let target = if matches!(id, ViewId::Status | ViewId::Capabilities) {
                    &mut self.primary
                } else {
                    &mut self.secondary
                };
                target.push(ViewInstance::new(id));
            }
        }
        if self.primary.is_empty() {
            self.primary.push(self.secondary.remove(0));
        }
    }

    pub fn can_move_view(&self, id: ViewId, target: ContainerId) -> bool {
        id.movable()
            && matches!(target, ContainerId::Primary | ContainerId::Secondary)
            && !(target == ContainerId::Secondary
                && self.primary.len() == 1
                && self.primary[0].id == id)
    }

    pub fn cycle_focus(&mut self, backwards: bool) {
        let order: Vec<_> = self
            .primary
            .iter()
            .chain([&self.output])
            .chain(&self.secondary)
            .map(|view| view.id)
            .collect();
        let current = order.iter().position(|id| *id == self.focus).unwrap_or(0);
        let step = if backwards { order.len() - 1 } else { 1 };
        self.focus = order[(current + step) % order.len()];
    }

    pub fn move_view(&mut self, id: ViewId, target: DropTarget, geometry: &LayoutGeometry) -> bool {
        if !self.can_move_view(id, target.container) {
            return false;
        }
        let source = if self.primary.iter().any(|view| view.id == id) {
            ContainerId::Primary
        } else {
            ContainerId::Secondary
        };
        let index = self
            .views(source)
            .iter()
            .position(|view| view.id == id)
            .unwrap();
        let mut insertion = target.index.min(self.views(target.container).len());
        if source == target.container && index < insertion {
            insertion -= 1;
        }
        if source == target.container && index == insertion {
            self.focus = id;
            return false;
        }
        let mut instance = self.free_views_mut(source).remove(index);
        if !instance.collapsed {
            if let Some(view) = geometry.view(id) {
                instance.expanded_height = view.rect.height;
            }
        }
        self.free_views_mut(target.container)
            .insert(insertion, instance);
        self.focus = id;
        true
    }

    pub fn toggle_collapsed(&mut self, id: ViewId, geometry: &LayoutGeometry) {
        if !id.movable() {
            return;
        }
        let view = self.view_mut(id);
        if !view.collapsed {
            if let Some(region) = geometry.view(id) {
                view.expanded_height = region.rect.height;
            }
        }
        view.collapsed = !view.collapsed;
        self.focus = id;
    }
}

#[derive(Clone, Debug)]
pub struct ViewGeometry {
    pub id: ViewId,
    pub container: ContainerId,
    pub rect: Rect,
    pub title: Rect,
    pub content: Rect,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SashId {
    Primary,
    Secondary,
    Views(ContainerId, usize),
}

#[derive(Clone, Debug)]
pub struct SashGeometry {
    pub id: SashId,
    pub rect: Rect,
    pub enabled: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct DropTarget {
    pub container: ContainerId,
    pub index: usize,
    pub indicator: Rect,
}

/// Rows the boxed header occupies: top border, content, bottom border.
pub const HEADER_ROWS: u16 = 3;

#[derive(Clone, Debug)]
pub struct LayoutGeometry {
    pub header: Rect,
    pub hotbar: Rect,
    pub workbench: Rect,
    pub containers: [(ContainerId, Rect); 4],
    pub views: Vec<ViewGeometry>,
    pub sashes: Vec<SashGeometry>,
    pub secondary_dock: Option<Rect>,
    pub minimum_size: (u16, u16),
    pub too_small: bool,
}

impl LayoutGeometry {
    pub fn compute(area: Rect, state: &LayoutState, required_input: u16) -> Self {
        let upper_min = stack_min(&state.primary)
            .max(stack_min(&state.secondary))
            .max(6);
        let right_min = if state.secondary.is_empty() {
            0
        } else {
            SIDE_MIN
        };
        let minimum_size = (
            SIDE_MIN + OUTPUT_MIN + right_min,
            upper_min + required_input + HEADER_ROWS + 1,
        );
        let empty = Rect::default();
        let mut geometry = Self {
            header: Rect::new(area.x, area.y, area.width, area.height.min(HEADER_ROWS)),
            hotbar: Rect::new(
                area.x,
                area.bottom().saturating_sub(1),
                area.width,
                u16::from(area.height > 2),
            ),
            workbench: Rect::new(
                area.x,
                area.y.saturating_add(HEADER_ROWS),
                area.width,
                area.height.saturating_sub(HEADER_ROWS + 1),
            ),
            containers: [
                (ContainerId::Primary, empty),
                (ContainerId::Center, empty),
                (ContainerId::Secondary, empty),
                (ContainerId::Bottom, empty),
            ],
            views: Vec::new(),
            sashes: Vec::new(),
            secondary_dock: None,
            minimum_size,
            too_small: area.width < minimum_size.0 || area.height < minimum_size.1,
        };
        if geometry.too_small {
            return geometry;
        }
        // The composer sizes itself to its content. There is no bottom sash and
        // no user-adjustable bottom height, so the work area gets everything the
        // fixed chrome does not claim.
        let bottom_height = required_input;
        let upper_height = area.height.saturating_sub(HEADER_ROWS + 1 + bottom_height);
        let available_width = area.width;
        let mut left = u32::from(state.primary_width.max(SIDE_MIN));
        let mut right = if state.secondary.is_empty() {
            0
        } else {
            u32::from(state.secondary_width.max(SIDE_MIN))
        };
        let shortage =
            (left + right + u32::from(OUTPUT_MIN)).saturating_sub(u32::from(available_width));
        let right_shrink = shortage.min(right - u32::from(right_min));
        right -= right_shrink;
        left -= (shortage - right_shrink).min(left - u32::from(SIDE_MIN));
        let left = left as u16;
        let right = right as u16;
        let center = available_width - left - right;
        let y = area.y + HEADER_ROWS;
        let primary = Rect::new(area.x, y, left, upper_height);
        let output = Rect::new(primary.right(), y, center, upper_height);
        let secondary = Rect::new(output.right(), y, right, upper_height);
        let bottom = Rect::new(area.x, y + upper_height, area.width, bottom_height);
        geometry.containers = [
            (ContainerId::Primary, primary),
            (ContainerId::Center, output),
            (ContainerId::Secondary, secondary),
            (ContainerId::Bottom, bottom),
        ];
        geometry.sashes.push(SashGeometry {
            id: SashId::Primary,
            rect: Rect::new(primary.right() - 1, y, 2, upper_height),
            enabled: true,
        });
        if right > 0 {
            geometry.sashes.push(SashGeometry {
                id: SashId::Secondary,
                rect: Rect::new(output.right() - 1, y, 2, upper_height),
                enabled: true,
            });
        } else if area.width >= SIDE_MIN * 2 + OUTPUT_MIN {
            let dock_width = state
                .secondary_width
                .max(SIDE_MIN)
                .min(output.width.saturating_sub(OUTPUT_MIN).max(SIDE_MIN));
            geometry.secondary_dock = Some(Rect::new(
                area.right() - dock_width,
                y,
                dock_width,
                upper_height,
            ));
        }
        geometry.add_stack(ContainerId::Primary, primary, &state.primary);
        geometry.add_view(ContainerId::Center, output, &state.output);
        geometry.add_stack(ContainerId::Secondary, secondary, &state.secondary);
        geometry
    }

    pub fn container(&self, id: ContainerId) -> Rect {
        self.containers
            .iter()
            .find(|(candidate, _)| *candidate == id)
            .unwrap()
            .1
    }

    pub fn view(&self, id: ViewId) -> Option<&ViewGeometry> {
        self.views.iter().find(|view| view.id == id)
    }

    pub fn drop_target(&self, point: Position) -> Option<DropTarget> {
        if self.too_small {
            return None;
        }
        if let Some(indicator) = self.secondary_dock {
            if point.x >= indicator.right().saturating_sub(3)
                && point.x < indicator.right()
                && point.y >= indicator.y
                && point.y < indicator.bottom()
            {
                return Some(DropTarget {
                    container: ContainerId::Secondary,
                    index: 0,
                    indicator,
                });
            }
        }
        for container in [ContainerId::Primary, ContainerId::Secondary] {
            let rect = self.container(container);
            if !rect.contains(point) {
                continue;
            }
            let views: Vec<_> = self
                .views
                .iter()
                .filter(|view| view.container == container)
                .collect();
            let index = views
                .iter()
                .position(|view| point.y < view.rect.y + view.rect.height.div_ceil(2))
                .unwrap_or(views.len());
            let y = views.get(index).map_or_else(
                || {
                    views
                        .last()
                        .map_or(rect.y, |view| view.rect.bottom().min(rect.bottom() - 1))
                },
                |view| view.rect.y,
            );
            return Some(DropTarget {
                container,
                index,
                indicator: Rect::new(rect.x, y, rect.width, 1),
            });
        }
        None
    }

    fn add_view(&mut self, container: ContainerId, rect: Rect, view: &ViewInstance) {
        self.views.push(ViewGeometry {
            id: view.id,
            container,
            rect,
            title: Rect::new(rect.x, rect.y, rect.width, 1),
            content: if view.collapsed {
                Rect::default()
            } else {
                Rect::new(
                    rect.x + 1,
                    rect.y + 1,
                    rect.width.saturating_sub(2),
                    rect.height.saturating_sub(2),
                )
            },
        });
    }

    fn add_stack(&mut self, container: ContainerId, rect: Rect, views: &[ViewInstance]) {
        let heights = stack_heights(views, rect.height);
        let mut y = rect.y;
        for (index, (view, height)) in views.iter().zip(heights).enumerate() {
            self.add_view(container, Rect::new(rect.x, y, rect.width, height), view);
            y += height;
            if let Some(next) = views.get(index + 1) {
                self.sashes.push(SashGeometry {
                    id: SashId::Views(container, index),
                    // The preceding bottom border leaves the following title free to drag.
                    rect: Rect::new(rect.x, y - 1, rect.width, 1),
                    enabled: !view.collapsed && !next.collapsed,
                });
            }
        }
    }
}

fn stack_min(views: &[ViewInstance]) -> u16 {
    views
        .iter()
        .map(|view| if view.collapsed { 1 } else { VIEW_MIN })
        .sum::<u16>()
}

fn stack_heights(views: &[ViewInstance], height: u16) -> Vec<u16> {
    let expanded = views.iter().filter(|view| !view.collapsed).count() as u16;
    let collapsed = views.len() as u16 - expanded;
    let share = height.saturating_sub(collapsed) / expanded.max(1);
    let mut heights: Vec<_> = views
        .iter()
        .map(|view| {
            if view.collapsed {
                1
            } else if view.expanded_height == 0 {
                share.max(VIEW_MIN)
            } else {
                view.expanded_height.max(VIEW_MIN)
            }
        })
        .collect();
    let total: u32 = heights.iter().map(|value| u32::from(*value)).sum();
    let mut shortage = total.saturating_sub(u32::from(height));
    for (view, view_height) in views.iter().zip(&mut heights).rev() {
        if !view.collapsed {
            let reduction = shortage.min(u32::from(*view_height - VIEW_MIN));
            *view_height -= reduction as u16;
            shortage -= reduction;
        }
    }
    if total < u32::from(height) {
        if let Some(index) = views.iter().rposition(|view| !view.collapsed) {
            heights[index] += (u32::from(height) - total) as u16;
        }
    }
    heights
}

#[derive(Clone, Debug)]
pub enum Gesture {
    Move {
        id: ViewId,
        origin: Position,
        dragging: bool,
        target: Option<DropTarget>,
    },
    Resize {
        sash: SashId,
        origin: Position,
        original: Box<LayoutState>,
        geometry: Box<LayoutGeometry>,
    },
}

pub fn resize(state: &mut LayoutState, geometry: &LayoutGeometry, sash: SashId, dx: i32, dy: i32) {
    match sash {
        SashId::Primary | SashId::Secondary => {
            let left = geometry.container(ContainerId::Primary).width;
            let right = geometry.container(ContainerId::Secondary).width;
            let center = geometry.container(ContainerId::Center).width;
            state.primary_width = left;
            if right > 0 {
                state.secondary_width = right;
            }
            if sash == SashId::Primary {
                state.primary_width = (i32::from(left) + dx)
                    .clamp(i32::from(SIDE_MIN), i32::from(left + center - OUTPUT_MIN))
                    as u16;
            } else {
                state.secondary_width = (i32::from(right) - dx)
                    .clamp(i32::from(SIDE_MIN), i32::from(right + center - OUTPUT_MIN))
                    as u16;
            }
        }
        SashId::Views(container, index) => {
            let views = state.free_views_mut(container);
            if views[index].collapsed || views[index + 1].collapsed {
                return;
            }
            // Materialize the rendered sizes so a drag cannot redistribute spare rows.
            for view in views.iter_mut().filter(|view| !view.collapsed) {
                view.expanded_height = geometry.view(view.id).unwrap().rect.height;
            }
            let total = views[index].expanded_height + views[index + 1].expanded_height;
            let first = (i32::from(views[index].expanded_height) + dy)
                .clamp(i32::from(VIEW_MIN), i32::from(total - VIEW_MIN))
                as u16;
            views[index].expanded_height = first;
            views[index + 1].expanded_height = total - first;
        }
    }
}
