"""Freeze the installed release package into a standalone native render binary."""

import argparse
import hashlib
from importlib import metadata
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import tomllib


def check_release(source: Path, version: str) -> None:
    project = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if project["name"] != "term-render" or project["version"] != version:
        raise ValueError("Source package name/version does not match the requested release")
    distribution = metadata.distribution("term-render")
    if distribution.version != version:
        raise ValueError("Install the requested release wheel before freezing it")
    installed = Path(distribution.locate_file("term_render"))
    pairs = [(path, installed / path.name) for path in (source / "src" / "term_render").glob("*.py")]
    pairs.append((source / "configs" / "default-config.json", installed / "configs" / "default-config.json"))
    for expected, actual in pairs:
        if not actual.is_file() or hashlib.sha256(expected.read_bytes()).digest() != hashlib.sha256(actual.read_bytes()).digest():
            raise ValueError(f"Installed package does not match the release source: {actual}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    output = args.output_dir.resolve()
    check_release(source, args.version)
    output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="term-render-native-") as temporary:
        work = Path(temporary)
        command = [
            sys.executable, "-I", "-m", "PyInstaller",
            "--noconfirm", "--clean", "--onefile", "--console", "--noupx",
            "--name", "render", "--add-data", f"{source / 'LICENSE'}:licenses", "--distpath", str(output),
            "--workpath", str(work / "build"), "--specpath", str(work),
            "--hidden-import", "term_render.configs", "--collect-data", "term_render.configs",
            # Frozen Python ignores normal PYTHONUTF8/PYTHONIOENCODING variables.
            "--python-option", "X utf8",
        ]
        licenses = (
            Path(sys.base_prefix) / "LICENSE.txt",
            Path(sys.base_prefix) / "LICENSE",
            Path(sysconfig.get_path("stdlib")) / "LICENSE.txt",
        )
        for license_path in licenses:
            if license_path.is_file():
                notice = work / "PYTHON-LICENSE.txt"
                shutil.copy2(license_path, notice)
                command.extend(["--add-data", f"{notice}:licenses"])
                break
        command.append(str(Path(__file__).with_name("entrypoint.py").resolve()))
        subprocess.run(command, cwd=work, check=True)

    binary = output / ("render.exe" if sys.platform == "win32" else "render")
    if not binary.is_file():
        raise RuntimeError(f"PyInstaller did not create {binary}")
    subprocess.run([sys.executable, str(Path(__file__).with_name("smoke.py")), "--binary", str(binary), "--version", args.version], check=True)
    print(f"Native binary: {binary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
