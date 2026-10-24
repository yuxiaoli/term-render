"""Local path resolution, command dispatch, and non-installing health checks."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from .console import Style


class RenderError(ValueError):
    """An actionable dispatch or launch failure."""


def resolve_file(value: str) -> Path:
    try:
        path = Path(value).expanduser().resolve(strict=True)
        if not path.is_file():
            raise RenderError(f"Not a regular file: {path}")
        return path
    except (OSError, ValueError, RuntimeError) as error:
        if isinstance(error, RenderError):
            raise
        raise RenderError(f"Cannot access file {value!r}: {error}") from error


def resolve_executable(value: str) -> str | None:
    executable = shutil.which(value)
    # Windows may invoke batch files via a system shell even with shell=False.
    if executable and Path(executable).suffix.lower() in (".bat", ".cmd"):
        return None
    return executable


def resolve_command(value: str) -> list[str] | None:
    executable = resolve_executable(value)
    if executable:
        return [executable]
    # npm exposes anydoc through a .cmd shim on Windows. Launch the known
    # package entry point with native Node directly; never execute the shim.
    if sys.platform == "win32" and Path(value).name.lower() == "anydoc":
        wrapper = shutil.which(value)
        prefixes = []
        if wrapper and Path(wrapper).name.lower() == "anydoc.cmd":
            prefixes.append(Path(wrapper).parent)
        # Already-open terminals can have a stale PATH after npm installation.
        # Only discover the standard user prefix for the bare command name;
        # explicit executable paths must keep their configured meaning.
        if not wrapper and value.lower() == "anydoc" and (appdata := os.environ.get("APPDATA")):
            prefixes.append(Path(appdata) / "npm")
        for prefix in prefixes:
            script = prefix / "node_modules" / "@firecrawl" / "anydoc" / "cli.js"
            node = prefix / "node.exe"
            native_node = str(node) if node.is_file() else resolve_executable("node")
            if not native_node:
                # An already-open terminal may predate Node's PATH update too.
                for variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
                    if directory := os.environ.get(variable):
                        installed_node = Path(directory) / "nodejs" / "node.exe"
                        if installed_node.is_file():
                            native_node = str(installed_node)
                            break
            if native_node and script.is_file():
                return [native_node, str(script)]
    return None


def _select_routes(config: dict[str, Any], path: Path, platform: str) -> list[dict]:
    extension = path.suffix.lower()
    for candidate in dict.fromkeys((extension, ".txt")):
        routes = [config.get(section, {}).get(candidate, {}).get(platform)
                  for section in ("extensions", "fallbacks")]
        matches = [route for route in routes if route is not None]
        if matches:
            return matches
    if extension in config["extensions"] or extension in config.get("fallbacks", {}):
        raise RenderError(f"Extension {extension} has no configured renderer for platform {platform}.")
    reason = config.get("pending", {}).get(extension)
    detail = f" Pending: {reason}" if reason else " Configure a .txt catch-all or an explicit mapping."
    raise RenderError(f"Unsupported extension: {extension or '(none)'}.{detail}")


def select_route(config: dict[str, Any], path: Path, platform: str) -> tuple[str, dict, list[str]]:
    return _route_command(config, _select_routes(config, path, platform)[0], path, platform)


def _route_command(config: dict[str, Any], route: dict, path: Path, platform: str) -> tuple[str, dict, list[str]]:
    tool_id = route["tool"]
    command = config["tools"][tool_id]["platforms"][platform]
    args = [str(path) if arg == "{file}" else arg for arg in route["args"]]
    return tool_id, command, args


def _run(tool_id: str, argv: list[str]) -> int:
    try:
        # Children inherit terminal capabilities and receive console Ctrl-C.
        result = subprocess.run(argv, shell=False, check=False)
    except OSError as error:
        raise RenderError(f"Cannot launch renderer {tool_id}: {error}") from error
    return 128 - result.returncode if result.returncode < 0 else result.returncode


def _announce_fallback(tool_id: str, reason: str, fallback_tool: str) -> None:
    label = Style(sys.stderr)("render:", "warning")
    print(f"{label} {tool_id}: {reason}; trying fallback {fallback_tool}.", file=sys.stderr)


def _attempt(tool_id: str, command: dict, args: list[str], fallback_tool: str | None) -> int | None:
    try:
        argv = resolve_command(command["executable"])
        if not argv:
            raise RenderError(f"Missing tool {tool_id}: {command['executable']!r}. {command['install']}")
        code = _run(tool_id, [*argv, *args])
    except RenderError as error:
        if fallback_tool is None:
            raise
        _announce_fallback(tool_id, str(error), fallback_tool)
        return None
    if fallback_tool is not None and code not in (0, 130):
        _announce_fallback(tool_id, f"exited with status {code}", fallback_tool)
        return None
    return code


def render(
    config: dict[str, Any], value: str, platform: str, *, _seen: frozenset[str] = frozenset(),
) -> int:
    path = resolve_file(value)
    extension = path.suffix.lower()
    if extension in _seen:
        raise RenderError(f"Conversion cycle at extension {extension}.")
    routes = _select_routes(config, path, platform)
    for index, route in enumerate(routes):
        tool_id, command, args = _route_command(config, route, path, platform)
        fallback_tool = routes[index + 1]["tool"] if index + 1 < len(routes) else None
        output_extension = route.get("output_extension")
        if output_extension is None:
            code = _attempt(tool_id, command, args, fallback_tool)
            if code is None:
                continue
            return code
        try:
            with tempfile.TemporaryDirectory(prefix="render-") as directory:
                output = Path(directory) / f"converted{output_extension}"
                converted_args = [str(output) if arg == "{output}" else arg for arg in args]
                code = _attempt(tool_id, command, converted_args, fallback_tool)
                if code is None:
                    continue
                if code:
                    return code
                if not output.is_file():
                    reason = f"Converter {tool_id} did not create its output file: {output}"
                    if fallback_tool is None:
                        raise RenderError(reason)
                    _announce_fallback(tool_id, reason, fallback_tool)
                    continue
                # A downstream viewer failure belongs to the converted file;
                # do not retry the original document's fallback after conversion.
                return render(config, str(output), platform, _seen=_seen | {extension})
        except OSError as error:
            raise RenderError(f"Cannot use temporary conversion output for {tool_id}: {error}") from error
    raise RenderError(f"No usable route for {extension} on {platform}.")


@dataclass(frozen=True)
class ToolStatus:
    tool: str
    status: str
    detail: str
    guidance: str = ""
    optional: bool = False


def check_tools(config: dict[str, Any], platform: str, timeout: float = 5.0) -> list[ToolStatus]:
    primary_used = {routes[platform]["tool"] for routes in config["extensions"].values() if platform in routes}
    fallback_used = {routes[platform]["tool"] for routes in config.get("fallbacks", {}).values() if platform in routes}
    results = []
    for tool_id, tool in sorted(config["tools"].items()):
        command = tool["platforms"].get(platform)
        if command is None or tool_id not in primary_used | fallback_used:
            results.append(ToolStatus(tool_id, "not applicable", f"No active {platform} route."))
            continue
        optional = tool_id not in primary_used
        prefix = "Optional fallback. " if optional else ""
        argv = resolve_command(command["executable"])
        if argv is None:
            results.append(ToolStatus(tool_id, "missing", prefix + command["executable"], command["install"], optional))
            continue
        try:
            result = subprocess.run(
                [*argv, *command["healthcheck"]], shell=False, check=False,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=timeout,
            )
            if result.returncode:
                results.append(ToolStatus(tool_id, "check failed", prefix + f"Health check exited {result.returncode}.", command["install"], optional))
            else:
                results.append(ToolStatus(tool_id, "available", prefix + " ".join(argv), command["install"], optional))
        except subprocess.TimeoutExpired:
            results.append(ToolStatus(tool_id, "check failed", prefix + f"Health check timed out after {timeout:g}s.", command["install"], optional))
        except OSError as error:
            results.append(ToolStatus(tool_id, "check failed", prefix + str(error), command["install"], optional))
    return results
