"""Edit the selected configuration with an explicitly configured native editor."""

from __future__ import annotations

from importlib.resources import files
import os
from pathlib import Path
import shlex
import subprocess
from typing import Any

from .config import ConfigError, load_config, user_config_path
from .runner import resolve_executable


def _split_windows_editor(value: str) -> list[str]:
    """Split Windows command-line quoting without treating path slashes as escapes."""
    arguments: list[str] = []
    word: list[str] = []
    quoted = started = False
    index = 0
    while index < len(value):
        character = value[index]
        if character in " \t" and not quoted:
            if started:
                arguments.append("".join(word))
                word = []
                started = False
            index += 1
            continue
        started = True
        if character == "\\":
            start = index
            while index < len(value) and value[index] == "\\":
                index += 1
            count = index - start
            if index == len(value) or value[index] != '"':
                word.append("\\" * count)
                continue
            word.append("\\" * (count // 2))
            if count % 2:
                word.append('"')
                index += 1
                continue
            # An even number of slashes leaves a quote delimiter to process below.
            character = '"'
        if character == '"':
            if quoted and index + 1 < len(value) and value[index + 1] == '"':
                word.append('"')
                index += 2
            else:
                quoted = not quoted
                index += 1
            continue
        word.append(character)
        index += 1
    if quoted:
        raise ValueError("unclosed double quote")
    if started:
        arguments.append("".join(word))
    return arguments


def _split_editor(value: str, *, windows: bool) -> list[str]:
    if not value.strip() or "\0" in value:
        raise ConfigError("Set EDITOR to an editor command, for example code --wait or a quoted native executable path.")
    try:
        arguments = _split_windows_editor(value) if windows else shlex.split(value)
    except ValueError as error:
        raise ConfigError(f"Cannot parse EDITOR: {error}. Quote executable paths that contain spaces.") from error
    if not arguments or not arguments[0].strip():
        raise ConfigError("EDITOR must name a native executable, optionally followed by editor arguments.")
    return arguments


def edit_config(explicit: str | None = None) -> tuple[dict[str, Any], str]:
    """Open the selected config, then return its saved, validated contents.

    Resolve the file before validation so the editor can repair invalid JSON. An
    implicit packaged default becomes an editable user config on first use.
    """
    command = _split_editor(os.environ.get("EDITOR", ""), windows=os.name == "nt")
    try:
        executable = resolve_executable(str(Path(command[0]).expanduser()))
    except (OSError, ValueError, RuntimeError) as error:
        raise ConfigError(f"Cannot resolve EDITOR {command[0]!r}: {error}") from error
    if executable is None:
        raise ConfigError(
            f"Cannot find a native executable for EDITOR {command[0]!r}. "
            "Use an installed editor executable; on Windows select its .exe rather than a .cmd/.bat launcher."
        )
    selected = explicit if explicit is not None else os.environ.get("TERM_RENDER_CONFIG")
    try:
        if selected is not None:
            if not selected.strip():
                raise ConfigError("Configuration path must not be blank.")
            path = Path(selected).expanduser().resolve()
        else:
            path = user_config_path().expanduser().resolve()
            if not path.exists():
                raw = files("term_render.configs").joinpath("default-config.json").read_text(encoding="utf-8")
                path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    # Do not overwrite a configuration created concurrently.
                    with path.open("x", encoding="utf-8") as output:
                        output.write(raw)
                except FileExistsError:
                    pass
    except (OSError, ValueError, RuntimeError) as error:
        if isinstance(error, ConfigError):
            raise
        raise ConfigError(f"Cannot prepare configuration for editing: {error}") from error
    try:
        result = subprocess.run([executable, *command[1:], str(path)], shell=False, check=False)
    except OSError as error:
        raise ConfigError(f"Cannot launch EDITOR {command[0]!r}: {error}") from error
    if result.returncode:
        raise ConfigError(f"EDITOR exited with status {result.returncode}; configuration was not re-applied. File: {path}")
    return load_config(str(path))
