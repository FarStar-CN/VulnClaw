"""VulnClaw MCP Lifecycle Manager — start/stop MCP servers and manage their lifetime."""

from __future__ import annotations

import asyncio
import subprocess
from contextlib import suppress
from typing import Any
from urllib.parse import urlparse

from vulnclaw.agent.builtin_tools import infer_port_from_url
from vulnclaw.config.schema import MCPServerConfig, VulnClawConfig
from vulnclaw.mcp.registry import MCPRegistry

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:  # pragma: no cover - optional runtime dependency
    ClientSession = None
    StdioServerParameters = None
    stdio_client = None


class MCPLifecycleManager:
    """Manages the lifecycle of MCP servers: start, stop, health check.

    For MVP, we use subprocess-based MCP communication.
    In later versions, this will use the Python MCP SDK for proper protocol handling.
    """

    def __init__(self, config: VulnClawConfig) -> None:
        self.config = config
        self.registry = MCPRegistry()
        self._processes: dict[str, subprocess.Popen] = {}
        self._mcp_clients: dict[str, Any] = {}  # Server attach capability cache
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task_constraints: Any = None

    def set_task_constraints(self, constraints: Any) -> None:
        """Attach current task constraints for tool-level enforcement."""
        self._task_constraints = constraints

    def _check_fetch_constraints(self, arguments: dict[str, Any]) -> dict[str, Any] | None:
        constraints = self._task_constraints
        if constraints is None or constraints.is_empty():
            return None

        url = str(arguments.get("url", "") or "").strip()
        if not url:
            return None

        try:
            parsed = urlparse(url)
        except Exception:
            parsed = None
        host = parsed.hostname.lower() if parsed and parsed.hostname else ""
        path = parsed.path.rstrip("/") if parsed and parsed.path else ""

        port = infer_port_from_url(url)
        if port is None:
            port = None

        if constraints.allowed_hosts and host and host not in constraints.allowed_hosts:
            allowed_hosts = ", ".join(constraints.allowed_hosts)
            return self._tool_result(
                ok=False,
                server="fetch",
                tool="fetch",
                execution_mode="local",
                error_type="constraint_violation",
                message=f"Host {host} is outside allowed scope [{allowed_hosts}] for url {url}",
                suggestion="Adjust the task scope or send the request to an allowed host.",
            )

        if host and host in constraints.blocked_hosts:
            return self._tool_result(
                ok=False,
                server="fetch",
                tool="fetch",
                execution_mode="local",
                error_type="constraint_violation",
                message=f"Host {host} is blocked by task constraints for url {url}",
                suggestion="Remove the blocked host from the request or adjust constraints.",
            )

        if constraints.allowed_paths and path and path not in constraints.allowed_paths:
            allowed_paths = ", ".join(constraints.allowed_paths)
            return self._tool_result(
                ok=False,
                server="fetch",
                tool="fetch",
                execution_mode="local",
                error_type="constraint_violation",
                message=f"Path {path} is outside allowed scope [{allowed_paths}] for url {url}",
                suggestion="Adjust the task scope or send the request to an allowed path.",
            )

        if path and path in constraints.blocked_paths:
            return self._tool_result(
                ok=False,
                server="fetch",
                tool="fetch",
                execution_mode="local",
                error_type="constraint_violation",
                message=f"Path {path} is blocked by task constraints for url {url}",
                suggestion="Remove the blocked path from the request or adjust constraints.",
            )

        if port is not None and constraints.allowed_ports and port not in constraints.allowed_ports:
            allowed = ", ".join(str(p) for p in constraints.allowed_ports)
            return self._tool_result(
                ok=False,
                server="fetch",
                tool="fetch",
                execution_mode="local",
                error_type="constraint_violation",
                message=f"Port {port} is outside allowed scope [{allowed}] for url {url}",
                suggestion="Adjust the task scope or send the request to an allowed port.",
            )

        if port is not None and port in constraints.blocked_ports:
            return self._tool_result(
                ok=False,
                server="fetch",
                tool="fetch",
                execution_mode="local",
                error_type="constraint_violation",
                message=f"Port {port} is blocked by task constraints for url {url}",
                suggestion="Remove the blocked port from the request or adjust constraints.",
            )

        return None

    def _tool_result(
        self,
        *,
        ok: bool,
        server: str,
        tool: str,
        execution_mode: str,
        content: Any = None,
        structured_content: dict[str, Any] | None = None,
        error_type: str | None = None,
        message: str = "",
        suggestion: str = "",
    ) -> dict[str, Any]:
        return {
            "ok": ok,
            "server": server,
            "tool": tool,
            "execution_mode": execution_mode,
            "content": content,
            "structured_content": structured_content,
            "error_type": error_type,
            "message": message,
            "suggestion": suggestion,
        }

    def start_enabled_servers(self) -> int:
        """Start all enabled MCP servers.

        Returns the number of servers successfully started.
        """
        with suppress(RuntimeError):
            self._loop = asyncio.get_running_loop()
        started = 0
        for name, server_config in self.config.mcp.servers.items():
            if server_config.enabled:
                self.registry.register_server(name)
                try:
                    if self._start_server(name, server_config):
                        started += 1
                except Exception as e:
                    self.registry.set_server_error(name, str(e), error_type="startup_error")
        return started

    def _start_server(self, name: str, config: MCPServerConfig) -> bool:
        """Start a single MCP server.

        Current execution modes:
        - fetch/memory: local implementation (usable now, no external MCP process)
        - stdio/sse others: attempt attach, then degrade to placeholder if unavailable
        """
        transport = config.transport

        if name in {"fetch", "memory"}:
            self.registry.set_server_running(name, running=False)
            self.registry.set_server_execution_mode(name, "local")
            self.registry.set_server_health(name, "healthy")
            self.registry.set_server_attach_result(name, attempted=False, succeeded=True)
            self._register_known_tools(name)
            return True

        if transport.type == "stdio":
            attached = self._try_attach_stdio_client(name, config)
            self.registry.set_server_attach_result(name, attempted=True, succeeded=attached)
            self.registry.set_server_running(name, running=attached)
            self.registry.set_server_execution_mode(name, "sdk" if attached else "placeholder")
            self.registry.set_server_health(name, "healthy" if attached else "degraded")
            if not attached:
                self._register_known_tools(name)
            return True

        if transport.type == "sse":
            attached = self._try_attach_sse_client(name, config)
            self.registry.set_server_attach_result(name, attempted=True, succeeded=attached)
            self.registry.set_server_running(name, running=attached)
            self.registry.set_server_execution_mode(name, "sse" if attached else "placeholder")
            self.registry.set_server_health(name, "healthy" if attached else "degraded")
            self._register_known_tools(name)
            return True

        self.registry.set_server_health(name, "unavailable")
        return False

    def _try_attach_stdio_client(self, name: str, config: MCPServerConfig) -> bool:
        """Attempt a real stdio MCP attach when SDK primitives are available."""
        transport = config.transport
        if ClientSession is None or StdioServerParameters is None or stdio_client is None:
            self.registry.set_server_error(
                name, "MCP Python SDK is not installed", error_type="sdk_unavailable"
            )
            return False

        if not transport.command:
            self.registry.set_server_error(
                name, "stdio transport is missing command", error_type="config_error"
            )
            return False

        if name not in {"chrome-devtools", "burp"}:
            self.registry.set_server_error(
                name,
                "stdio attach not implemented for this server yet",
                error_type="unsupported_mode",
            )
            return False

        ok, details, tools = self._probe_stdio_server(config)
        if not ok:
            self.registry.set_server_error(
                name, details or "stdio attach probe failed", error_type="attach_failed"
            )
            return False

        self._mcp_clients[name] = {"kind": "stdio-probe", "config": config}
        if tools:
            self._register_runtime_tools(name, tools)
        return True

    def _try_attach_sse_client(self, name: str, config: MCPServerConfig) -> bool:
        """Attempt a minimal SSE reachability/config validation before fallback."""
        from urllib.parse import urlparse

        url = config.transport.url or ""
        if not url:
            self.registry.set_server_error(
                name, "sse transport is missing url", error_type="config_error"
            )
            return False

        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            self.registry.set_server_error(
                name, f"invalid SSE url: {url}", error_type="config_error"
            )
            return False

        return False

    def _probe_stdio_server(
        self, config: MCPServerConfig
    ) -> tuple[bool, str, list[dict[str, Any]]]:
        """Run a one-shot stdio MCP probe to validate the server can initialize."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            return False, "stdio probe skipped because an event loop is already running", []

        try:
            return asyncio.run(self._async_probe_stdio_server(config))
        except RuntimeError as exc:
            return False, str(exc), []
        except Exception as exc:  # pragma: no cover - defensive
            return False, str(exc), []

    async def _async_probe_stdio_server(
        self, config: MCPServerConfig
    ) -> tuple[bool, str, list[dict[str, Any]]]:
        transport = config.transport
        server = StdioServerParameters(
            command=transport.command or "",
            args=transport.args or [],
            env=transport.env,
        )

        try:
            async with stdio_client(server) as (read_stream, write_stream):
                session = ClientSession(read_stream, write_stream)
                await session.initialize()
                tools = await session.list_tools()
                tool_defs = self._normalize_mcp_tools(getattr(tools, "tools", []) or [])
                return True, f"initialized with {len(tool_defs)} tools", tool_defs
        except Exception as exc:
            return False, str(exc), []

    def _normalize_mcp_tools(self, tools: list[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for tool in tools:
            name = getattr(tool, "name", None)
            if not name:
                continue
            normalized.append(
                {
                    "name": name,
                    "description": getattr(tool, "description", "") or "",
                    "inputSchema": getattr(tool, "inputSchema", None)
                    or getattr(tool, "input_schema", None)
                    or {"type": "object", "properties": {}},
                }
            )
        return normalized

    def _render_mcp_call_result(self, result: Any) -> tuple[str, dict[str, Any] | None, bool]:
        """Normalize an MCP CallToolResult into readable text plus structured data."""
        if result is None:
            return "", None, False

        structured = getattr(result, "structuredContent", None)
        is_error = bool(getattr(result, "isError", False))
        content_items = getattr(result, "content", None)

        if not content_items:
            return (
                str(structured or result),
                structured if isinstance(structured, dict) else None,
                is_error,
            )

        parts: list[str] = []
        for item in content_items:
            item_type = getattr(item, "type", "")
            if item_type == "text":
                text = getattr(item, "text", "")
                if text:
                    parts.append(str(text))
                continue
            if item_type == "image":
                mime = getattr(item, "mimeType", "") or getattr(item, "mime_type", "")
                parts.append(f"[image:{mime or 'unknown'}]")
                continue
            if item_type == "resource_link":
                uri = getattr(item, "uri", "")
                name = getattr(item, "name", "") or uri
                parts.append(f"[resource:{name}]")
                continue
            parts.append(str(item))

        rendered = "\n".join(part for part in parts if part).strip()
        if not rendered and structured is not None:
            rendered = str(structured)
        return rendered, structured if isinstance(structured, dict) else None, is_error

    def _register_runtime_tools(self, server_name: str, tools: list[dict[str, Any]]) -> None:
        """Replace static known tools with tools discovered from the live MCP server."""
        self.registry.clear_server_tools(server_name)
        for tool in tools:
            self.registry.register_tool(server_name, tool)

    async def _call_stdio_server(
        self, server_name: str, tool_name: str, arguments: dict[str, Any]
    ) -> Any:
        """Run a one-shot stdio MCP call using the Python SDK."""
        client_meta = self._mcp_clients.get(server_name)
        config = None
        if isinstance(client_meta, dict):
            config = client_meta.get("config")
        if config is None:
            config = self.config.mcp.servers.get(server_name)
        if config is None:
            raise RuntimeError(f"missing MCP config for server {server_name}")

        transport = config.transport
        server = StdioServerParameters(
            command=transport.command or "",
            args=transport.args or [],
            env=transport.env,
        )

        async with stdio_client(server) as (read_stream, write_stream):
            session = ClientSession(read_stream, write_stream)
            await session.initialize()
            result = await session.call_tool(tool_name, arguments=arguments)
            return result

    async def _get_or_create_persistent_stdio_session(self, server_name: str) -> Any:
        """Create and cache a persistent stdio-backed MCP session for the current loop."""
        client_meta = self._mcp_clients.get(server_name)
        current_loop = asyncio.get_running_loop()

        if isinstance(client_meta, dict) and client_meta.get("kind") == "persistent-stdio":
            if client_meta.get("loop") is current_loop and client_meta.get("session") is not None:
                return client_meta["session"]

        config = None
        if isinstance(client_meta, dict):
            config = client_meta.get("config")
        if config is None:
            config = self.config.mcp.servers.get(server_name)
        if config is None:
            raise RuntimeError(f"missing MCP config for server {server_name}")

        transport = config.transport
        server = StdioServerParameters(
            command=transport.command or "",
            args=transport.args or [],
            env=transport.env,
        )

        cm = stdio_client(server)
        read_stream, write_stream = await cm.__aenter__()
        session = ClientSession(read_stream, write_stream)
        await session.initialize()

        self._mcp_clients[server_name] = {
            "kind": "persistent-stdio",
            "config": config,
            "loop": current_loop,
            "session": session,
            "context_manager": cm,
        }
        return session

    def _register_known_tools(self, server_name: str) -> None:
        """Register known tools for a server based on its type.

        This is a temporary approach for MVP. In production, tools will be
        discovered dynamically via the MCP protocol.
        """
        KNOWN_TOOLS: dict[str, list[dict]] = {
            "fetch": [
                {
                    "name": "fetch",
                    "description": "Fetch a URL and return the content",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "URL to fetch"},
                            "method": {
                                "type": "string",
                                "description": "HTTP method",
                                "default": "GET",
                            },
                            "headers": {"type": "object", "description": "HTTP headers"},
                            "body": {"type": "string", "description": "Request body"},
                        },
                        "required": ["url"],
                    },
                },
            ],
            "memory": [
                {
                    "name": "save",
                    "description": "Save information to persistent memory",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string", "description": "Memory key"},
                            "value": {"type": "string", "description": "Memory value"},
                        },
                        "required": ["key", "value"],
                    },
                },
                {
                    "name": "retrieve",
                    "description": "Retrieve information from persistent memory",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string", "description": "Memory key to retrieve"},
                        },
                        "required": ["key"],
                    },
                },
            ],
            "chrome-devtools": [
                {
                    "name": "new_page",
                    "description": "Open a new browser page",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "URL to navigate to"},
                        },
                    },
                },
                {
                    "name": "navigate",
                    "description": "Navigate to a URL in the current page",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "URL to navigate to"},
                        },
                        "required": ["url"],
                    },
                },
                {
                    "name": "screenshot",
                    "description": "Take a screenshot of the current page",
                    "inputSchema": {"type": "object", "properties": {}},
                },
                {
                    "name": "evaluate_js",
                    "description": "Evaluate JavaScript in the browser",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "expression": {
                                "type": "string",
                                "description": "JS expression to evaluate",
                            },
                        },
                        "required": ["expression"],
                    },
                },
            ],
            "js-reverse": [
                {
                    "name": "analyze_js",
                    "description": "Analyze JavaScript code for security-relevant patterns",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "description": "URL of the JS file to analyze",
                            },
                            "code": {"type": "string", "description": "Raw JS code to analyze"},
                        },
                    },
                },
                {
                    "name": "extract_endpoints",
                    "description": "Extract API endpoints from JavaScript",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "description": "URL of the page to extract endpoints from",
                            },
                        },
                        "required": ["url"],
                    },
                },
            ],
            "burp": [
                {
                    "name": "send_http1_request",
                    "description": "Send an HTTP/1 request through Burp proxy",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "method": {"type": "string", "description": "HTTP method"},
                            "url": {"type": "string", "description": "Target URL"},
                            "headers": {"type": "object", "description": "Request headers"},
                            "body": {"type": "string", "description": "Request body"},
                        },
                        "required": ["method", "url"],
                    },
                },
                {
                    "name": "get_proxy_history",
                    "description": "Get proxy history from Burp",
                    "inputSchema": {"type": "object", "properties": {}},
                },
            ],
            "frida-mcp": [
                {
                    "name": "frida_attach",
                    "description": "Attach Frida to a running process",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "process": {"type": "string", "description": "Process name or PID"},
                            "script": {"type": "string", "description": "Frida script to inject"},
                        },
                        "required": ["process", "script"],
                    },
                },
                {
                    "name": "frida_spawn",
                    "description": "Spawn an app with Frida attached",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "package": {"type": "string", "description": "App package name"},
                            "script": {"type": "string", "description": "Frida script to inject"},
                        },
                        "required": ["package", "script"],
                    },
                },
            ],
            "adb-mcp": [
                {
                    "name": "adb_tap",
                    "description": "Tap on screen coordinates via ADB",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "integer", "description": "X coordinate"},
                            "y": {"type": "integer", "description": "Y coordinate"},
                        },
                        "required": ["x", "y"],
                    },
                },
                {
                    "name": "adb_screenshot",
                    "description": "Take a screenshot via ADB",
                    "inputSchema": {"type": "object", "properties": {}},
                },
                {
                    "name": "adb_shell",
                    "description": "Execute shell command on Android device",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "Shell command to execute",
                            },
                        },
                        "required": ["command"],
                    },
                },
            ],
            "jadx": [
                {
                    "name": "decompile",
                    "description": "Decompile an APK file",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "apk_path": {"type": "string", "description": "Path to APK file"},
                        },
                        "required": ["apk_path"],
                    },
                },
                {
                    "name": "get_source",
                    "description": "Get decompiled source code for a class",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "class_name": {
                                "type": "string",
                                "description": "Fully qualified class name",
                            },
                        },
                        "required": ["class_name"],
                    },
                },
            ],
            "ida-pro-mcp": [
                {
                    "name": "decompile_function",
                    "description": "Decompile a function in IDA Pro",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "address": {"type": "string", "description": "Function address"},
                        },
                        "required": ["address"],
                    },
                },
                {
                    "name": "get_xrefs",
                    "description": "Get cross-references to an address",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "address": {
                                "type": "string",
                                "description": "Address to find xrefs for",
                            },
                        },
                        "required": ["address"],
                    },
                },
            ],
            "sequential-thinking": [
                {
                    "name": "sequential_thinking",
                    "description": "Use structured sequential thinking for complex analysis",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "thought": {"type": "string", "description": "Current thought step"},
                            "next_step": {
                                "type": "string",
                                "description": "What to think about next",
                            },
                        },
                        "required": ["thought"],
                    },
                },
            ],
            "context7": [
                {
                    "name": "resolve_library_id",
                    "description": "Resolve a library name to its context7 ID",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "library_name": {
                                "type": "string",
                                "description": "Name of the library",
                            },
                        },
                        "required": ["library_name"],
                    },
                },
            ],
            "everything-search": [
                {
                    "name": "search_files",
                    "description": "Search for files on the local system",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query"},
                            "max_results": {
                                "type": "integer",
                                "description": "Max results to return",
                            },
                        },
                        "required": ["query"],
                    },
                },
            ],
        }

        tools = KNOWN_TOOLS.get(server_name, [])
        for tool in tools:
            self.registry.register_tool(server_name, tool)

    def stop_server(self, name: str) -> None:
        """Stop a single MCP server."""
        client_meta = self._mcp_clients.pop(name, None)
        if isinstance(client_meta, dict) and client_meta.get("kind") == "persistent-stdio":
            cm = client_meta.get("context_manager")
            loop = client_meta.get("loop")
            if cm is not None and loop is not None and not loop.is_closed():
                try:
                    future = asyncio.run_coroutine_threadsafe(cm.__aexit__(None, None, None), loop)
                    future.result(timeout=5)
                except Exception:
                    pass

        if name in self._processes:
            try:
                self._processes[name].terminate()
                self._processes[name].wait(timeout=5)
            except Exception:
                try:
                    self._processes[name].kill()
                except Exception:
                    pass
            del self._processes[name]

        self.registry.set_server_running(name, running=False)
        self.registry.set_server_health(name, "unknown")

    def stop_all(self) -> None:
        """Stop all running MCP servers."""
        for name in list(self._processes.keys()):
            self.stop_server(name)

        for name in self.registry.get_running_servers():
            self.registry.set_server_running(name, running=False)

    def running_count(self) -> int:
        """Number of currently running servers."""
        return len(self.registry.get_running_servers())

    def list_available_tools(self) -> list[str]:
        """List all available tool names."""
        return [
            schema.name
            for schema in [
                self.registry.get_tool_schema(n)
                for n in [
                    t for server_tools in self.registry._server_tools.values() for t in server_tools
                ]
            ]
            if schema is not None
        ]

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Get all tool schemas for LLM function calling."""
        return self.registry.get_all_tool_schemas()

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call an MCP tool by name.

        fetch/memory currently run via local implementations.
        Other servers expose structured unsupported/service-unavailable results.
        """

        server_name = self.registry.get_server_for_tool(tool_name)
        if not server_name:
            raise ValueError(f"Unknown tool: {tool_name}")

        server_state = self.registry.get_all_servers().get(server_name)
        mode = server_state.execution_mode if server_state else "unknown"

        try:
            if server_name == "fetch" and tool_name == "fetch":
                violation = self._check_fetch_constraints(arguments)
                if violation is not None:
                    self.registry.record_tool_call(server_name, success=False)
                    return violation
                content = await self._call_fetch(arguments)
                self.registry.record_tool_call(server_name, success=True)
                self.registry.set_server_health(server_name, "healthy")
                return self._tool_result(
                    ok=True,
                    server=server_name,
                    tool=tool_name,
                    execution_mode=mode,
                    content=content,
                    structured_content=None,
                )
            if server_name == "memory":
                content = await self._call_memory(tool_name, arguments)
                self.registry.record_tool_call(server_name, success=True)
                self.registry.set_server_health(server_name, "healthy")
                return self._tool_result(
                    ok=True,
                    server=server_name,
                    tool=tool_name,
                    execution_mode=mode,
                    content=content,
                    structured_content=None,
                )
            if server_name == "chrome-devtools":
                try:
                    content, structured = await self._call_chrome(tool_name, arguments)
                    self.registry.record_tool_call(server_name, success=True)
                    self.registry.set_server_health(server_name, "healthy")
                    return self._tool_result(
                        ok=True,
                        server=server_name,
                        tool=tool_name,
                        execution_mode=mode,
                        content=content,
                        structured_content=structured,
                    )
                except Exception as exc:
                    message = str(exc)
                    self.registry.record_tool_call(server_name, success=False)
                    self.registry.set_server_error(
                        server_name, message, error_type="service_unavailable"
                    )
                    return self._tool_result(
                        ok=False,
                        server=server_name,
                        tool=tool_name,
                        execution_mode=mode,
                        error_type="service_unavailable",
                        message=message,
                        suggestion="Start the chrome-devtools MCP service or switch to a browser-capable local setup.",
                    )
            if server_name == "burp":
                try:
                    content, structured = await self._call_burp(tool_name, arguments)
                    self.registry.record_tool_call(server_name, success=True)
                    self.registry.set_server_health(server_name, "healthy")
                    return self._tool_result(
                        ok=True,
                        server=server_name,
                        tool=tool_name,
                        execution_mode=mode,
                        content=content,
                        structured_content=structured,
                    )
                except Exception as exc:
                    message = str(exc)
                    self.registry.record_tool_call(server_name, success=False)
                    self.registry.set_server_error(
                        server_name, message, error_type="service_unavailable"
                    )
                    return self._tool_result(
                        ok=False,
                        server=server_name,
                        tool=tool_name,
                        execution_mode=mode,
                        error_type="service_unavailable",
                        message=message,
                        suggestion="Start the Burp MCP service and verify the proxy integration is ready.",
                    )

            message = (
                f"MCP tool '{tool_name}' is registered in {mode} mode but is not executable yet."
            )
            suggestion = (
                "Use a local alternative, or enable a runnable MCP backend for this service."
            )
            self.registry.record_tool_call(server_name, success=False)
            self.registry.set_server_error(server_name, message, error_type="unsupported_mode")
            return self._tool_result(
                ok=False,
                server=server_name,
                tool=tool_name,
                execution_mode=mode,
                error_type="unsupported_mode",
                message=message,
                suggestion=suggestion,
            )
        except Exception as exc:
            self.registry.record_tool_call(server_name, success=False)
            self.registry.set_server_error(server_name, str(exc), error_type="execution_failed")
            return self._tool_result(
                ok=False,
                server=server_name,
                tool=tool_name,
                execution_mode=mode,
                error_type="execution_failed",
                message=str(exc),
                suggestion="Inspect the MCP service health and tool arguments, then retry.",
            )

    async def _call_fetch(self, args: dict) -> str:
        """Execute a fetch request using httpx."""
        try:
            import httpx

            url = args.get("url", "")
            method = args.get("method", "GET").upper()
            headers = args.get("headers", {})
            body = args.get("body")

            async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    content=body,
                )

            result = f"Status: {response.status_code}\n"
            result += f"Headers: {dict(response.headers)}\n"
            result += f"Body (first 2000 chars): {response.text[:2000]}"
            return result

        except ImportError:
            return "[!] httpx 未安装，无法执行 fetch 请求"
        except Exception as e:
            return f"[!] fetch 请求失败: {e}"

    async def _call_memory(self, tool_name: str, args: dict) -> str:
        """Execute a memory tool call (local implementation)."""
        from vulnclaw.agent.memory import MemoryStore

        store = MemoryStore()

        if tool_name == "save":
            store.save(args.get("key", ""), args.get("value", ""))
            return f"[+] 已保存: {args.get('key', '')}"
        elif tool_name == "retrieve":
            value = store.retrieve(args.get("key", ""))
            return str(value) if value else "[-] 未找到"
        return "[!] 未知 memory 工具"

    async def _call_chrome(self, tool_name: str, args: dict) -> tuple[str, dict[str, Any] | None]:
        """Execute a Chrome DevTools tool call."""
        session = await self._get_or_create_persistent_stdio_session("chrome-devtools")
        result = await session.call_tool(tool_name, arguments=args)
        rendered, _, is_error = self._render_mcp_call_result(result)
        if is_error:
            raise RuntimeError(rendered or "chrome-devtools call returned an error")
        _, structured, _ = self._render_mcp_call_result(result)
        return rendered, structured

    async def _call_burp(self, tool_name: str, args: dict) -> tuple[str, dict[str, Any] | None]:
        """Execute a Burp Suite tool call."""
        session = await self._get_or_create_persistent_stdio_session("burp")
        result = await session.call_tool(tool_name, arguments=args)
        rendered, _, is_error = self._render_mcp_call_result(result)
        if is_error:
            raise RuntimeError(rendered or "burp call returned an error")
        _, structured, _ = self._render_mcp_call_result(result)
        return rendered, structured
