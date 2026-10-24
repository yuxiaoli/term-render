"""Exercise an installed wheel from outside the source tree using a real child.

Run with the isolated installation's Python, not the development interpreter.
The synthetic viewer verifies dispatch and inherited streams, not format rendering.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    with tempfile.TemporaryDirectory(prefix="term-render smoke ") as directory:
        work = Path(directory)
        viewer = work / "synthetic viewer 测试.py"
        viewer.write_text(
            "import json, os, sys\n"
            "if '--health' in sys.argv: sys.exit(0)\n"
            "print(json.dumps({'args':sys.argv[1:], 'stdin':sys.stdin.read(), "
            "'color':os.environ.get('FORCE_COLOR')}))\n"
            "print('\\x1b[31msynthetic stderr\\x1b[0m', file=sys.stderr)\n"
            "sys.exit(7)\n",
            encoding="utf-8",
        )
        file = work / "-测试 & $() ; [file].MD"
        file.write_text("# Original file\n", encoding="utf-8")
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        platform = "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "linux"
        config = {"version": 1, "tools": {"probe": {"platforms": {
            platform: {"executable": sys.executable, "healthcheck": [str(viewer), "--health"], "install": "Synthetic test viewer; no installation."}
        }}}, "extensions": {".md": {platform: {"tool": "probe", "args": [str(viewer), "--literal", "$literal; &", "{file}"]}}}}
        config_path = work / "config with spaces.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        # -I ignores PYTHONIOENCODING, so select UTF-8 explicitly for captures.
        command = [sys.executable, "-I", "-X", "utf8", "-m", "term_render", "--config", str(config_path)]
        env = {**os.environ, "FORCE_COLOR": "1", "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run([*command, "--", file.name], cwd=work, input="interactive input\n",
                                text=True, encoding="utf-8", capture_output=True, env=env, timeout=20)
        assert result.returncode == 7, result.stderr
        output = json.loads(result.stdout)
        assert output["args"] == ["--literal", "$literal; &", str(file.resolve())], output
        assert output["stdin"] == "interactive input\n", output
        assert output["color"] == "1", output
        assert "\x1b[31msynthetic stderr\x1b[0m" in result.stderr, result.stderr
        assert hashlib.sha256(file.read_bytes()).hexdigest() == digest
        setup = subprocess.run([*command, "setup"], cwd=work, capture_output=True, text=True, encoding="utf-8", env=env, timeout=20)
        assert setup.returncode == 0 and "probe: available" in setup.stdout, setup.stderr
        invalid = subprocess.run([*command, "missing.md"], cwd=work, capture_output=True, text=True, encoding="utf-8", env=env, timeout=20)
        assert invalid.returncode == 2 and "Cannot access file" in invalid.stderr
        # The console script and python -m entry point are both installed.
        script = Path(sys.executable).parent / ("render.exe" if sys.platform == "win32" else "render")
        version = subprocess.run([str(script), "--version"], cwd=work, capture_output=True, text=True, timeout=20)
        assert version.returncode == 0 and "render " + __import__("importlib.metadata", fromlist=["version"]).version("term-render") in version.stdout, version.stderr
        origin = subprocess.run([sys.executable, "-I", "-c", "import term_render; print(term_render.__file__)"], cwd=work, capture_output=True, text=True, timeout=20)
        assert origin.returncode == 0 and "site-packages" in origin.stdout, origin.stdout
        print(f"PASS: installed console entry point, isolated module import, real {platform} child, literal Unicode path/argv, stdin, stdout, stderr, ANSI color, exit 7, setup, missing file, unchanged input.")
        print(f"Installed import: {origin.stdout.strip()}")
        print("Synthetic viewer only; no third-party renderer fidelity or manual interactive TUI claim.")


if __name__ == "__main__":
    main()
