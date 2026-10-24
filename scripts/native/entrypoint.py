"""Frozen console entry point; external viewers use the host library paths."""

import os
from pathlib import Path
import sys

from term_render.cli import main


def restore_external_environment() -> None:
    if not getattr(sys, "frozen", False):
        return

    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
        kernel32.SetDllDirectoryW.restype = ctypes.c_int
        if not kernel32.SetDllDirectoryW(None):
            raise ctypes.WinError(ctypes.get_last_error())
    elif sys.platform != "darwin":
        variable = "LIBPATH" if sys.platform.startswith("aix") else "LD_LIBRARY_PATH"
        original = os.environ.pop(f"{variable}_ORIG", None)
        if original is None:
            os.environ.pop(variable, None)
        else:
            os.environ[variable] = original

    bundle = Path(getattr(sys, "_MEIPASS")).resolve()
    for variable in ("PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH"):
        if variable not in os.environ:
            continue
        entries = []
        for entry in os.environ[variable].split(os.pathsep):
            try:
                bundled = bool(entry) and Path(entry).resolve().is_relative_to(bundle)
            except (OSError, RuntimeError, ValueError):
                bundled = False
            if not bundled:
                entries.append(entry)
        os.environ[variable] = os.pathsep.join(entries)


if __name__ == "__main__":
    restore_external_environment()
    raise SystemExit(main())
