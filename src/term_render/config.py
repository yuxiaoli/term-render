"""Versioned JSON configuration with strict, dependency-free validation."""

from __future__ import annotations

import json
import os
import re
import sys
from importlib.resources import files
from pathlib import Path
from typing import Any

PLATFORMS = ("windows", "macos", "linux")
EXTENSION = re.compile(r"\.[a-z0-9][a-z0-9_-]*\Z")
TOOL_ID = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")


class ConfigError(ValueError):
    """A selected configuration cannot be used safely."""


def current_platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    raise ConfigError(f"Unsupported platform: {sys.platform}. Use Windows, macOS, or Linux.")


def _object(value: Any, where: str, required: set[str], optional: set[str] | None = None) -> None:
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: expected an object.")
    missing = required - value.keys()
    unknown = value.keys() - required - (optional or set())
    if missing or unknown:
        raise ConfigError(f"{where}: missing keys {sorted(missing)}; unknown keys {sorted(unknown)}.")


def _text(value: Any, where: str) -> None:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ConfigError(f"{where}: expected a nonblank string without NUL characters.")


def _array(value: Any, where: str, *, file_argument: bool, output_argument: bool = False) -> None:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{where}: expected a nonempty string array.")
    for arg in value:
        if not isinstance(arg, str) or "\0" in arg:
            raise ConfigError(f"{where}: arguments must be strings without NUL characters.")
        # Only a whole argument may be substituted. No shell or format expansion.
        placeholders = {"{file}"} | ({"{output}"} if output_argument else set())
        if ("{" in arg or "}" in arg) and arg not in placeholders:
            raise ConfigError(f"{where}: only literal whole arguments {sorted(placeholders)} are placeholders.")
    if value.count("{file}") != (1 if file_argument else 0):
        raise ConfigError(f"{where}: expected {'exactly one' if file_argument else 'no'} {{file}} argument.")
    if value.count("{output}") != (1 if output_argument else 0):
        raise ConfigError(f"{where}: expected {'exactly one' if output_argument else 'no'} {{output}} argument.")


def validate_config(config: Any) -> dict[str, Any]:
    _object(config, "config", {"version", "tools", "extensions"}, {"pending", "fallbacks"})
    if type(config["version"]) is not int or config["version"] != 1:
        raise ConfigError("config.version: only integer version 1 is supported.")
    for key in ("tools", "extensions", "fallbacks", "pending"):
        if not isinstance(config.get(key, {}), dict):
            raise ConfigError(f"config.{key}: expected an object.")
    for tool_id, tool in config["tools"].items():
        if not TOOL_ID.fullmatch(tool_id):
            raise ConfigError(f"tools: invalid tool identifier {tool_id!r}.")
        _object(tool, f"tools.{tool_id}", {"platforms"})
        platforms = tool["platforms"]
        if not isinstance(platforms, dict) or not platforms or platforms.keys() - set(PLATFORMS):
            raise ConfigError(f"tools.{tool_id}.platforms: use windows, macos, or linux.")
        for platform, command in platforms.items():
            where = f"tools.{tool_id}.platforms.{platform}"
            _object(command, where, {"executable", "healthcheck", "install"})
            _text(command["executable"], f"{where}.executable")
            executable = command["executable"]
            if "{" in executable or "}" in executable or executable.lower().endswith((".cmd", ".bat")):
                raise ConfigError(f"{where}.executable: use a native executable, not a batch file or placeholder.")
            _text(command["install"], f"{where}.install")
            _array(command["healthcheck"], f"{where}.healthcheck", file_argument=False)
    conversions: dict[tuple[str, str], set[str]] = {}
    for section in ("extensions", "fallbacks"):
        for extension, routes in config.get(section, {}).items():
            if not EXTENSION.fullmatch(extension):
                raise ConfigError(f"{section}: invalid lowercase extension {extension!r}.")
            if not isinstance(routes, dict) or not routes or routes.keys() - set(PLATFORMS):
                raise ConfigError(f"{section}.{extension}: use windows, macos, or linux routes.")
            for platform, route in routes.items():
                where = f"{section}.{extension}.{platform}"
                _object(route, where, {"tool", "args"}, {"output_extension"})
                _text(route["tool"], f"{where}.tool")
                tool = config["tools"].get(route["tool"])
                if tool is None or platform not in tool["platforms"]:
                    raise ConfigError(f"{where}: tool {route['tool']!r} has no definition for {platform}.")
                converts = "output_extension" in route
                if converts and route["output_extension"] not in (".md", ".txt"):
                    raise ConfigError(f"{where}.output_extension: use .md or .txt.")
                _array(route["args"], f"{where}.args", file_argument=True, output_argument=converts)
                if converts:
                    conversions.setdefault((extension, platform), set()).add(route["output_extension"])
    # Include every possible primary/fallback edge when rejecting conversion cycles.
    complete: set[tuple[str, str]] = set()
    active: set[tuple[str, str]] = set()

    def visit(node: tuple[str, str]) -> None:
        if node in active:
            raise ConfigError(f"conversion cycle at {node[0]} for {node[1]}.")
        if node in complete:
            return
        active.add(node)
        extension, platform = node
        for output_extension in conversions.get(node, ()):
            target = next((candidate for candidate in (output_extension, ".txt")
                           if any(platform in config.get(section, {}).get(candidate, {})
                                  for section in ("extensions", "fallbacks"))), None)
            if target is None:
                raise ConfigError(f"{extension}.{platform}: no renderer for converted extension {output_extension}.")
            visit((target, platform))
        active.remove(node)
        complete.add(node)

    for node in conversions:
        visit(node)
    for extension, reason in config.get("pending", {}).items():
        if (not EXTENSION.fullmatch(extension) or extension in config["extensions"]
                or extension in config.get("fallbacks", {})):
            raise ConfigError(f"pending: invalid or already routed extension {extension!r}.")
        _text(reason, f"pending.{extension}")
    return config


def user_config_path(platform: str | None = None) -> Path:
    platform = platform or current_platform()
    if platform == "windows":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif platform == "macos":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "term-render" / "config.json"


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError(f"Duplicate JSON key: {key!r}.")
        result[key] = value
    return result


def load_config(explicit: str | None = None) -> tuple[dict[str, Any], str]:
    selected = explicit if explicit is not None else os.environ.get("TERM_RENDER_CONFIG")
    try:
        if selected is not None:
            if not selected.strip():
                raise ConfigError("Configuration path must not be blank.")
            path = Path(selected).expanduser().resolve()
        else:
            path = user_config_path()
        if selected is None and not path.exists():
            raw = files("term_render.configs").joinpath("default-config.json").read_text(encoding="utf-8")
            source = "packaged default"
        else:
            raw = path.read_text(encoding="utf-8-sig")
            source = str(path)
        return validate_config(json.loads(raw, object_pairs_hook=_unique_object)), source
    except (OSError, ValueError, RuntimeError, RecursionError) as error:
        raise ConfigError(f"Cannot load configuration: {error}") from error
