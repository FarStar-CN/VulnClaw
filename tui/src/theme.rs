use std::time::{Instant, SystemTime, UNIX_EPOCH};

use ratatui::style::{Color, Style};

use crate::app::TranscriptKind;

/// Charcoal surfaces with copper outlines and orange interaction accents.
pub const BG: Color = Color::Rgb(28, 28, 28); // #1C1C1C
pub const CHROME: Color = Color::Rgb(32, 32, 32); // #202020
pub const PANEL: Color = Color::Rgb(32, 32, 32); // #202020
pub const PLATE: Color = Color::Rgb(43, 41, 39); // #2B2927
pub const DOCK_PREVIEW: Color = Color::Rgb(72, 72, 72); // #484848
pub const BORDER: Color = Color::Rgb(156, 107, 67); // #9C6B43

pub const TEXT_BODY: Color = Color::Rgb(230, 233, 239); // #E6E9EF
pub const TEXT_SOFT: Color = Color::Rgb(194, 200, 210); // #C2C8D2
pub const TEXT_MUTED: Color = Color::Rgb(160, 168, 181); // #A0A8B5
pub const TEXT_HINT: Color = Color::Rgb(124, 133, 147); // #7C8593

pub const ACTION: Color = Color::Rgb(255, 158, 85); // #FF9E55
pub const SEAFOAM: Color = Color::Rgb(255, 176, 92); // #FFB05C warm amber accent secondary
pub const GOLD: Color = Color::Rgb(255, 199, 89); // #FFC759 signal gold
pub const ROSE: Color = Color::Rgb(255, 82, 82); // #FF5252 danger red
pub const CORAL: Color = Color::Rgb(255, 108, 66); // #FF6C42 warning coral
pub const SUCCESS: Color = Color::Rgb(120, 211, 130); // #78D382 diff added / success
pub const MODE_AGENT: Color = Color::Rgb(255, 158, 76); // #FF9E4C

/// Neutral transcript text with semantic accents for errors and findings.
pub fn transcript_style(kind: &TranscriptKind) -> Style {
    match kind {
        TranscriptKind::User => Style::default().fg(TEXT_BODY),
        TranscriptKind::System => Style::default().fg(TEXT_SOFT),
        TranscriptKind::Status | TranscriptKind::Log => Style::default().fg(TEXT_MUTED),
        TranscriptKind::Reasoning => Style::default().fg(TEXT_HINT),
        TranscriptKind::Error => Style::default()
            .fg(ROSE)
            .add_modifier(ratatui::style::Modifier::BOLD),
        TranscriptKind::Finding => Style::default().fg(GOLD),
    }
}

/// Severity colors reuse the danger/warning/gold/amber grammar.
pub fn severity_style(severity: &str) -> Style {
    match severity.to_ascii_lowercase().as_str() {
        "critical" => Style::default()
            .fg(ROSE)
            .add_modifier(ratatui::style::Modifier::BOLD),
        "high" => Style::default().fg(CORAL),
        "medium" => Style::default().fg(GOLD),
        "low" => Style::default().fg(SEAFOAM),
        _ => Style::default().fg(TEXT_HINT),
    }
}

/// Mode badge color — mirrors the mode-specific accent tokens.
pub fn mode_color(label: &str) -> Color {
    match label {
        "Agent" => MODE_AGENT,
        "YOLO" => ROSE,
        _ => TEXT_HINT,
    }
}

/// Permission posture color.
pub fn permission_color(label: &str) -> Color {
    match label {
        "Ask" => CORAL,
        "Auto-review" => ACTION,
        "Full access" => SEAFOAM,
        _ => TEXT_HINT,
    }
}

/// Live spinner — keeps a rotating glyph while a worker runs so the surface
/// feels alive. Driven by wall-clock time so it actually advances across
/// redraws (an `Instant` captured per call would never move).
const SPINNER: &[char] = &['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

pub fn spinner_frame(active: bool) -> char {
    spinner_frame_at(active, now_ms())
}

/// Deterministic spinner frame for render tests and alternate clocks.
pub fn spinner_frame_at(active: bool, timestamp_ms: u128) -> char {
    if !active {
        return ' ';
    }
    let step = timestamp_ms / 90;
    SPINNER[(step as usize) % SPINNER.len()]
}

/// Wall-clock millisecond tick shared by every animation below, so all live
/// elements move on one stable time source.
pub fn now_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0)
}

/// Activity equalizer — a short row of bars that bounce while a command runs,
/// mimicking a live "processing" signal. Pure function of the wall-clock tick.
const EQ: &[char] = &['▁', '▂', '▃', '▄', '▅', '▆', '▇', '█'];

pub fn equalizer_frame() -> String {
    equalizer_frame_at(now_ms())
}

/// Deterministic equalizer frame for render tests and alternate clocks.
pub fn equalizer_frame_at(timestamp_ms: u128) -> String {
    let bars = 6;
    let mut out = String::with_capacity(bars);
    for i in 0..bars {
        let phase = (timestamp_ms as f64 / 130.0) + i as f64 * 0.9;
        let v = (phase.sin() * 0.5 + 0.5).clamp(0.0, 1.0);
        let idx = (v * (EQ.len() as f64 - 1.0)).round() as usize;
        out.push(EQ[idx]);
    }
    out
}

/// Elapsed run time as `mm:ss`, derived monotonically from the worker start
/// instant. Returns an empty string when no command is running.
pub fn elapsed_label(started: Option<Instant>) -> String {
    match started {
        Some(s) => {
            let secs = s.elapsed().as_secs();
            format!("⏱ {:02}:{:02}", secs / 60, secs % 60)
        }
        None => String::new(),
    }
}

/// Blink phase — true for the first half of each ~900 ms window. Drives the
/// live stream dot and finding-count pulse so indicators breathe rather than
/// strobe.
pub fn blink_on() -> bool {
    blink_on_at(now_ms())
}

/// Deterministic blink phase for render tests and alternate clocks.
pub fn blink_on_at(timestamp_ms: u128) -> bool {
    (timestamp_ms / 450) & 1 == 0
}

/// Breathing border color for the focused content pane while a worker runs.
/// Oscillates gently between the two accent tokens so the panel feels alive
/// without a hard strobe. Falls back to the static `BORDER` when idle.
pub fn pulse_border(active: bool) -> Color {
    pulse_border_at(active, now_ms())
}

/// Deterministic border pulse for render tests and alternate clocks.
pub fn pulse_border_at(active: bool, timestamp_ms: u128) -> Color {
    if !active {
        return BORDER;
    }
    if (timestamp_ms / 800) & 1 == 0 {
        ACTION
    } else {
        SEAFOAM
    }
}
