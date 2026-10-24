"""Build and verify the per-user Windows installer for a frozen render.exe."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time


APP_ID = "{B0B43C24-DF91-41F2-8BB7-9863FA8678C7}"
INSTALLER_KEY = r"Software\term-render\Installer"


def find_compiler() -> str:
    candidates = [os.environ.get("ISCC"), shutil.which("ISCC.exe"), shutil.which("iscc")]
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        if prefix := os.environ.get(variable):
            candidates.append(str(Path(prefix) / "Inno Setup 6" / "ISCC.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise RuntimeError("Inno Setup 6 is required; set ISCC to the installed ISCC.exe path.")


def run(command: list[str], *, timeout: int = 180, cwd: Path | None = None) -> None:
    print(subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, check=True, timeout=timeout, cwd=cwd)


def read_path() -> tuple[str, int] | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return winreg.QueryValueEx(key, "Path")
    except FileNotFoundError:
        return None


def write_path(value: tuple[str, int] | None) -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        if value is None:
            try:
                winreg.DeleteValue(key, "Path")
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(key, "Path", 0, value[1], value[0])


def path_entries(value: tuple[str, int] | None) -> list[str]:
    if value is None:
        return []
    return [os.path.normcase(os.path.normpath(os.path.expandvars(entry.strip(' "'))))
            for entry in value[0].split(";")]


def assert_no_existing_installer() -> None:
    import winreg

    keys = (INSTALLER_KEY, rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}_is1")
    for name in keys:
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, name, 0, winreg.KEY_READ | view):
                    pass
            except FileNotFoundError:
                continue
            raise RuntimeError("Installer verification requires a user without an existing term-render native installation.")


def verify_installer(installer: Path, version: str, smoke: Path) -> None:
    import winreg

    assert_no_existing_installer()
    original = read_path()
    with tempfile.TemporaryDirectory(prefix="term-render installer ") as directory:
        work = Path(directory)
        installed = work / "installed render"
        preexisting = work / "pre-existing render"
        try:
            for destination, preexisting_entry in ((installed, False), (preexisting, True)):
                before = original
                if preexisting_entry:
                    value = str(destination) if original is None or not original[0] else original[0] + ";" + str(destination)
                    before = (value, winreg.REG_EXPAND_SZ)
                    write_path(before)
                run([str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
                     f"/DIR={destination}", f"/LOG={work / ('preexisting.log' if preexisting_entry else 'install.log')}"], cwd=work)
                binary = destination / "render.exe"
                uninstaller = destination / "unins000.exe"
                try:
                    assert binary.is_file(), "Installer did not create render.exe"
                    key = os.path.normcase(os.path.normpath(str(destination)))
                    assert path_entries(read_path()).count(key) == 1, "Installer must add PATH once"
                    if preexisting_entry:
                        assert read_path()[0] == before[0], "Installer changed a pre-existing PATH entry"
                    else:
                        run([sys.executable, str(smoke), "--binary", str(binary), "--version", version], cwd=work)
                finally:
                    if uninstaller.is_file():
                        run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], cwd=work)
                        # Inno's launcher can return before its worker finishes;
                        # file removal also precedes the final PATH cleanup.
                        expected_path = None if before is None else before[0]
                        deadline = time.monotonic() + 30
                        while time.monotonic() < deadline:
                            current = read_path()
                            current_path = None if current is None else current[0]
                            if not binary.exists() and current_path == expected_path:
                                break
                            time.sleep(0.1)
                after = read_path()
                assert (None if after is None else after[0]) == (None if before is None else before[0]), (
                    "Uninstaller changed unrelated or pre-existing PATH entries", before, after,
                )
                assert not binary.exists(), "Uninstaller did not remove render.exe"
                write_path(original)
        finally:
            write_path(original)
    print("PASS: native Windows install, standalone smoke, PATH ownership, and uninstall", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--arch", choices=["x86_64"], required=True)
    parser.add_argument("--verify", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--smoke-script", type=Path, default=Path(__file__).with_name("smoke.py"))
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Windows installers must be built and verified on Windows")
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        parser.error("--version must be a three-part numeric release version")
    binary = args.binary.resolve(strict=True)
    if not binary.is_file() or binary.suffix.lower() != ".exe":
        parser.error("--binary must point to a frozen render.exe")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).with_name("windows.iss").resolve(strict=True)
    run([find_compiler(), "/Qp", f"/DBinaryPath={binary}", f"/DPackageVersion={args.version}", f"/O{output}", str(script)])
    installer = output / f"term-render-{args.version}-windows-{args.arch}-setup.exe"
    if not installer.is_file():
        raise RuntimeError(f"Inno Setup did not create {installer}")
    if args.verify:
        verify_installer(installer, args.version, args.smoke_script.resolve(strict=True))
    print(installer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
