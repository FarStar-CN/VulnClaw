"""Constraint policy helpers for task, phase, and tool enforcement."""

from __future__ import annotations

from vulnclaw.agent.context import PentestPhase, TaskConstraints

PHASE_TO_ACTION: dict[PentestPhase, str] = {
    PentestPhase.RECON: "recon",
    PentestPhase.VULN_DISCOVERY: "scan",
    PentestPhase.EXPLOITATION: "exploit",
    PentestPhase.POST_EXPLOITATION: "post_exploitation",
    PentestPhase.REPORTING: "report",
}


def normalize_action_name(action: str) -> str:
    """Normalize action aliases into a shared policy namespace."""
    lowered = (action or "").strip().lower()
    aliases = {
        "run": "run",
        "recon": "recon",
        "scan": "scan",
        "exploit": "exploit",
        "post": "post_exploitation",
        "post_exploitation": "post_exploitation",
        "report": "report",
        "reporting": "report",
        "persistent": "persistent",
    }
    return aliases.get(lowered, lowered)


def validate_action_constraints(action: str, constraints: TaskConstraints) -> str | None:
    """Return a constraint violation message when a task action is out of scope."""
    if constraints.is_empty():
        return None

    normalized = normalize_action_name(action)
    allowed = [normalize_action_name(item) for item in constraints.allowed_actions]
    blocked = [normalize_action_name(item) for item in constraints.blocked_actions]

    # Composite commands (run, persistent) include all phases;
    # fine-grained enforcement happens inside the loop via phase/tool checks.
    if normalized in ("run", "persistent"):
        if normalized in blocked:
            return f"constraint_violation: command '{normalized}' is blocked by task constraints"
        return None

    if allowed and normalized not in allowed:
        return f"constraint_violation: command '{normalized}' is outside allowed actions [{', '.join(allowed)}]"

    if normalized in blocked:
        return f"constraint_violation: command '{normalized}' is blocked by task constraints"

    return None


def validate_phase_transition(
    next_phase: PentestPhase,
    constraints: TaskConstraints,
) -> str | None:
    """Return a constraint violation message when a phase transition is out of scope."""
    action = PHASE_TO_ACTION.get(next_phase)
    if action is None:
        return None
    violation = validate_action_constraints(action, constraints)
    if violation is None:
        return None
    return f"{violation} (phase transition to {next_phase.value})"


def infer_tool_action(tool_name: str, args: dict[str, object]) -> str:
    """Infer the effective action class of a tool invocation."""
    normalized_tool = (tool_name or "").strip().lower()

    if normalized_tool == "nmap_scan":
        return "recon"

    if normalized_tool == "fetch":
        url = str(args.get("url", "") or "").lower()
        method = str(args.get("method", "GET") or "GET").upper()
        body = str(args.get("body", "") or "").lower()
        suspicious_markers = [
            "union select",
            " or 1=1",
            "../",
            "<script",
            "cmd=",
            "php://",
            "extractvalue(",
            "whoami",
        ]
        if method != "GET" or any(marker in url or marker in body for marker in suspicious_markers):
            return "exploit"
        return "recon"

    if normalized_tool == "python_execute":
        code = str(args.get("code", "") or "").lower()
        if any(
            marker in code
            for marker in [
                "requests.",
                "httpx.",
                "urllib",
                "socket.",
                "whoami",
                "extractvalue(",
                "../",
                "<script",
            ]
        ):
            return "exploit"
        return "scan"

    if normalized_tool in {"crypto_decode", "load_skill_reference", "brute_force_login"}:
        return "scan"

    return "scan"


def validate_tool_action(
    tool_name: str, args: dict[str, object], constraints: TaskConstraints
) -> str | None:
    """Return a constraint violation when a tool invocation implies a blocked action."""
    inferred = infer_tool_action(tool_name, args)
    violation = validate_action_constraints(inferred, constraints)
    if violation is None:
        return None
    return f"{violation} (tool '{tool_name}' inferred action '{inferred}')"
