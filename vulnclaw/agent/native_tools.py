"""Responses local tools, routed through the existing execution boundary."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from vulnclaw.agent.tool_result_overrides import ToolOutput
from vulnclaw.llm.types import ToolCall


def _apply_diff(original: str, diff: str, *, create: bool = False) -> str:
    lines = diff.splitlines()
    if create:
        if any(not line.startswith("+") for line in lines):
            raise ValueError("New file diff must contain only added lines")
        return "\n".join(line[1:] for line in lines) + ("\n" if lines else "")
    source = original.splitlines()
    cursor = 0
    hunks: list[list[str]] = [[]]
    for line in lines:
        if line.startswith("@@"):
            if hunks[-1]:
                hunks.append([])
        else:
            hunks[-1].append(line)
    for hunk in hunks:
        eof = "*** End of File" in hunk
        hunk = [line for line in hunk if line != "*** End of File"]
        if any(not line or line[0] not in " +-" for line in hunk):
            raise ValueError("Invalid patch hunk")
        old = [line[1:] for line in hunk if line[0] in " -"]
        new = [line[1:] for line in hunk if line[0] in " +"]
        candidates = [
            i
            for i in range(cursor, len(source) - len(old) + 1)
            if source[i : i + len(old)] == old and (not eof or i + len(old) == len(source))
        ]
        if len(candidates) != 1:
            raise ValueError("Patch context missing or ambiguous; provide more context")
        index = candidates[0]
        source[index : index + len(old)] = new
        cursor = index + len(new)
    return "\n".join(source) + ("\n" if source else "")


def _operations(patch: str) -> list[dict]:
    lines = patch.splitlines()
    if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise ValueError("Expected Begin Patch / End Patch envelope")
    operations: list[dict] = []
    i = 1
    while i < len(lines) - 1:
        header = lines[i]
        prefixes = {
            "*** Add File: ": "create_file",
            "*** Delete File: ": "delete_file",
            "*** Update File: ": "update_file",
        }
        prefix = next((p for p in prefixes if header.startswith(p)), None)
        if prefix is None:
            raise ValueError("Expected file operation in patch")
        operation = {"type": prefixes[prefix], "path": header[len(prefix) :]}
        i += 1
        if i < len(lines) - 1 and lines[i].startswith("*** Move to: "):
            operation["move_to"] = lines[i][len("*** Move to: ") :]
            i += 1
        body = []
        while i < len(lines) - 1 and not any(lines[i].startswith(p) for p in prefixes):
            body.append(lines[i])
            i += 1
        operation["diff"] = "\n".join(body)
        operations.append(operation)
    if not operations:
        raise ValueError("Patch contains no operations")
    return operations


def _path(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError("Patch path must stay inside the working directory")
    return path


async def _patch(agent: Any, call: ToolCall) -> ToolOutput:
    from vulnclaw.agent.builtin_tools import dangerous_tool_refusal
    from vulnclaw.agent.exec_gate import GateRequest, get_execution_gate
    from vulnclaw.agent.roles import role_tool_violation

    refusal = dangerous_tool_refusal("shell_command", agent) or role_tool_violation(
        getattr(agent, "active_role", None),
        "shell_command",
        {},
    )
    if refusal:
        return ToolOutput(f"[!] {refusal}", f"[!] {refusal}", failed=True)
    root = Path.cwd().resolve()
    operations = (
        [call.raw["operation"]]
        if call.type == "apply_patch_call"
        else _operations(call.raw["input"])
    )
    originals: dict[Path, bytes | None] = {}
    changes: dict[Path, bytes | None] = {}
    paths: dict[str, Path] = {}
    for operation in operations:
        path = _path(root, operation["path"])
        paths[operation["path"]] = path
        if path in changes:
            raise ValueError("Duplicate file in patch")
        original = path.read_bytes() if path.exists() else None
        originals[path] = original
        kind = operation["type"]
        if kind == "create_file":
            if original is not None:
                raise ValueError("Create target already exists")
            changes[path] = _apply_diff("", operation["diff"], create=True).encode()
        elif kind == "delete_file":
            if original is None:
                raise ValueError("Delete target does not exist")
            changes[path] = None
        elif kind == "update_file":
            if original is None:
                raise ValueError("Update target does not exist")
            changes[path] = _apply_diff(original.decode(), operation["diff"]).encode()
            if operation.get("move_to"):
                destination = _path(root, operation["move_to"])
                paths[operation["move_to"]] = destination
                if destination.exists() or destination in changes:
                    raise ValueError("Move destination already exists")
                originals[destination] = None
                changes[destination], changes[path] = changes[path], None
        else:
            raise ValueError(f"Unsupported patch operation: {kind}")
    outcome = await get_execution_gate(getattr(agent, "config", None)).authorize(
        GateRequest(
            kind="apply_patch",
            display=json.dumps(operations, ensure_ascii=False),
            cwd=str(root),
            model_risk="review",
        ),
        run_id=str(getattr(getattr(agent, "runtime", None), "run_id", "") or ""),
    )
    if not outcome.approved:
        return ToolOutput("[!] " + outcome.refusal_text("apply_patch"), "[!] Patch denied", failed=True)
    for value, expected in paths.items():
        if _path(root, value) != expected:
            raise ValueError("Patch path changed during approval")
    for path, original in originals.items():
        if (path.read_bytes() if path.exists() else None) != original:
            raise ValueError("File changed during patch approval")
    written = []
    try:
        for path, content in changes.items():
            written.append(path)
            if content is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
    except OSError:
        for path in reversed(written):
            original = originals[path]
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(original)
        raise
    text = "Patch applied: " + ", ".join(str(path.relative_to(root)) for path in changes)
    return ToolOutput(text, text)


async def execute_native_tool(agent: Any, call: ToolCall) -> ToolOutput:
    if call.type == "apply_patch_call" or (
        call.type == "custom" and call.function.name == "apply_patch" and not call.namespace
    ):
        return await _patch(agent, call)
    if call.type in {"shell_call", "local_shell_call"}:
        action = call.raw.get("action") or {}
        commands = action.get("commands")
        if call.type == "local_shell_call":
            commands = [shlex.join(action["command"])]
        if (
            not isinstance(commands, list)
            or not commands
            or not all(isinstance(c, str) for c in commands)
        ):
            raise ValueError("Shell call requires commands")
        if action.get("env"):
            raise ValueError(
                "Native shell environment overrides are not supported by the execution gate"
            )
        outputs, text = [], []
        for command in commands:
            result = await agent._execute_mcp_tool(
                "shell_command",
                {
                    "command": command,
                    "workdir": action.get("working_directory"),
                    "timeout_ms": action.get("timeout_ms"),
                    "max_output_chars": action.get("max_output_length"),
                },
            )
            text.append(str(result))
            outputs.append(
                getattr(
                    result,
                    "native_output",
                    {
                        "stdout": "",
                        "stderr": str(result),
                        "outcome": {"type": "exit", "exit_code": 1},
                    },
                )
            )
        return ToolOutput(
            "\n".join(text), outputs if call.type == "shell_call" else "\n".join(text)
        )
    if call.type == "tool_search_call":
        arguments = call.raw.get("arguments") or {}
        query = str(arguments.get("query", arguments.get("goal", ""))).lower()
        terms = query.split()
        from vulnclaw.llm.adapters.responses import tools_for_wire

        tools = tools_for_wire(agent._build_openai_tools())
        found = [tool for tool in tools if any(term in json.dumps(tool).lower() for term in terms)]
        return ToolOutput(json.dumps(found), found)
    name = f"{call.namespace}.{call.function.name}" if call.namespace else call.function.name
    result = await agent._execute_mcp_tool(name, json.loads(call.function.arguments))
    return ToolOutput(str(result), str(result))
