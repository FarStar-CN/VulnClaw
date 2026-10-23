"""Configuration operations shared by machine and interactive clients."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from vulnclaw.config.schema import BUILTIN_MCP_SERVERS, ENGINE_CHOICES, VulnClawConfig
from vulnclaw.config.settings import list_providers, load_config, save_config

_SECRET_NAMES = {
    "api_key",
    "api_keys",
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "client_secret",
    "authorization",
    "fofa_key",
    "hunter_key",
    "quake_key",
    "zoomeye_key",
    "shodan_key",
    "zerozone_key",
}


def masked_config(value: Any, *, name: str = "") -> Any:
    """Mask existing credentials, including arbitrary MCP transport environment/header values."""
    if name.lower() in _SECRET_NAMES or name.lower() in {"env", "headers"}:
        if isinstance(value, dict):
            return {key: "***" if item else item for key, item in value.items()}
        if isinstance(value, list):
            return ["***" if item else item for item in value]
        return "***" if value else value
    if isinstance(value, dict):
        return {key: masked_config(item, name=key) for key, item in value.items()}
    if isinstance(value, list):
        return [masked_config(item) for item in value]
    return value


def config_document(config: VulnClawConfig | None = None) -> dict[str, Any]:
    config = config or load_config()
    secret_paths: list[str] = []

    def collect(value: Any, path: str = "") -> None:
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            child = f"{path}.{key}" if path else key
            if child == "terminal":
                continue
            if key.lower() in _SECRET_NAMES:
                secret_paths.append(child)
            elif key.lower() in {"env", "headers"} and isinstance(item, dict):
                secret_paths.extend(f"{child}.{name}" for name in item)
            else:
                collect(item, child)

    collect(config.model_dump(mode="json"))
    schema = VulnClawConfig.model_json_schema()
    schema["$defs"]["SessionConfig"]["properties"]["engine"]["enum"] = list(ENGINE_CHOICES)
    public = masked_config(config.model_dump(mode="json"))
    # Theme names are user-defined dictionary keys; their validated colors are public.
    public["terminal"] = config.terminal.model_dump(mode="json")
    return {
        "config": public,
        "terminal_configured": "terminal" in config.model_fields_set,
        "schema": schema,
        "providers": list_providers(),
        "builtin_servers": list(BUILTIN_MCP_SERVERS),
        "secret_paths": secret_paths,
    }


def update_config(
    changes: dict[str, Any], *, config: VulnClawConfig | None = None
) -> VulnClawConfig:
    """Apply dotted-path edits, preserving masked credentials.

    Secret-list items may use {"$keep": index} to retain a saved item when
    earlier items are removed; references are resolved before schema validation.
    """
    if not isinstance(changes, dict) or not changes:
        raise ValueError("provide a non-empty changes object")
    raw = deepcopy((config or load_config()).model_dump(mode="json"))
    for path, value in changes.items():
        parts = path.split(".")
        node = raw
        for part in parts[:-1]:
            if not isinstance(node, dict) or part not in node:
                raise ValueError(f"unknown configuration path: {path}")
            node = node[part]
        if not isinstance(node, dict) or (
            parts[-1] not in node
            and parts[:2] != ["mcp", "servers"]
            and "env" not in parts
            and "headers" not in parts
        ):
            raise ValueError(f"unknown configuration path: {path}")
        node[parts[-1]] = _preserve_masked(value, node.get(parts[-1]), parts[-1])
    # Validate before saving or mutating the live configuration.
    result = VulnClawConfig.model_validate(raw)
    save_config(result)
    return result


def _preserve_masked(value: Any, existing: Any, name: str) -> Any:
    """Masked form documents can replace containers without replacing their secrets."""
    if existing is not None and value == masked_config(existing, name=name):
        return deepcopy(existing)
    if isinstance(value, dict) and isinstance(existing, dict):
        return {
            key: _preserve_masked(
                item, existing.get(key), key if name not in {"env", "headers"} else "secret"
            )
            for key, item in value.items()
        }
    if isinstance(value, list) and isinstance(existing, list):
        result = []
        for index, item in enumerate(value):
            if name.lower() in _SECRET_NAMES and isinstance(item, dict) and "$keep" in item:
                source = item["$keep"]
                if set(item) != {"$keep"} or type(source) is not int or not 0 <= source < len(existing):
                    raise ValueError("invalid saved credential reference")
                result.append(deepcopy(existing[source]))
            else:
                result.append(_preserve_masked(
                    item, existing[index] if index < len(existing) else None, name,
                ))
        return result
    return value
