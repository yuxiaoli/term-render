"""Build a wheel and source distribution in dist/ using the current Python.

Requires the build package. Extra arguments are passed to python -m build,
for example: python scripts/build.py --wheel --no-isolation
Relative output paths are resolved from the repository root.
"""

from pathlib import Path
import subprocess
import sys


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    return subprocess.call(
        [sys.executable, "-m", "build", *sys.argv[1:]],
        cwd=root,
    )


if __name__ == "__main__":
    raise SystemExit(main())
