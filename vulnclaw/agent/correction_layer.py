"""Lightweight correction signals around model-selected tool calls.

This is deliberately not a planner.  It observes tool lifecycle events and
stores compact facts, health, timing and loop hints in AgentState so the model
can self-correct on the next turn without being forced into a fixed workflow.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from vulnclaw.agent.agent_state import EvidenceRecord, extract_flags, one_line

if TYPE_CHECKING:
    from vulnclaw.agent.agent_context import AgentContext

_URL_RE = re.compile(r"https?://[^\s'\"<>）)]+", re.IGNORECASE)
_PATH_RE = re.compile(r"(?<![\w-])/[A-Za-z0-9._~!$&'()*+,;=:@%/-]{2,120}")
_ATTR_RE = re.compile(r"""(?i)\b([a-z_:][-a-z0-9_:.]*)\s*=\s*["']?([^"'\s>]+)""")
_FORM_RE = re.compile(r"(?is)<form\b[^>]*>")
_INPUT_RE = re.compile(r"(?is)<input\b[^>]*>")
_LINK_RE = re.compile(
    r"""(?is)\b(?:href|src)\s*=\s*["']([^"']{1,180}(?:\.php\b|\.js\b|/api/?|api/)[^"']*)["']"""
)
_JS_ENDPOINT_RE = re.compile(
    r"""(?is)(?:url\s*[:=]\s*|fetch\s*\(|axios\.\w+\s*\()\s*["']([^"']{1,180}(?:\.php\b|/api/?|api/|\?)[^"']*)["']"""
)
_SQL_SNIPPET_RE = re.compile(
    r"""(?is)(?:\$sql\s*=\s*)?["']?\s*(select\s+.{0,260}?\s+from\s+.{0,260}?(?:where|limit)\s+.{0,420}?)(?:["';\n]|$)"""
)
_IGNORED_URL_PARTS = (
    "urllib3.readthedocs.io",
    "pythonhosted.org/urllib3",
    "requests.readthedocs.io",
)
_FAILURE_MARKERS = (
    "[!]",
    "failed locally",
    "traceback",
    "exception",
    "timed out",
    "timeout",
    "connection refused",
    "cancellederror",
    "constraint_violation",
    "role_tool_violation",
)
_PROGRESS_MARKERS = (
    "flag",
    "ctf{",
    "status:",
    "set-cookie",
    "location:",
    "<form",
    "<input",
    "href=",
    "select",
    "union",
    "sql",
    "token",
    "admin",
    "endpoint",
)


@dataclass
class CorrectionSignal:
    """Result of observing one tool call."""

    tool: str
    ok: bool
    duration_ms: int = 0
    evidence_id: str = ""
    progress: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)

    def model_hint(self) -> str:
        parts = [*self.progress, *self.hints]
        return "; ".join(one_line(item, 220) for item in parts if item)


def _agent_state(agent: AgentContext) -> Any:
    state = getattr(getattr(agent, "context", None), "state", None)
    return getattr(state, "agent_state", None)


def before_tool_call(agent: AgentContext, tool: str, arguments: dict[str, Any]) -> str:
    """Return a soft pre-tool hint. It never blocks execution."""

    state = _agent_state(agent)
    if state is None:
        return ""

    hints: list[str] = []
    repeated = state.count_recent_tool_call(tool=tool, arguments=arguments, window=8)
    if repeated >= 2:
        hints.append(
            f"Exact tool call {tool} has already appeared {repeated} times recently; "
            "change arguments or inspect saved evidence if the result repeats."
        )

    if tool == "evidence_view":
        redundant_reason = state.evidence_view_redundancy_reason(arguments)
        if redundant_reason:
            hints.append(redundant_reason)

    health = state.tool_health.get(tool)
    if health is not None and health.status == "degraded":
        hints.append(
            f"{tool} is currently degraded after {health.consecutive_failures} failure(s); "
            "prefer a fallback tool if this call fails again."
        )

    return " ".join(hints)


def after_tool_call(
    agent: AgentContext,
    *,
    tool: str,
    arguments: dict[str, Any],
    raw_output: Any,
    duration_ms: int,
    evidence: EvidenceRecord | None,
    error: BaseException | None = None,
) -> CorrectionSignal:
    """Persist post-tool health/progress facts and return model-visible hints."""

    raw = str(raw_output or "")
    lower = raw.lower()
    ok = error is None and not any(marker in lower for marker in _FAILURE_MARKERS)
    evidence_id = evidence.id if evidence is not None else ""
    signal = CorrectionSignal(tool=tool, ok=ok, duration_ms=duration_ms, evidence_id=evidence_id)

    state = _agent_state(agent)
    if state is None:
        return signal

    error_text = str(error or "").strip() or _first_failure_line(raw)
    state.record_tool_health(
        tool=tool,
        ok=ok,
        duration_ms=duration_ms,
        error=error_text,
    )

    if duration_ms >= 15_000:
        hint = f"{tool} took {duration_ms}ms; avoid repeating slow probes unless the result is necessary."
        signal.hints.append(hint)
        state.add_correction_hint(hint)

    if not ok:
        hint = f"{tool} failed or returned an error; use a fallback path rather than retrying unchanged."
        signal.hints.append(hint)
        state.add_correction_hint(hint)
        return signal

    progress = _extract_progress(raw)
    for detail in progress:
        state.record_progress_signal(
            kind="tool_observation",
            detail=detail,
            tool=tool,
            evidence_id=evidence_id,
        )
        signal.progress.append(detail)

    for fact in _extract_pinned_facts(raw):
        state.pin_fact(fact, evidence_id=evidence_id)

    for hint in _extract_semantic_hints(raw, state):
        signal.hints.append(hint)
        state.add_correction_hint(hint)

    if not progress and state.count_recent_tool_call(tool=tool, arguments=arguments, window=6) >= 2:
        hint = f"{tool} produced no obvious new signal for repeated arguments; pivot or inspect raw evidence."
        signal.hints.append(hint)
        state.add_correction_hint(hint)

    return signal


def after_tool_batch(agent: AgentContext, signals: list[CorrectionSignal]) -> str:
    """Store a short batch-level correction hint when useful."""

    if not signals:
        return ""
    state = _agent_state(agent)
    failed = [item for item in signals if not item.ok]
    slow = [item for item in signals if item.duration_ms >= 15_000]

    hint = ""
    if len(failed) == len(signals):
        hint = "All tool calls in this batch failed; switch tool family or ask for missing prerequisites."
    elif slow:
        names = ", ".join(item.tool for item in slow[:3])
        hint = f"Slow tool(s) observed: {names}; batch similar probes or reduce scope."

    if hint and state is not None:
        state.add_correction_hint(hint)
    return hint


def content_digest(text: str) -> str:
    """Stable digest used by tests and future trace analysis."""

    return hashlib.sha256(str(text or "").encode("utf-8", errors="replace")).hexdigest()[:16]


def _first_failure_line(raw: str) -> str:
    for line in str(raw or "").splitlines():
        stripped = line.strip()
        if stripped and any(marker in stripped.lower() for marker in _FAILURE_MARKERS):
            return one_line(stripped, 220)
    return ""


def _extract_progress(raw: str) -> list[str]:
    text = str(raw or "")
    lower = text.lower()
    progress: list[str] = []

    flags = extract_flags(text)
    for flag in flags[:3]:
        progress.append(f"flag-like token observed: {flag}")

    status = re.search(r"(?:Status|HTTP/\d(?:\.\d)?)\s*:?\s*(\d{3})", text, re.IGNORECASE)
    if status:
        progress.append(f"HTTP status observed: {status.group(1)}")

    if any(marker in lower for marker in ("<form", "<input", "name=")):
        progress.append("HTML form/input surface observed")

    if _extract_sql_facts(text):
        progress.append("server-side SQL/source snippet observed")

    if _extract_js_endpoint_facts(text):
        progress.append("JS/API endpoint construction observed")

    urls = _filtered_urls(text)[:5]
    if urls:
        progress.append("URL(s) observed: " + ", ".join(one_line(url, 80) for url in urls[:3]))

    endpoints = [
        item
        for item in dict.fromkeys(_PATH_RE.findall(text))
        if not item.startswith(("//", "/usr/", "/lib/", "/site-packages/"))
    ][:5]
    if endpoints:
        progress.append("path(s) observed: " + ", ".join(endpoints[:4]))

    if not progress and any(marker in lower for marker in _PROGRESS_MARKERS):
        progress.append("security-relevant marker observed in tool output")

    return progress[:6]


def _extract_pinned_facts(raw: str) -> list[str]:
    text = str(raw or "")
    facts: list[str] = []
    for flag in extract_flags(text)[:3]:
        facts.append(f"Observed flag-like token: {flag}")

    facts.extend(_extract_sql_facts(text))
    facts.extend(_extract_form_facts(text))
    facts.extend(_extract_js_endpoint_facts(text))
    facts.extend(_extract_link_facts(text))

    for url in _filtered_urls(text)[:5]:
        facts.append(f"Observed URL: {one_line(url, 160)}")
    return list(dict.fromkeys(facts))[:14]


def _filtered_urls(text: str) -> list[str]:
    """Return target/user URLs while ignoring dependency-warning documentation links."""

    urls: list[str] = []
    for url in dict.fromkeys(_URL_RE.findall(text or "")):
        lowered = url.lower()
        if any(part in lowered for part in _IGNORED_URL_PARTS):
            continue
        urls.append(url)
    return urls


def _extract_attrs(tag: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for key, value in _ATTR_RE.findall(tag or ""):
        attrs[key.lower()] = value
    return attrs


def _extract_sql_facts(text: str) -> list[str]:
    facts: list[str] = []
    for line in str(text or "").splitlines():
        lower = line.lower()
        if "select" not in lower or " from " not in lower:
            continue
        if not any(marker in lower for marker in (" where ", "$_get", " limit ", " union ")):
            continue
        snippet = one_line(line, 300)
        if snippet:
            facts.append(f"Source SQL: {snippet}")
        if len(facts) >= 4:
            return list(dict.fromkeys(facts))
    if facts:
        return list(dict.fromkeys(facts))

    for match in _SQL_SNIPPET_RE.finditer(text or ""):
        snippet = one_line(match.group(1), 300)
        if snippet:
            facts.append(f"Source SQL: {snippet}")
        if len(facts) >= 4:
            break
    return list(dict.fromkeys(facts))


def _extract_form_facts(text: str) -> list[str]:
    facts: list[str] = []
    for tag in _FORM_RE.findall(text or "")[:4]:
        attrs = _extract_attrs(tag)
        parts: list[str] = []
        if attrs.get("method"):
            parts.append(f"method={attrs['method']}")
        if attrs.get("action"):
            parts.append(f"action={attrs['action']}")
        if parts:
            facts.append("HTML form: " + " ".join(parts))

    for tag in _INPUT_RE.findall(text or "")[:8]:
        attrs = _extract_attrs(tag)
        name = attrs.get("name") or attrs.get("id")
        if not name:
            continue
        detail = f"name={name}" if attrs.get("name") else f"id={name}"
        if attrs.get("type"):
            detail += f" type={attrs['type']}"
        facts.append(f"HTML input: {detail}")
    return facts[:8]


def _extract_js_endpoint_facts(text: str) -> list[str]:
    facts: list[str] = []
    for match in _JS_ENDPOINT_RE.finditer(text or ""):
        endpoint = one_line(match.group(1), 180)
        if endpoint:
            facts.append(f"JS/API endpoint: {endpoint}")
        if len(facts) >= 6:
            break
    return facts


def _extract_link_facts(text: str) -> list[str]:
    facts: list[str] = []
    for match in _LINK_RE.finditer(text or ""):
        endpoint = one_line(match.group(1), 180)
        if endpoint:
            facts.append(f"Linked endpoint: {endpoint}")
        if len(facts) >= 8:
            break
    return facts


def _extract_semantic_hints(raw: str, state: Any) -> list[str]:
    """Generate small evidence-backed course corrections without planning tools."""

    text = str(raw or "")
    lower = text.lower()
    hints: list[str] = []
    sql_facts = _extract_sql_facts(text)
    pinned_sql = [
        item.text
        for item in getattr(state, "pinned_facts", [])
        if "source sql:" in getattr(item, "text", "").lower()
    ]
    has_sql_source = bool(sql_facts or pinned_sql)

    if sql_facts:
        hints.append(
            "SQL source is visible; derive payloads from the exact server-side expression before "
            "generic SQLi lists. If the app appends a trailing quote, test no-comment closure "
            "variants such as 0'||username='flag as well as comment variants."
        )

    used_comment_terminator = any(marker in lower for marker in ("%23", "%2d%2d", "--+", "#"))
    no_flag = not extract_flags(text)
    if has_sql_source and used_comment_terminator and no_flag:
        hints.append(
            "Pinned SQL source plus failed comment-terminated probes: try removing the comment "
            "terminator and rely on the application-supplied trailing quote; also test URL-encoded "
            "operator variants like %7C%7C."
        )

    return list(dict.fromkeys(hints))[:3]
