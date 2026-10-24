"""Shared terminal styles for human-readable command output."""

from __future__ import annotations

import os
import sys
from typing import TextIO


def _supports_color(stream: TextIO) -> bool:
    if not stream.isatty() or os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    if sys.platform != "win32":
        return True
    # Enable ANSI processing on the actual output stream's Windows console.
    try:
        import ctypes
        from ctypes import wintypes
        import msvcrt

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetConsoleMode.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        kernel32.SetConsoleMode.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.SetConsoleMode.restype = wintypes.BOOL
        handle = msvcrt.get_osfhandle(stream.fileno())
        mode = wintypes.DWORD()
        return bool(kernel32.GetConsoleMode(handle, ctypes.byref(mode))
                    and kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError, ValueError):
        return False


class Style:
    """Apply semantic colors only when the selected stream supports them."""

    _codes = {
        # RGB values sampled from the reference terminal palette.
        "heading": "1;38;2;214;172;255",
        "name": "1",
        "executable": "1;38;2;255;146;223",
        "command": "1;38;2;105;255;148",
        "positional": "1;38;2;105;255;148",
        "short_option": "1;38;2;105;255;148",
        "option": "1;38;2;139;233;253",
        "value": "38;2;241;250;140",
        "muted": "2",
        "success": "38;2;105;255;148",
        "warning": "38;2;241;250;140",
        "error": "31",
    }

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = sys.stdout if stream is None else stream
        self.enabled = _supports_color(self.stream)

    def __call__(self, text: str, role: str) -> str:
        code = self._codes.get(role)
        return f"\033[{code}m{text}\033[0m" if self.enabled and code else text
