"""Verify a standalone render executable from outside the checkout."""

import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--version")
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    version = args.version or metadata.version("term-render")
    platform = "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "linux"

    with tempfile.TemporaryDirectory(prefix="render native smoke ") as directory:
        work = Path(directory)
        environment = {
            **os.environ, "NO_COLOR": "1", "TERM": "dumb",
            "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
            "APPDATA": str(work / "profile"), "XDG_CONFIG_HOME": str(work / "profile"),
            "HOME": str(work / "profile"), "FORCE_COLOR": "1",
        }
        environment.pop("TERM_RENDER_CONFIG", None)

        def run(*arguments: str, input: str | None = None) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [str(binary), *arguments], cwd=work, env=environment,
                input=input, text=True, encoding="utf-8", capture_output=True, timeout=45,
            )

        for arguments in (("--help",), ("help",)):
            result = run(*arguments)
            assert result.returncode == 0, result.stderr
            assert "Commands:" in result.stdout and "config" in result.stdout, result.stdout
            assert "\x1b[" not in result.stdout and not result.stderr, result.stderr
        result = run("--version")
        assert result.returncode == 0 and result.stdout.strip() == f"render {version}", result
        result = run("--show-config")
        assert result.returncode == 0 and not result.stderr, result.stderr
        defaults = json.loads(result.stdout)
        assert defaults["version"] == 1 and ".md" in defaults["extensions"], defaults

        viewer = work / "synthetic viewer 测试.py"
        viewer.write_text(
            "import json,os,sys\n"
            "if '--health' in sys.argv: raise SystemExit(0)\n"
            "print(json.dumps({'args':sys.argv[1:],'stdin':sys.stdin.read(),'color':os.environ.get('FORCE_COLOR')}))\n"
            "print('\\x1b[31mnative viewer stderr\\x1b[0m',file=sys.stderr)\n"
            "raise SystemExit(7)\n", encoding="utf-8",
        )
        target = work / "-测试 & $() ; [file].MD"
        target.write_text("# Original file\n", encoding="utf-8")
        digest = hashlib.sha256(target.read_bytes()).digest()
        config = {
            "version": 1,
            "tools": {"probe": {"platforms": {platform: {
                "executable": sys.executable, "healthcheck": [str(viewer), "--health"],
                "install": "Synthetic viewer for the native package smoke check.",
            }}}},
            "extensions": {".md": {platform: {
                "tool": "probe", "args": [str(viewer), "--literal", "$literal; &", "{file}"],
            }}},
        }
        config_path = work / "config 测试 with spaces.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        result = run("--config", str(config_path), "--", target.name, input="interactive input\n")
        assert result.returncode == 7, result.stderr
        output = json.loads(result.stdout)
        assert output["args"] == ["--literal", "$literal; &", str(target.resolve())], output
        assert output["stdin"] == "interactive input\n" and output["color"] == "1", output
        assert result.stderr == "\x1b[31mnative viewer stderr\x1b[0m\n", result.stderr
        assert hashlib.sha256(target.read_bytes()).digest() == digest

        result = run("--config", str(config_path), "setup")
        assert result.returncode == 0 and "probe: available" in result.stdout and not result.stderr, result
        result = run("--config", str(config_path), "missing.md")
        assert result.returncode == 2 and "Cannot access file" in result.stderr, result

        editor = work / "synthetic editor 测试.py"
        editor.write_text(
            "from pathlib import Path\nimport sys\n"
            f"Path(sys.argv[1]).write_text({json.dumps(config)!r},encoding='utf-8')\n",
            encoding="utf-8",
        )
        command = [sys.executable, str(editor)]
        environment["EDITOR"] = subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)
        config_path.write_text("{", encoding="utf-8")
        result = run("--config", str(config_path), "config")
        assert result.returncode == 0 and "saved and validated" in result.stdout, result
        assert "probe: available" in result.stdout and not result.stderr, result
        assert json.loads(config_path.read_text(encoding="utf-8")) == config

    print(f"PASS: native {platform} binary, packaged config, help/version, Unicode argv, streams, exit codes, config editor, unchanged input.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
