use std::sync::mpsc::Sender;
use std::time::Instant;

use serde::{Deserialize, Serialize};

use crate::exec::BackendHandle;
use crate::prompts::text;
use crate::protocol::{AppEvent, BackendEvent, ClientRequest, Finding, StateSnapshot};
use crate::sessions::{self, SessionState};
use crate::skills::catalog::{skill_tree, SkillNode};

use ratatui::{
    backend::TestBackend,
    buffer::Buffer,
    layout::{Constraint, Direction, Layout, Rect},
    widgets::{Block, Borders, Paragraph, Wrap},
    Terminal,
};

/// Extract the visible text of a rectangular screen region from a rendered
/// buffer. Used to copy a single workbench pane without pulling in neighbouring
/// panes — the terminal's own drag-select is a whole-screen block selection and
/// cannot be confined to one logical pane.
fn extract_rect_text(buffer: &Buffer, rect: Rect) -> String {
    let area = buffer.area;
    let mut out = String::new();
    for y in rect.y..rect.bottom() {
        if y >= area.height {
            break;
        }
        let mut line = String::new();
        for x in rect.x..rect.right() {
            if x >= area.width {
                break;
            }
            let idx = (y * area.width + x) as usize;
            if let Some(cell) = buffer.content.get(idx) {
                line.push_str(cell.symbol());
            }
        }
        out.push_str(line.trim_end());
        out.push('\n');
    }
    out
}

#[cfg(not(windows))]
fn base64_encode(data: &[u8]) -> String {
    const CHARS: &[u8; 64] =
        b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity((data.len() + 2) / 3 * 4);
    for chunk in data.chunks(3) {
        let b0 = chunk[0] as u32;
        let b1 = *chunk.get(1).unwrap_or(&0) as u32;
        let b2 = *chunk.get(2).unwrap_or(&0) as u32;
        let n = (b0 << 16) | (b1 << 8) | b2;
        out.push(CHARS[((n >> 18) & 63) as usize] as char);
        out.push(CHARS[((n >> 12) & 63) as usize] as char);
        out.push(if chunk.len() > 1 {
            CHARS[((n >> 6) & 63) as usize] as char
        } else {
            '='
        });
        out.push(if chunk.len() > 2 {
            CHARS[(n & 63) as usize] as char
        } else {
            '='
        });
    }
    out
}

/// Write text to the system clipboard without pulling in a third-party crate.
/// Windows: persist to a temp UTF-8 file and use the built-in `Set-Clipboard`
/// (handles Unicode correctly). Unix: emit an OSC 52 sequence to the terminal.
fn copy_to_clipboard(text: &str) -> bool {
    #[cfg(windows)]
    {
        use std::process::Command;
        let tmp = std::env::temp_dir().join(format!("vulnclaw-cb-{}.txt", std::process::id()));
        if std::fs::write(&tmp, text.as_bytes()).is_err() {
            return false;
        }
        let path = tmp.to_string_lossy().replace('\'', "''");
        let ps = format!("Set-Clipboard -LiteralPath '{}'", path);
        let status = Command::new("powershell.exe")
            .args([
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-Command",
                &ps,
            ])
            .status();
        let _ = std::fs::remove_file(&tmp);
        matches!(status, Ok(s) if s.success())
    }
    #[cfg(not(windows))]
    {
        use std::io::Write;
        let b64 = base64_encode(text.as_bytes());
        let seq = format!("\x1b]52;c;{}\x07", b64);
        let _ = std::io::stdout().write_all(seq.as_bytes());
        let _ = std::io::stdout().flush();
        true
    }
}


#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ExecutionMode {
    Plan,
    Agent,
    Yolo,
}

impl ExecutionMode {
    pub fn next(self) -> Self {
        match self {
            // VulnClaw is a task-driven workbench: Tab cycles between the
            // read-only plan posture and the live agent posture. YOLO is
            // retired.
            Self::Plan => Self::Agent,
            Self::Agent => Self::Plan,
            Self::Yolo => Self::Plan,
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Self::Plan => "Plan",
            Self::Agent => "Agent",
            Self::Yolo => "YOLO",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PermissionMode {
    Ask,
    AutoReview,
    FullAccess,
}

impl PermissionMode {
    pub fn next(self) -> Self {
        match self {
            Self::Ask => Self::AutoReview,
            Self::AutoReview => Self::FullAccess,
            Self::FullAccess => Self::Ask,
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Self::Ask => "Ask",
            Self::AutoReview => "Auto-review",
            Self::FullAccess => "Full access",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ActivePane {
    Workspace,
    Transcript,
    Findings,
}

impl ActivePane {
    pub fn next(self) -> Self {
        match self {
            Self::Workspace => Self::Transcript,
            Self::Transcript => Self::Findings,
            Self::Findings => Self::Workspace,
        }
    }

    pub fn previous(self) -> Self {
        match self {
            Self::Workspace => Self::Findings,
            Self::Transcript => Self::Workspace,
            Self::Findings => Self::Transcript,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub enum TranscriptKind {
    User,
    System,
    Status,
    Log,
    Reasoning,
    Error,
    Finding,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct TranscriptItem {
    pub kind: TranscriptKind,
    pub text: String,
}

#[derive(Clone, Debug)]
pub struct SlashCommand {
    pub command: String,
    pub description: &'static str,
}

const LOCAL_SLASH_COMMANDS: &[(&str, &str)] = &[
    ("/scope ", "update session scope defaults"),
    ("/report", "show report export guidance"),
    ("/config", "show configuration guidance"),
    ("/clear", "clear the transcript"),
    ("/help", "list available commands"),
];

const MAX_COMMAND_HISTORY: usize = 50;

#[derive(Clone, Debug)]
pub struct OperationReceipt {
    pub command: String,
    pub phase: String,
    pub findings: usize,
}

pub struct App {
    pub mode: ExecutionMode,
    pub permission: PermissionMode,
    pub active_pane: ActivePane,
    pub input: String,
    pub input_cursor: usize,
    pub command_history: Vec<String>,
    history_index: Option<usize>,
    history_draft: String,
    pub transcript: Vec<TranscriptItem>,
    pub findings: Vec<Finding>,
    pub findings_scroll: u16,
    pub transcript_scroll: u16,
    /// When true the Session transcript tracks new output automatically,
    /// pinning the view to the bottom as lines arrive. Set false the moment the
    /// user scrolls up to read history; re-enabled once they scroll back to the
    /// bottom. See `autoscroll_transcript`.
    pub transcript_follow: bool,
    pub palette_selection: usize,
    pub show_reasoning: bool,
    pub running: bool,
    pub worker_active: bool,
    /// One persistent Python backend for the entire terminal session.
    pub backend: Option<BackendHandle>,
    pub backend_ready: bool,
    pub backend_pid: Option<u32>,
    pub config_ready: Option<bool>,
    /// Task verbs advertised by the backend in `ready.capabilities.commands`.
    /// Local presentation commands such as `/help` are deliberately separate.
    pub backend_commands: Vec<String>,
    /// Optional management operations advertised by the Python backend.
    pub backend_control_operations: Vec<String>,
    pub backend_supports_cancellation: bool,
    pub target: String,
    pub phase: String,
    pub active_task_id: Option<String>,
    pub task_constraints: serde_json::Value,
    pub last_run: Option<serde_json::Value>,
    pub evidence: Vec<serde_json::Value>,
    pub constraint_violations: Vec<String>,
    request_counter: u64,
    pub active_receipt: Option<OperationReceipt>,
    pub last_receipt: Option<OperationReceipt>,
    /// Monotonic instant the current worker started, used to render a live
    /// elapsed-time readout (`mm:ss`) in the header while a command runs.
    pub worker_started_at: Option<Instant>,
    pub show_attack_chain: bool,
    pub pending_task: Option<String>,
    pub skills: Vec<SkillNode>,
    /// Last known terminal viewport size, captured each frame. Used to render an
    /// offscreen copy of the focused pane for independent clipboard copies.
    pub terminal_size: Rect,
    /// Transient feedback line shown in the hotbar (e.g. "Copied …"). Cleared on
    /// the next key press.
    pub toast: String,
    #[cfg_attr(test, allow(dead_code))]
    sender: Sender<AppEvent>,
}

impl App {
    pub fn new(sender: Sender<AppEvent>) -> Self {
        #[allow(unused_mut)]
        let mut app = Self {
            mode: ExecutionMode::Agent,
            permission: PermissionMode::AutoReview,
            active_pane: ActivePane::Transcript,
            input: String::new(),
            input_cursor: 0,
            command_history: Vec::new(),
            history_index: None,
            history_draft: String::new(),
            transcript: vec![
                TranscriptItem {
                    kind: TranscriptKind::System,
                    text: text::WELCOME.to_owned(),
                },
                TranscriptItem {
                    kind: TranscriptKind::Status,
                    text: text::READY.to_owned(),
                },
            ],
            findings: Vec::new(),
            findings_scroll: 0,
            transcript_scroll: 0,
            transcript_follow: true,
            palette_selection: 0,
            show_reasoning: true,
            running: true,
            worker_active: false,
            backend: None,
            backend_ready: false,
            backend_pid: None,
            config_ready: None,
            backend_commands: Vec::new(),
            backend_control_operations: Vec::new(),
            backend_supports_cancellation: false,
            target: String::new(),
            phase: "idle".to_owned(),
            active_task_id: None,
            task_constraints: serde_json::json!({}),
            last_run: None,
            evidence: Vec::new(),
            constraint_violations: Vec::new(),
            request_counter: 0,
            active_receipt: None,
            last_receipt: None,
            worker_started_at: None,
            show_attack_chain: false,
            pending_task: None,
            skills: skill_tree(),
            terminal_size: Rect::default(),
            toast: String::new(),
            sender,
        };
        #[cfg(not(test))]
        app.connect_backend();
        app
    }

    fn next_request_id(&mut self) -> String {
        self.request_counter = self.request_counter.saturating_add(1);
        format!("rust-{}-{}", std::process::id(), self.request_counter)
    }

    #[cfg(not(test))]
    fn connect_backend(&mut self) {
        match crate::exec::spawn_backend(self.sender.clone()) {
            Ok(handle) => {
                let bootstrap = std::env::var("VULNCLAW_TUI_BOOTSTRAP")
                    .ok()
                    .and_then(|raw| serde_json::from_str(&raw).ok())
                    .unwrap_or_else(|| serde_json::json!({}));
                let request = ClientRequest::initialize(self.next_request_id(), bootstrap);
                if let Err(error) = handle.send(&request) {
                    handle.wait_or_kill(std::time::Duration::from_millis(100));
                    self.error(format!("Could not initialize Python backend: {error}"));
                } else {
                    self.backend = Some(handle);
                    self.status("Connecting to the VulnClaw Python backend...");
                }
            }
            Err(error) => self.error(format!("Could not start VulnClaw Python backend: {error}")),
        }
    }

    pub fn submit(&mut self) {
        let command = strip_prompt_prefix(self.input.trim());
        if command.is_empty() {
            return;
        }
        self.record_command(&command);
        self.push(TranscriptKind::User, format!("> {command}"));
        self.clear_composer();
        if command == "/help" {
            let backend = if self.backend_commands.is_empty() {
                "none advertised yet".to_owned()
            } else {
                self.backend_commands
                    .iter()
                    .map(|command| format!("/{command} <target>"))
                    .collect::<Vec<_>>()
                    .join(", ")
            };
            self.status(format!(
                "Backend tasks: {backend}. Local commands: /scope, /report, /config, /clear, /help. Ctrl+C aborts a running command."
            ));
        } else if command == "/clear" {
            self.transcript.clear();
            self.transcript_scroll = 0;
            self.transcript_follow = true;
            self.status("Transcript cleared. Findings remain available in the inspector.");
        } else if command == "/report" {
            self.status(
                "Use vulnclaw report <result.json> [--pdf] to write the report; the TUI shows findings live.",
            );
        } else if command == "/config" {
            self.status("Use vulnclaw config set <key> <value> for llm.provider / llm.api_key / llm.base_url / llm.model.");
        } else if let Some((verb, arguments)) = split_slash_command(&command) {
            if verb == "scope" {
                self.request_scope_control(arguments);
            } else if self.backend_commands.iter().any(|item| item == verb) {
                self.request_task(verb, arguments);
            } else {
                self.error(format!("Unknown command: {command}"));
            }
        } else {
            self.error(format!("Unknown command: {command}"));
        }
    }

    pub fn cycle_mode(&mut self) {
        self.set_mode(self.mode.next());
    }

    pub fn cycle_permission(&mut self) {
        self.permission = self.permission.next();
        self.status(format!(
            "Permission posture switched to {}. Explicit confirmation still required before a task starts.",
            self.permission.label()
        ));
    }

    pub fn cycle_active_pane(&mut self, backwards: bool) {
        self.active_pane = if backwards {
            self.active_pane.previous()
        } else {
            self.active_pane.next()
        };
    }

    pub fn scroll_active_pane(&mut self, down: bool) {
        match self.active_pane {
            // Findings keeps the original unbounded behaviour so a lone Down press
            // still increments even when the list is short (preserves existing tests).
            ActivePane::Findings => {
                if down {
                    self.findings_scroll = self.findings_scroll.saturating_add(1);
                } else {
                    self.findings_scroll = self.findings_scroll.saturating_sub(1);
                }
            }
            ActivePane::Workspace | ActivePane::Transcript => {
                let max = self.transcript_max_scroll();
                if down {
                    self.transcript_scroll = self.transcript_scroll.saturating_add(1);
                } else {
                    self.transcript_scroll = self.transcript_scroll.saturating_sub(1);
                }
                let max_u16 = (max as u16).min(u16::MAX);
                if self.transcript_scroll > max_u16 {
                    self.transcript_scroll = max_u16;
                }
                // Reaching the bottom resumes auto-follow; leaving it disables it.
                self.transcript_follow = (self.transcript_scroll as usize) >= max;
            }
        }
    }

    /// Rectangle of the Session transcript panel (the wide centre pane), computed
    /// from the same split used by `ui::layout::render_workbench`. Independent of
    /// which pane is currently focused so auto-follow always anchors the
    /// transcript view, not the narrow Workspace sidebar.
    fn transcript_panel_rect(&self) -> Rect {
        let area = self.terminal_size;
        let composer_height: u16 = if self.pending_task.is_some() {
            3
        } else if self.palette_visible() {
            7
        } else {
            1
        };
        let workbench = Rect {
            x: area.x,
            y: area.y.saturating_add(2),
            width: area.width,
            height: area.height.saturating_sub(2 + composer_height + 1),
        };
        let panels = Layout::default()
            .direction(Direction::Horizontal)
            .constraints([Constraint::Length(28), Constraint::Min(36), Constraint::Length(40)])
            .split(workbench);
        panels[1]
    }

    /// Maximum vertical scroll offset for the transcript: total wrapped rows
    /// minus the visible rows. Uses ratatui's own `Paragraph::line_count` so the
    /// wrap accounting (CJK widths, word breaks) matches the real render exactly.
    fn transcript_max_scroll(&self) -> usize {
        let rect = self.transcript_panel_rect();
        if rect.width < 3 || rect.height < 3 {
            return 0;
        }
        let inner_width = rect.width.saturating_sub(2);
        let lines = crate::ui::transcript::build_lines(self);
        let paragraph = Paragraph::new(lines)
            .wrap(Wrap { trim: false })
            .block(Block::default().borders(Borders::ALL));
        let total_rows = paragraph.line_count(inner_width);
        total_rows.saturating_sub(rect.height as usize)
    }

    /// Keep the transcript pinned to the newest output. Called once per frame
    /// (before drawing) while `transcript_follow` is set; a manual scroll-up
    /// clears the flag so we stop yanking the view away from the user.
    pub fn autoscroll_transcript(&mut self) {
        if self.transcript_follow {
            self.transcript_scroll = (self.transcript_max_scroll() as u16).min(u16::MAX);
        }
    }

    pub fn active_pane_label(&self) -> &'static str {
        match self.active_pane {
            ActivePane::Workspace => "Workspace",
            ActivePane::Transcript => "Session transcript",
            ActivePane::Findings => "Findings inspector",
        }
    }

    /// Screen rectangle occupied by the currently focused workbench pane.
    /// Mirrors the split used by `ui::layout::render_workbench` so the copied
    /// region never bleeds into neighbouring panes.
    pub fn active_pane_rect(&self, area: Rect) -> Rect {
        let composer_height: u16 = if self.pending_task.is_some() {
            3
        } else if self.palette_visible() {
            7
        } else {
            1
        };
        let workbench = Rect {
            x: area.x,
            y: area.y.saturating_add(2),
            width: area.width,
            height: area.height.saturating_sub(2 + composer_height + 1),
        };
        let panels = Layout::default()
            .direction(Direction::Horizontal)
            .constraints([Constraint::Length(28), Constraint::Min(36), Constraint::Length(40)])
            .split(workbench);
        match self.active_pane {
            ActivePane::Workspace => panels[0],
            ActivePane::Transcript => panels[1],
            ActivePane::Findings => panels[2],
        }
    }

    /// Copy the focused workbench pane to the system clipboard. Each pane is
    /// copied independently — copying one never drags in the others. The
    /// terminal's own drag-select is a whole-screen block selection that cannot
    /// be confined to a single logical pane, so this is the reliable per-pane
    /// copy path.
    pub fn copy_active_pane(&mut self) {
        let area = self.terminal_size;
        if area.width == 0 || area.height == 0 {
            self.toast = "Copy unavailable: terminal size unknown".into();
            return;
        }
        let backend = TestBackend::new(area.width, area.height);
        let mut term = match Terminal::new(backend) {
            Ok(t) => t,
            Err(_) => {
                self.toast = "Copy failed: cannot render pane".into();
                return;
            }
        };
        let app_ref: &App = self;
        if term.draw(|f| crate::ui::draw(f, app_ref)).is_err() {
            self.toast = "Copy failed: cannot render pane".into();
            return;
        }
        let buffer = term.backend().buffer();
        let rect = self.active_pane_rect(area);
        let text = extract_rect_text(buffer, rect);
        let label = self.active_pane_label();
        if copy_to_clipboard(&text) {
            self.toast = format!(
                "Copied {} to clipboard ({} chars)",
                label,
                text.chars().count()
            );
        } else {
            self.toast = "Copy failed: clipboard unavailable".into();
        }
    }

    pub fn append_input(&mut self, character: char) {
        self.insert_text(&character.to_string());
    }

    pub fn clear_composer(&mut self) {
        self.input.clear();
        self.input_cursor = 0;
        self.palette_selection = 0;
        self.clear_history_navigation();
    }

    /// Insert pasted/IME text into the presentation-only composer. Newlines are
    /// dropped so a multi-line paste never submits more than one command.
    pub fn insert_text(&mut self, text: &str) {
        for character in text
            .chars()
            .filter(|character| *character != '\r' && *character != '\n')
        {
            self.input.insert(self.input_cursor, character);
            self.input_cursor += character.len_utf8();
        }
        self.palette_selection = 0;
        self.clear_history_navigation();
    }

    pub fn delete_input(&mut self) {
        if self.input_cursor == 0 {
            return;
        }
        let previous = previous_char_boundary(&self.input, self.input_cursor);
        self.input.drain(previous..self.input_cursor);
        self.input_cursor = previous;
        self.palette_selection = 0;
        self.clear_history_navigation();
    }

    pub fn delete_forward_input(&mut self) {
        if self.input_cursor >= self.input.len() {
            return;
        }
        let next = next_char_boundary(&self.input, self.input_cursor);
        self.input.drain(self.input_cursor..next);
        self.palette_selection = 0;
        self.clear_history_navigation();
    }

    pub fn move_input_cursor(&mut self, right: bool) {
        self.input_cursor = if right {
            next_char_boundary(&self.input, self.input_cursor)
        } else {
            previous_char_boundary(&self.input, self.input_cursor)
        };
        self.palette_selection = 0;
    }

    pub fn move_input_cursor_to_edge(&mut self, end: bool) {
        self.input_cursor = if end { self.input.len() } else { 0 };
        self.palette_selection = 0;
    }

    pub fn recall_history(&mut self, older: bool) {
        if self.command_history.is_empty() {
            return;
        }
        let next_index = if older {
            match self.history_index {
                Some(index) => index.saturating_sub(1),
                None => {
                    self.history_draft = self.input.clone();
                    self.command_history.len() - 1
                }
            }
        } else {
            let Some(index) = self.history_index else {
                return;
            };
            if index + 1 == self.command_history.len() {
                self.history_index = None;
                self.set_input(self.history_draft.clone());
                self.history_draft.clear();
                return;
            }
            index + 1
        };
        self.history_index = Some(next_index);
        self.set_input(self.command_history[next_index].clone());
    }

    pub fn palette_visible(&self) -> bool {
        self.input_cursor == self.input.len()
            && self.input.trim_start().starts_with('/')
            && !self.suggested_commands().is_empty()
    }

    pub fn suggested_commands(&self) -> Vec<SlashCommand> {
        let query = self.input.trim_start().to_ascii_lowercase();
        if !query.starts_with('/') {
            return Vec::new();
        }
        let backend = self.backend_commands.iter().map(|command| SlashCommand {
            command: format!("/{command} "),
            description: "run a task through the Python backend",
        });
        let local = LOCAL_SLASH_COMMANDS.iter().map(|(command, description)| SlashCommand {
            command: (*command).to_owned(),
            description,
        });
        backend
            .chain(local)
            .filter(|item| item.command.starts_with(&query))
            .collect()
    }

    pub fn select_next_command(&mut self, down: bool) {
        let count = self.suggested_commands().len();
        if count == 0 {
            return;
        }
        self.palette_selection = if down {
            (self.palette_selection + 1) % count
        } else {
            (self.palette_selection + count - 1) % count
        };
    }

    pub fn accept_selected_command(&mut self) -> bool {
        let commands = self.suggested_commands();
        let Some(command) = commands.get(self.palette_selection) else {
            return false;
        };
        self.set_input(command.command.to_owned());
        true
    }

    pub fn should_complete_selected_command(&self) -> bool {
        let commands = self.suggested_commands();
        let Some(command) = commands.get(self.palette_selection) else {
            return false;
        };
        (command.command.ends_with(' ') && self.input == command.command.trim_end())
            || (self.input.trim() != command.command.trim() && !self.input.ends_with(' '))
    }

    pub fn confirm_task(&mut self) {
        let Some(command_line) = self.pending_task.take() else {
            return;
        };
        self.status("TUI confirmation recorded. Starting task.");
        self.start_task(command_line);
    }

    pub fn dismiss_task(&mut self) {
        if self.pending_task.take().is_some() {
            self.status("Task command cancelled before execution.");
        }
    }

    pub fn apply_event(&mut self, event: AppEvent) {
        match event {
            AppEvent::Backend(stream) => match stream {
                BackendEvent::Ready {
                    backend,
                    capabilities,
                    runtime,
                    state,
                } => {
                    self.backend_ready = true;
                    self.backend_pid = Some(backend.pid);
                    self.config_ready = Some(runtime.config_ready);
                    self.backend_commands = capabilities
                        .commands
                        .into_iter()
                        .filter_map(normalize_backend_command)
                        .collect();
                    self.backend_commands.sort();
                    self.backend_commands.dedup();
                    self.backend_control_operations = capabilities
                        .control_operations
                        .into_iter()
                        .filter(|operation| !operation.trim().is_empty())
                        .collect();
                    self.backend_control_operations.sort();
                    self.backend_control_operations.dedup();
                    self.backend_supports_cancellation = capabilities.cancellation;
                    if !capabilities.authoritative_state {
                        self.error(
                            "Backend does not advertise authoritative state; refusing task commands.",
                        );
                        self.backend_commands.clear();
                    }
                    self.apply_backend_state(state);
                    if !runtime.skills.is_empty() {
                        self.skills = vec![SkillNode {
                            name: "Python skills".into(),
                            children: runtime
                                .skills
                                .into_iter()
                                .map(|name| SkillNode {
                                    name,
                                    children: Vec::new(),
                                })
                                .collect(),
                        }];
                    }
                    self.status(format!(
                        "Python backend ready (pid {}, VulnClaw {}, {}/{}).",
                        backend.pid, backend.version, runtime.provider, runtime.model
                    ));
                    if !runtime.config_ready {
                        self.error(
                            "LLM credentials are not configured. Run `vulnclaw config set` before starting a task.",
                        );
                    }
                }
                BackendEvent::State { state } => self.apply_backend_state(state),
                BackendEvent::TaskStarted {
                    task_id,
                    command,
                    normalized_command,
                    _target: _,
                    _resume: _,
                    _constraints: _,
                    state,
                } => {
                    if self.active_task_id.as_deref() != Some(task_id.as_str()) {
                        return;
                    }
                    self.worker_active = true;
                    self.worker_started_at = Some(Instant::now());
                    self.apply_backend_state(state);
                    if let Some(receipt) = self.active_receipt.as_mut() {
                        receipt.phase = format!("{} running", command);
                        if !normalized_command.is_empty() {
                            receipt.command = normalized_command;
                        }
                    }
                }
                BackendEvent::Status {
                    task_id,
                    status: message,
                } => {
                    if !self.is_current_task(&task_id) {
                        return;
                    }
                    self.update_receipt(&message);
                    self.status(message);
                }
                BackendEvent::Finding { task_id, finding } => {
                    if !self.is_current_task(&task_id) {
                        return;
                    }
                    self.upsert_finding(finding);
                }
                BackendEvent::Reasoning { task_id, text: chunk } => {
                    if !self.is_current_task(&task_id) {
                        return;
                    }
                    self.update_receipt("Thinking");
                    self.push(TranscriptKind::Reasoning, chunk);
                }
                BackendEvent::Log {
                    task_id,
                    message: line,
                } => {
                    if !self.is_current_task(&task_id) {
                        return;
                    }
                    self.update_receipt("Running");
                    self.push(TranscriptKind::Log, line);
                }
                BackendEvent::ToolCall {
                    task_id,
                    tool,
                    arguments,
                } => {
                    if !self.is_current_task(&task_id) {
                        return;
                    }
                    self.update_receipt("Using tool");
                    self.push(
                        TranscriptKind::Log,
                        format!("→ tool: {tool} {}", truncate_text(&arguments, 160)),
                    );
                }
                BackendEvent::ToolResult { task_id, result } => {
                    if !self.is_current_task(&task_id) {
                        return;
                    }
                    self.update_receipt("Running");
                    self.push(
                        TranscriptKind::Log,
                        format!("→ result: {}", truncate_text(&result, 240)),
                    );
                }
                BackendEvent::ApprovalRequired { task_id, question } => {
                    if !self.is_current_task(&task_id) {
                        return;
                    }
                    self.push(TranscriptKind::Status, format!("Approval required: {question}"));
                }
                BackendEvent::TaskCompleted {
                    task_id,
                    result,
                    findings: _,
                    state,
                } => {
                    if !self.is_current_task(&task_id) {
                        return;
                    }
                    self.apply_backend_state(state);
                    self.finish_task("Completed");
                    let run_name = result
                        .get("run")
                        .and_then(|run| run.get("name"))
                        .and_then(serde_json::Value::as_str)
                        .unwrap_or("");
                    self.status(if run_name.is_empty() {
                        "VulnClaw task completed.".to_owned()
                    } else {
                        format!("VulnClaw task completed. Run: {run_name}")
                    });
                }
                BackendEvent::TaskCancelled { task_id, state } => {
                    if !self.is_current_task(&task_id) {
                        return;
                    }
                    self.apply_backend_state(state);
                    self.finish_task("Cancelled");
                    self.status("VulnClaw task cancelled; backend session remains available.");
                }
                BackendEvent::TaskFailed {
                    task_id,
                    error,
                    state,
                } => {
                    if !self.is_current_task(&task_id) {
                        return;
                    }
                    self.apply_backend_state(state);
                    self.finish_task("Failed");
                    let message = error
                        .get("message")
                        .and_then(serde_json::Value::as_str)
                        .unwrap_or("task failed");
                    self.error(format!("VulnClaw task failed: {message}"));
                }
                BackendEvent::ControlResult {
                    _request_id: _,
                    operation,
                    result,
                    state,
                } => {
                    if let Some(state) = state {
                        self.apply_backend_state(state);
                    }
                    self.status(
                        result
                            .get("message")
                            .and_then(serde_json::Value::as_str)
                            .map_or_else(
                                || format!("Backend control {operation} completed."),
                                str::to_owned,
                            ),
                    );
                }
                BackendEvent::Error {
                    task_id,
                    code,
                    message,
                } => {
                    if task_id.is_some() && task_id == self.active_task_id {
                        self.finish_task("Rejected");
                    }
                    self.error(format!("Backend {code}: {message}"));
                }
                BackendEvent::ShutdownComplete => {
                    self.backend_ready = false;
                    self.backend_commands.clear();
                    self.backend_control_operations.clear();
                }
            },
            AppEvent::BackendDiagnostic(message) => {
                self.push(TranscriptKind::Log, format!("backend: {message}"));
            }
            AppEvent::BackendExited(success) => {
                self.backend = None;
                self.backend_ready = false;
                self.backend_pid = None;
                self.backend_commands.clear();
                self.backend_control_operations.clear();
                self.backend_supports_cancellation = false;
                self.worker_active = false;
                self.worker_started_at = None;
                if let Some(mut receipt) = self.active_receipt.take() {
                    receipt.phase = "Backend disconnected".to_owned();
                    self.last_receipt = Some(receipt);
                }
                self.active_task_id = None;
                if self.running {
                    self.error(if success {
                        "Python backend exited."
                    } else {
                        "Python backend exited with a non-zero status."
                    });
                }
            }
        }
    }

    fn is_current_task(&self, task_id: &str) -> bool {
        self.active_task_id.as_deref() == Some(task_id)
    }

    fn apply_backend_state(&mut self, state: StateSnapshot) {
        self.target = state.target;
        self.phase = state.phase;
        self.findings = state.findings;
        self.worker_active = state.task.active;
        self.active_task_id = state.task.task_id;
        self.task_constraints = state.task_constraints;
        self.last_run = state.last_run;
        self.evidence = state.evidence;
        self.constraint_violations = state.constraint_violations;
        if let Some(receipt) = self.active_receipt.as_mut() {
            receipt.findings = self.findings.len();
        }
    }

    fn upsert_finding(&mut self, finding: Finding) {
        let summary = finding.summary();
        if let Some(existing) = self
            .findings
            .iter_mut()
            .find(|item| !finding.id.is_empty() && item.id == finding.id)
        {
            *existing = finding;
        } else {
            self.findings.push(finding);
            self.push(TranscriptKind::Finding, summary);
        }
        if let Some(receipt) = self.active_receipt.as_mut() {
            receipt.findings = self.findings.len();
            receipt.phase = "Receiving findings".to_owned();
        }
    }

    fn finish_task(&mut self, phase: &str) {
        self.worker_active = false;
        self.worker_started_at = None;
        self.active_task_id = None;
        if let Some(mut receipt) = self.active_receipt.take() {
            receipt.phase = phase.to_owned();
            receipt.findings = self.findings.len();
            self.last_receipt = Some(receipt);
        }
    }

    pub fn save_session(&mut self) {
        let state = SessionState::from_app(self);
        match sessions::save(&state) {
            Ok(path) => self.status(format!("Session saved: {}", path.display())),
            Err(error) => self.error(format!("Could not save session: {error}")),
        }
    }

    pub fn restore_session(&mut self) {
        match sessions::load() {
            Ok(state) => {
                state.apply(self);
                self.status("Session restored.");
            }
            Err(error) => self.error(format!("Could not restore session: {error}")),
        }
    }

    fn set_mode(&mut self, mode: ExecutionMode) {
        self.mode = mode;
        self.status(format!("Execution mode switched to {}.", mode.label()));
    }

    fn request_task(&mut self, command: &str, arguments: &str) {
        if self.mode == ExecutionMode::Plan {
            self.error(
                "Plan mode is read-only. Press Tab to switch to Agent before running a task.",
            );
            return;
        }
        let arguments = arguments.trim();
        if arguments.is_empty() {
            self.error(format!(
                "/{command} requires a target: /{command} <target> [--only-port N] [--only-host H] [--blocked-host H]"
            ));
            return;
        }
        let target = arguments.split_whitespace().next().unwrap_or(arguments);
        self.pending_task = Some(format!("/{command} {arguments}"));
        self.status(format!(
            "/{command} armed for {target}. Press Y to run, or Esc to cancel."
        ));
    }

    fn request_control(&mut self, operation: &str, arguments: serde_json::Value) -> bool {
        if self.worker_active {
            self.error("Administrative settings cannot change while a task is running.");
            return false;
        }
        if !self.backend_ready {
            self.error("The Python backend is not ready.");
            return false;
        }
        if !self
            .backend_control_operations
            .iter()
            .any(|candidate| candidate == operation)
        {
            self.error(format!(
                "The connected backend does not support control operation {operation}."
            ));
            return false;
        }
        let request = ClientRequest::control(self.next_request_id(), operation, arguments);
        let send_result = self
            .backend
            .as_ref()
            .ok_or_else(|| std::io::Error::other("backend disconnected"))
            .and_then(|backend| backend.send(&request));
        if let Err(error) = send_result {
            self.error(format!("Could not send {operation} to Python backend: {error}"));
            return false;
        }
        true
    }

    fn request_scope_control(&mut self, arguments: &str) {
        let arguments = arguments.trim();
        if arguments.is_empty() {
            self.status(
                "Usage: /scope [--only-host H] [--only-port N] [--only-path P] [--blocked-host H] [--blocked-path P] [--allow-actions A,B] [--block-actions A,B], or /scope --clear.",
            );
            return;
        }
        let (operation, payload) = if arguments == "--clear" {
            ("session.scope.reset", serde_json::json!({}))
        } else {
            (
                "session.scope.update",
                serde_json::json!({"command_line": arguments}),
            )
        };
        if self.request_control(operation, payload) {
            self.status("Session scope change requested.");
        }
    }

    fn start_task(&mut self, command_line: String) {
        if self.worker_active {
            self.error("A VulnClaw command is already running.");
            return;
        }
        if !self.backend_ready {
            self.error("The Python backend is not ready.");
            return;
        }
        let task_id = format!("task-{}-{}", std::process::id(), self.request_counter + 1);
        let request = ClientRequest::start_task(
            self.next_request_id(),
            task_id.clone(),
            command_line.clone(),
        );
        self.active_receipt = Some(OperationReceipt {
            command: command_line,
            phase: "Submitting".to_owned(),
            findings: 0,
        });
        self.worker_active = true;
        self.worker_started_at = Some(Instant::now());
        self.active_task_id = Some(task_id);
        let send_result = self
            .backend
            .as_ref()
            .ok_or_else(|| std::io::Error::other("backend disconnected"))
            .and_then(|backend| backend.send(&request));
        if let Err(error) = send_result {
            self.finish_task("Failed to submit");
            self.error(format!("Could not submit task to Python backend: {error}"));
        }
    }

    /// Request cancellation of the active task without terminating the backend.
    pub fn stop_worker(&mut self) {
        let Some(task_id) = self.active_task_id.clone() else {
            return;
        };
        if !self.backend_supports_cancellation {
            self.error("The connected backend does not support task cancellation.");
            return;
        }
        let request = ClientRequest::cancel_task(self.next_request_id(), task_id);
        match self.backend.as_ref().map(|backend| backend.send(&request)) {
            Some(Ok(())) => {
                self.update_receipt("Cancelling");
                self.status("Cancellation requested; waiting for Python checkpoint.");
            }
            Some(Err(error)) => self.error(format!("Could not cancel task: {error}")),
            None => self.error("Could not cancel task: backend disconnected."),
        }
    }

    pub fn shutdown_backend(&mut self) {
        let Some(backend) = self.backend.take() else {
            return;
        };
        let request = ClientRequest::shutdown(self.next_request_id());
        let _ = backend.send(&request);
        backend.wait_or_kill(std::time::Duration::from_secs(2));
        self.backend_ready = false;
        self.backend_commands.clear();
        self.backend_control_operations.clear();
        self.backend_supports_cancellation = false;
    }

    fn push(&mut self, kind: TranscriptKind, text: impl Into<String>) {
        self.transcript.push(TranscriptItem {
            kind,
            text: text.into(),
        });
    }

    fn status(&mut self, text: impl Into<String>) {
        self.push(TranscriptKind::Status, text);
    }

    fn error(&mut self, text: impl Into<String>) {
        self.push(TranscriptKind::Error, text);
    }

    fn update_receipt(&mut self, phase: impl Into<String>) {
        if let Some(receipt) = self.active_receipt.as_mut() {
            receipt.phase = phase.into();
        }
    }

    fn record_command(&mut self, command: &str) {
        if self
            .command_history
            .last()
            .is_none_or(|last| last != command)
        {
            self.command_history.push(command.to_owned());
            if self.command_history.len() > MAX_COMMAND_HISTORY {
                self.command_history.remove(0);
            }
        }
        self.clear_history_navigation();
    }

    fn clear_history_navigation(&mut self) {
        self.history_index = None;
        self.history_draft.clear();
    }

    fn set_input(&mut self, input: String) {
        self.input = input;
        self.input_cursor = self.input.len();
        self.palette_selection = 0;
    }
}

fn truncate_text(text: &str, max_chars: usize) -> String {
    let mut chars = text.chars();
    let preview = chars.by_ref().take(max_chars).collect::<String>();
    if chars.next().is_some() {
        format!("{preview}…")
    } else {
        preview
    }
}

/// Strip a leading transcript/prompt artifact such as `You > ` or `> ` that a
/// user may accidentally paste along with a command copied from the TUI output.
/// The real command always starts with `/`, so we keep everything after the
/// last `>` whose trailing content begins with `/`. Inputs without a `>` are
/// returned unchanged, so valid commands are never corrupted.
fn strip_prompt_prefix(command: &str) -> String {
    let trimmed = command.trim_start();
    if let Some(pos) = trimmed.rfind('>') {
        let rest = trimmed[pos + 1..].trim_start();
        if rest.starts_with('/') {
            return rest.to_owned();
        }
    }
    trimmed.to_owned()
}

fn split_slash_command(command: &str) -> Option<(&str, &str)> {
    let raw = command.strip_prefix('/')?;
    let split_at = raw.find(char::is_whitespace).unwrap_or(raw.len());
    let (verb, remainder) = raw.split_at(split_at);
    (!verb.is_empty()).then_some((verb, remainder.trim_start()))
}

fn normalize_backend_command(command: String) -> Option<String> {
    let normalized = command.trim().trim_start_matches('/');
    if normalized.is_empty()
        || !normalized
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || matches!(character, '-' | '_'))
    {
        return None;
    }
    Some(normalized.to_owned())
}

fn previous_char_boundary(text: &str, index: usize) -> usize {
    text[..index]
        .char_indices()
        .last()
        .map_or(0, |(index, _)| index)
}

fn next_char_boundary(text: &str, index: usize) -> usize {
    text[index..]
        .chars()
        .next()
        .map_or(index, |character| index + character.len_utf8())
}

#[cfg(test)]
mod tests {
    use std::sync::mpsc;

    use super::{strip_prompt_prefix, ActivePane, App, ExecutionMode, PermissionMode};

    #[test]
    fn strip_prompt_prefix_tolerates_pasted_transcript_prefix() {
        assert_eq!(
            strip_prompt_prefix("You  > /run https://example.com"),
            "/run https://example.com"
        );
        // Doubled prefix (composer prompt + pasted prefix) also resolves.
        assert_eq!(
            strip_prompt_prefix("You  > You  > /run https://example.com"),
            "/run https://example.com"
        );
        // Bare "> " prefix from the transcript echo.
        assert_eq!(strip_prompt_prefix("> /shield scan ."), "/shield scan .");
        // Clean command without a prompt artifact is left untouched.
        assert_eq!(strip_prompt_prefix("/help"), "/help");
    }

    #[test]
    fn composer_suggests_and_completes_slash_commands() {
        let (sender, _) = mpsc::channel();
        let mut app = App::new(sender);
        app.backend_commands = vec!["run".into()];
        app.insert_text("/ru");

        assert!(app.palette_visible());
        assert!(app.accept_selected_command());
        assert_eq!(app.input, "/run ");
    }

    #[test]
    fn task_dispatch_uses_backend_advertised_commands() {
        let (sender, _) = mpsc::channel();
        let mut app = App::new(sender);
        app.backend_commands = vec!["recon".into()];
        app.insert_text("/recon https://lab.example");
        app.submit();

        assert_eq!(
            app.pending_task.as_deref(),
            Some("/recon https://lab.example")
        );

        app.dismiss_task();
        app.insert_text("/run https://lab.example");
        app.submit();
        assert!(app.pending_task.is_none());
        assert!(app
            .transcript
            .iter()
            .any(|item| item.text.contains("Unknown command: /run")));
    }

    #[test]
    fn scope_command_routes_to_capability_gated_control() {
        let (sender, _) = mpsc::channel();
        let mut app = App::new(sender);

        app.insert_text("/scope");
        app.submit();
        assert!(app
            .transcript
            .iter()
            .any(|item| item.text.contains("Usage: /scope")));

        app.insert_text("/scope --only-port 443");
        app.submit();
        assert!(app.transcript.iter().any(|item| {
            item.text
                .contains("The Python backend is not ready")
        }));
        assert!(!app
            .transcript
            .iter()
            .any(|item| item.text.contains("Unknown command: /scope")));
    }

    #[test]
    fn ready_event_hydrates_backend_capabilities() {
        let (sender, _) = mpsc::channel();
        let mut app = App::new(sender);
        let event = crate::protocol::parse_backend_line(
            r#"{"protocol_version":1,"type":"ready","request_id":"r1","backend":{"pid":7,"version":"test","protocol_version":1},"capabilities":{"commands":["scan","run"],"control_operations":["example.inspect"],"cancellation":true,"authoritative_state":true},"runtime":{"config_ready":true,"provider":"test","model":"test","mcp_started":0,"skills":[]},"state":{"target":"","phase":"idle","task_constraints":{},"task":{"active":false,"task_id":null},"last_run":null,"findings":[],"evidence":[],"constraint_violations":[]}}"#,
        )
        .unwrap();

        app.apply_event(crate::protocol::AppEvent::Backend(event));

        assert_eq!(app.backend_commands, vec!["run", "scan"]);
        assert_eq!(app.backend_control_operations, vec!["example.inspect"]);
        assert!(app.backend_supports_cancellation);
    }

    #[test]
    fn authoritative_state_replaces_and_clears_every_business_field() {
        let (sender, _) = mpsc::channel();
        let mut app = App::new(sender);
        app.target = "stale.test".into();
        app.phase = "stale".into();
        app.worker_active = true;
        app.active_task_id = Some("stale-task".into());
        app.task_constraints = serde_json::json!({"allowed_hosts": ["stale.test"]});
        app.last_run = Some(serde_json::json!({"status": "stale"}));
        app.evidence = vec![serde_json::json!({"path": "stale"})];
        app.constraint_violations = vec!["stale violation".into()];

        app.apply_event(crate::protocol::AppEvent::Backend(
            crate::protocol::BackendEvent::State {
                state: crate::protocol::StateSnapshot {
                    target: String::new(),
                    phase: String::new(),
                    task_constraints: serde_json::json!({"allowed_ports": [443]}),
                    findings: Vec::new(),
                    task: crate::protocol::BackendTaskState {
                        active: false,
                        task_id: None,
                    },
                    last_run: Some(serde_json::json!({"status": "completed"})),
                    evidence: vec![serde_json::json!({"path": "fresh"})],
                    constraint_violations: vec!["fresh violation".into()],
                },
            },
        ));

        assert!(app.target.is_empty());
        assert!(app.phase.is_empty());
        assert!(!app.worker_active);
        assert!(app.active_task_id.is_none());
        assert_eq!(app.task_constraints["allowed_ports"][0], 443);
        assert_eq!(app.last_run.as_ref().unwrap()["status"], "completed");
        assert_eq!(app.evidence[0]["path"], "fresh");
        assert_eq!(app.constraint_violations, vec!["fresh violation"]);
    }

    #[test]
    fn task_event_summaries_never_override_authoritative_state() {
        let (sender, _) = mpsc::channel();
        let mut app = App::new(sender);
        app.worker_active = true;
        app.active_task_id = Some("t1".into());

        app.apply_event(crate::protocol::AppEvent::Backend(
            crate::protocol::BackendEvent::TaskStarted {
                task_id: "t1".into(),
                command: "run".into(),
                normalized_command: "/run authoritative.test".into(),
                _target: "summary.test".into(),
                _resume: true,
                _constraints: serde_json::json!({"allowed_hosts": ["summary.test"]}),
                state: crate::protocol::StateSnapshot {
                    target: "authoritative.test".into(),
                    phase: "recon".into(),
                    task_constraints: serde_json::json!({
                        "allowed_hosts": ["authoritative.test"]
                    }),
                    findings: Vec::new(),
                    task: crate::protocol::BackendTaskState {
                        active: true,
                        task_id: Some("t1".into()),
                    },
                    last_run: None,
                    evidence: Vec::new(),
                    constraint_violations: Vec::new(),
                },
            },
        ));

        assert_eq!(app.target, "authoritative.test");
        assert_eq!(
            app.task_constraints["allowed_hosts"][0],
            "authoritative.test"
        );

        let authoritative_finding = crate::protocol::Finding {
            id: "state-finding".into(),
            severity: "high".into(),
            title: "Authoritative".into(),
            target: "authoritative.test".into(),
            ..Default::default()
        };
        let summary_finding = crate::protocol::Finding {
            id: "summary-finding".into(),
            ..Default::default()
        };
        app.apply_event(crate::protocol::AppEvent::Backend(
            crate::protocol::BackendEvent::TaskCompleted {
                task_id: "t1".into(),
                result: serde_json::json!({}),
                findings: vec![summary_finding],
                state: crate::protocol::StateSnapshot {
                    target: "authoritative.test".into(),
                    phase: "reporting".into(),
                    task_constraints: app.task_constraints.clone(),
                    findings: vec![authoritative_finding],
                    task: crate::protocol::BackendTaskState {
                        active: false,
                        task_id: None,
                    },
                    last_run: Some(serde_json::json!({"status": "completed"})),
                    evidence: Vec::new(),
                    constraint_violations: Vec::new(),
                },
            },
        ));

        assert_eq!(app.findings.len(), 1);
        assert_eq!(app.findings[0].id, "state-finding");
    }

    #[test]
    fn mode_and_permission_cycles_are_independent() {
        let (sender, _) = mpsc::channel();
        let mut app = App::new(sender);
        app.cycle_mode();
        app.cycle_permission();

        // Default posture is Agent; one Tab cycles to the read-only Plan.
        assert_eq!(app.mode, ExecutionMode::Plan);
        assert_eq!(app.permission, PermissionMode::FullAccess);
        assert!(app.pending_task.is_none());
    }

    #[test]
    fn composer_supports_cursor_editing_and_history() {
        let (sender, _) = mpsc::channel();
        let mut app = App::new(sender);
        app.insert_text("/hep");
        app.move_input_cursor(false);
        app.insert_text("l");
        app.submit();
        app.insert_text("draft");
        app.recall_history(true);

        assert_eq!(app.input, "/help");
        app.recall_history(false);
        assert_eq!(app.input, "draft");
    }

    #[test]
    fn plan_mode_blocks_task_before_a_confirmation_can_be_armed() {
        let (sender, _) = mpsc::channel();
        let mut app = App::new(sender);
        app.mode = ExecutionMode::Plan;
        app.backend_commands = vec!["run".into()];
        app.insert_text("/run https://lab.example");
        app.submit();

        assert!(app.pending_task.is_none());
        assert!(!app.worker_active);
        assert!(app
            .transcript
            .iter()
            .any(|item| item.text.contains("Plan mode is read-only")));
    }

    #[test]
    fn agent_mode_arms_a_task_and_waits_for_confirmation() {
        let (sender, _) = mpsc::channel();
        let mut app = App::new(sender);
        app.mode = ExecutionMode::Agent;
        app.permission = PermissionMode::FullAccess;

        app.request_task("run", "https://lab.example");

        assert!(app.pending_task.is_some());
        assert!(!app.worker_active);
    }

    #[test]
    fn paste_in_main_input_still_works() {
        let (sender, _) = mpsc::channel();
        let mut app = App::new(sender);
        app.insert_text("hello world");
        assert_eq!(app.input, "hello world");
    }

    #[test]
    fn task_requires_a_target() {
        let (sender, _) = mpsc::channel();
        let mut app = App::new(sender);
        app.mode = ExecutionMode::Agent;

        app.request_task("run", "");

        assert!(app.pending_task.is_none());
        assert!(app
            .transcript
            .iter()
            .any(|item| item.text.contains("requires a target")));
    }

    #[test]
    fn streamed_events_update_and_finalize_the_work_receipt() {
        let (sender, _) = mpsc::channel();
        let mut app = App::new(sender);
        app.active_receipt = Some(super::OperationReceipt {
            command: "/run https://lab.example".into(),
            phase: "Starting".into(),
            findings: 0,
        });
        app.worker_active = true;
        app.active_task_id = Some("t1".into());
        let finding = crate::protocol::Finding {
            severity: "high".into(),
            title: "Test finding".into(),
            ..Default::default()
        };
        app.apply_event(crate::protocol::AppEvent::Backend(
            crate::protocol::BackendEvent::Finding {
                task_id: "t1".into(),
                finding: finding.clone(),
            },
        ));
        app.apply_event(crate::protocol::AppEvent::Backend(
            crate::protocol::BackendEvent::TaskCompleted {
                task_id: "t1".into(),
                result: serde_json::json!({}),
                findings: Vec::new(),
                state: crate::protocol::StateSnapshot {
                    findings: vec![finding],
                    task: crate::protocol::BackendTaskState {
                        active: false,
                        task_id: None,
                    },
                    ..Default::default()
                },
            },
        ));

        assert!(app.active_receipt.is_none());
        assert_eq!(app.last_receipt.as_ref().unwrap().findings, 1);
        assert_eq!(app.last_receipt.as_ref().unwrap().phase, "Completed");
    }

    #[test]
    fn active_pane_rect_partitions_the_workbench_without_overlap() {
        let (sender, _) = mpsc::channel();
        let mut app = App::new(sender);
        app.terminal_size = ratatui::layout::Rect::new(0, 0, 120, 28);

        app.active_pane = ActivePane::Workspace;
        let workspace = app.active_pane_rect(app.terminal_size);
        app.active_pane = ActivePane::Transcript;
        let transcript = app.active_pane_rect(app.terminal_size);
        app.active_pane = ActivePane::Findings;
        let findings = app.active_pane_rect(app.terminal_size);

        // The three panes are laid out left-to-right and must not overlap.
        assert_eq!(workspace.x, 0);
        assert!(workspace.right() <= transcript.x, "workspace right of transcript start");
        assert!(transcript.right() <= findings.x, "transcript right of findings start");
        assert!(findings.right() <= 120);
        // None of them spans the full screen — each is an independent region.
        assert!(workspace.width < 120);
        assert!(transcript.width < 120);
        assert!(findings.width < 120);
    }

    #[test]
    fn copy_active_pane_renders_only_the_focused_region() {
        let (sender, _) = mpsc::channel();
        let mut app = App::new(sender);
        app.terminal_size = ratatui::layout::Rect::new(0, 0, 120, 28);
        app.active_pane = ActivePane::Transcript;

        // Drive the same offscreen render path the real copy uses, then pull the
        // transcript region and confirm it contains transcript content but not
        // the findings title (proving the copy is pane-scoped, not whole-screen).
        let rect = app.active_pane_rect(app.terminal_size);
        let backend = ratatui::backend::TestBackend::new(120, 28);
        let mut term = ratatui::Terminal::new(backend).unwrap();
        term.draw(|f| crate::ui::draw(f, &app)).unwrap();
        let text = super::extract_rect_text(term.backend().buffer(), rect);

        assert!(text.contains("Session transcript"));
        assert!(
            !text.contains("Findings inspector"),
            "transcript copy must not bleed into the findings pane"
        );
    }

    #[test]
    fn transcript_autoscroll_pins_to_bottom_and_tracks_growth() {
        let (sender, _) = mpsc::channel();
        let mut app = App::new(sender);
        // 120x30 -> transcript panel height 26, visible inner rows = 24.
        app.terminal_size = ratatui::layout::Rect::new(0, 0, 120, 30);
        app.active_pane = ActivePane::Transcript;

        // Short lines never wrap at the ~50-col inner width, so the pin point is
        // purely a function of content length.
        for i in 0..5 {
            app.push(crate::app::TranscriptKind::Log, format!("line {i}"));
        }
        app.autoscroll_transcript();
        let small = app.transcript_scroll as usize;
        assert!(app.transcript_follow);
        assert_eq!(small, app.transcript_max_scroll());

        for i in 5..45 {
            app.push(crate::app::TranscriptKind::Log, format!("line {i}"));
        }
        app.autoscroll_transcript();
        let large = app.transcript_scroll as usize;
        assert!(app.transcript_follow);
        assert_eq!(large, app.transcript_max_scroll());
        // More content => larger bottom offset => the view followed the growth.
        assert!(large > small);
    }

    #[test]
    fn transcript_short_content_has_zero_scroll() {
        let (sender, _) = mpsc::channel();
        let mut app = App::new(sender);
        app.terminal_size = ratatui::layout::Rect::new(0, 0, 120, 30);
        app.active_pane = ActivePane::Transcript;
        app.autoscroll_transcript();
        // Only the two welcome lines — they fit, so no scrolling is needed.
        assert_eq!(app.transcript_max_scroll(), 0);
        assert_eq!(app.transcript_scroll, 0);
        assert!(app.transcript_follow);
    }

    #[test]
    fn scrolling_up_pauses_follow_and_bottom_resumes_it() {
        let (sender, _) = mpsc::channel();
        let mut app = App::new(sender);
        app.terminal_size = ratatui::layout::Rect::new(0, 0, 120, 30);
        app.active_pane = ActivePane::Transcript;
        for i in 0..45 {
            app.push(crate::app::TranscriptKind::Log, format!("line {i}"));
        }
        app.autoscroll_transcript();
        let max = app.transcript_max_scroll() as u16;
        assert!(max > 0);
        assert_eq!(app.transcript_scroll, max);
        assert!(app.transcript_follow);

        // Scroll up once to read history: follow must switch off.
        app.scroll_active_pane(false);
        assert!(!app.transcript_follow);
        assert_eq!(app.transcript_scroll, max - 1);

        // Scroll back down to the bottom: follow must switch back on.
        for _ in 0..(max as usize + 2) {
            app.scroll_active_pane(true);
        }
        assert!(app.transcript_follow);
        assert_eq!(app.transcript_scroll, max);
    }
}
