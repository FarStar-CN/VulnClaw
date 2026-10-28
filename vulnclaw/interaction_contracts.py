"""Machine-readable session interactions and their shared argument validation."""

from __future__ import annotations

import sys
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from vulnclaw.session_protocol import CLIENT_MESSAGE_TYPES, PROTOCOL_VERSION, request_schema

ApprovalDecision = Literal["approve", "deny"]


class ApprovalResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request_hash: str = Field(min_length=1, description="Copy request_hash from approval_required.")
    decision: ApprovalDecision


def describe_protocol(name: str | None = None) -> dict:
    """Describe the installed backend, without constructing its runtime."""
    if name is None:
        return {
            "kind": "protocol",
            "protocol_version": PROTOCOL_VERSION,
            "transport": {
                "command_argv": [sys.executable, "-u", "-m", "vulnclaw.session_backend"],
                "input": "stdin JSONL",
                "output": "stdout JSONL",
                "diagnostics": "stderr",
            },
            "initialization": {"send": "initialize", "wait_for": "ready"},
            "requests": sorted(CLIENT_MESSAGE_TYPES),
            "interactions": ["approval_required", "waiting_for_input"],
            "describe_argv": ["vulnclaw", "describe", "--protocol", "<name>"],
            "lifecycle": {
                "capabilities": "Check ready.backend version and ready.capabilities before use.",
                "completion": "Task/turn completion ends one phase; also inspect task.waiting_for_input.",
                "shutdown": "Send shutdown, close stdin, read shutdown_complete and wait for process exit.",
                "scope": "Session changes last for this process; saved config changes persist.",
            },
        }
    if name == "approval_required":
        return {
            "kind": "interaction_contract",
            "name": name,
            "trigger": {"event": name},
            "response": {
                "transport": "session_stdin",
                "message_type": "control",
                "operation": "execution.approval.resolve",
                "arguments_schema": ApprovalResolution.model_json_schema(),
                "field_sources": {"request_hash": "approval_required.request_hash"},
                "request_id": "Generate a new unique request_id for this decision.",
                "request_schema": request_schema("control"),
            },
            "example_request": {
                "protocol_version": PROTOCOL_VERSION,
                "type": "control",
                "request_id": "decision-1",
                "payload": {
                    "operation": "execution.approval.resolve",
                    "arguments": {"request_hash": "<event.request_hash>", "decision": "approve"},
                },
            },
            "completion": {
                "event": "control_result",
                "correlate_by": "request_id",
                "accepted_result_status": "resolved",
                "other_result_statuses": ["unknown_request", "already_resolved"],
            },
            "notes": [
                "Use the same live backend process; approval hashes do not survive CLI exit.",
                "Choose approve or deny using the caller's authorized approval policy.",
                "Task answers do not resolve execution approvals.",
            ],
        }
    if name == "waiting_for_input":
        schema = request_schema("submit_input")
        return {
            "kind": "interaction_contract",
            "name": name,
            "trigger": {"state_path": "task.waiting_for_input", "value": True},
            "response": {
                "transport": "session_stdin",
                "message_type": "submit_input",
                "payload_schema": schema["$defs"]["submitInputRequest"]["allOf"][1]["properties"][
                    "payload"
                ],
                "field_sources": {
                    "payload.text": "Answer the pending question in assistant messages."
                },
                "request_schema": schema,
                "request_id": "Generate a new unique request_id and a new task_id for the answer turn.",
            },
            "example_request": {
                "protocol_version": PROTOCOL_VERSION,
                "type": "submit_input",
                "request_id": "answer-1",
                "task_id": "answer-turn-1",
                "payload": {"text": "Read public pages only; do not submit forms."},
            },
            "completion": {
                "event": "turn_completed",
                "correlate_by": "request_id",
                "failures": ["turn_failed", "turn_cancelled", "error"],
            },
            "notes": [
                "Read the pending question from assistant messages and inspect authoritative state.",
                "task_completed can end a phase while task.waiting_for_input remains true.",
            ],
        }
    if name in CLIENT_MESSAGE_TYPES:
        payload = {}
        if name == "submit_input":
            payload = {"text": "Explain the saved evidence."}
        elif name == "start_task":
            payload = {"task": {"command": "run", "target": "https://authorized.example"}}
        elif name == "control":
            payload = {"operation": "session.help", "arguments": {}}
        example = {
            "protocol_version": PROTOCOL_VERSION,
            "type": name,
            "request_id": "request-1",
            "payload": payload,
        }
        if name in {"submit_input", "start_task", "cancel_task"}:
            example["task_id"] = "task-1"
        return {
            "kind": "protocol_request",
            "name": name,
            "request_schema": request_schema(name),
            "example_request": example,
            "completion": {
                "events": {
                    "initialize": ["ready"],
                    "get_state": ["state"],
                    "control": ["control_result"],
                    "shutdown": ["shutdown_complete"],
                    "start_task": ["task_completed", "task_failed", "task_cancelled"],
                    "submit_input": [
                        "turn_completed",
                        "turn_failed",
                        "turn_cancelled",
                        "control_result",
                    ],
                    "cancel_task": ["task_cancelled", "turn_cancelled"],
                }[name],
                "correlate_by": "request_id",
                "failures": ["error"],
            },
            "notes": [
                "cancel_task.task_id must match the currently active task.",
                "control operations are advertised by ready.capabilities.control_operations.",
            ],
        }
    raise ValueError(f"Unknown protocol name: {name}")
