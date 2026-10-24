"""Build and install-test native macOS, Debian, and RPM packages."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile


PACKAGE = "term-render"
MACOS_IDENTIFIER = "io.github.yuxiaoli.term-render"


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, **kwargs)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def smoke(binary: Path, version: str) -> None:
    script = Path(__file__).with_name("smoke.py")
    run([sys.executable, str(script), "--binary", str(binary), "--version", version])


def stage_binary(binary: Path, root: Path, destination: str) -> Path:
    target = root / destination
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary, target)
    target.chmod(0o755)
    return target


def verify_macos(package: Path, binary: Path, version: str) -> None:
    installed = Path("/usr/local/bin/render")
    if installed.exists() or installed.is_symlink():
        raise RuntimeError(f"Refusing to overwrite an existing command: {installed}")
    receipt = subprocess.run(
        ["pkgutil", "--pkg-info", MACOS_IDENTIFIER], capture_output=True,
    )
    if receipt.returncode == 0:
        raise RuntimeError(f"Refusing to replace an existing receipt: {MACOS_IDENTIFIER}")
    try:
        run(["sudo", "-n", "installer", "-pkg", str(package), "-target", "/"])
        metadata = plistlib.loads(run(
            ["pkgutil", "--pkg-info-plist", MACOS_IDENTIFIER], capture_output=True,
        ).stdout)
        if metadata.get("pkg-version") != version:
            raise RuntimeError(f"Unexpected installed package version: {metadata}")
        files = run(
            ["pkgutil", "--files", MACOS_IDENTIFIER], capture_output=True, text=True,
        ).stdout.splitlines()
        if "usr/local/bin/render" not in [name.removeprefix("./") for name in files]:
            raise RuntimeError(f"The macOS receipt does not list the command: {files}")
        if digest(installed) != digest(binary):
            raise RuntimeError("The installed macOS binary differs from the tested build")
        smoke(installed, version)
    finally:
        if installed.is_file() and digest(installed) == digest(binary):
            run(["sudo", "-n", "rm", "-f", str(installed)])
        subprocess.run(
            ["sudo", "-n", "pkgutil", "--forget", MACOS_IDENTIFIER], check=False,
        )
    print("PASS: native macOS package installation, receipt, and command smoke")


def package_macos(binary: Path, output: Path, version: str, arch: str, verify: bool) -> list[Path]:
    target = output / f"{PACKAGE}-{version}-macos-{arch}.pkg"
    with tempfile.TemporaryDirectory(prefix="term-render-pkg-") as temporary:
        payload = Path(temporary) / "payload"
        stage_binary(binary, payload, "usr/local/bin/render")
        run([
            "pkgbuild", "--root", str(payload), "--ownership", "recommended",
            "--identifier", MACOS_IDENTIFIER, "--version", version,
            "--install-location", "/", str(target),
        ])
    if verify:
        verify_macos(target, binary, version)
    return [target]


def build_deb(binary: Path, output: Path, version: str, arch: str) -> Path:
    deb_arch = {"x86_64": "amd64", "arm64": "arm64"}[arch]
    target = output / f"{PACKAGE}-{version}-linux-{arch}.deb"
    with tempfile.TemporaryDirectory(prefix="term-render-deb-") as temporary:
        root = Path(temporary)
        staged = stage_binary(binary, root, "usr/bin/render")
        control = root / "DEBIAN"
        control.mkdir(mode=0o755)
        (control / "control").write_text(
            f"Package: {PACKAGE}\n"
            f"Version: {version}\n"
            "Section: utils\n"
            "Priority: optional\n"
            f"Architecture: {deb_arch}\n"
            f"Installed-Size: {(staged.stat().st_size + 1023) // 1024}\n"
            "Maintainer: term-render contributors <noreply@github.com>\n"
            "Depends: libc6 (>= 2.35)\n"
            "Homepage: https://github.com/yuxiaoli/term-render\n"
            "Description: Route local files to external terminal viewers\n"
            " Includes a standalone render command; Python and uv are not required.\n"
            " External renderer programs are installed separately.\n",
            encoding="utf-8",
        )
        run(["dpkg-deb", "--root-owner-group", "--build", str(root), str(target)])
    return target


def build_rpm(binary: Path, output: Path, version: str, arch: str) -> Path:
    rpm_arch = {"x86_64": "x86_64", "arm64": "aarch64"}[arch]
    target = output / f"{PACKAGE}-{version}-linux-{arch}.rpm"
    with tempfile.TemporaryDirectory(prefix="term-render-rpm-") as temporary:
        root = Path(temporary)
        for name in ("BUILD", "BUILDROOT", "RPMS", "SOURCES", "SPECS", "SRPMS"):
            (root / name).mkdir()
        shutil.copy2(binary, root / "SOURCES" / "render")
        spec = root / "SPECS" / "term-render.spec"
        spec.write_text(
            "%global __os_install_post %{nil}\n"
            "%global _build_id_links none\n"
            f"Name: {PACKAGE}\n"
            f"Version: {version}\n"
            "Release: 1\n"
            "Summary: Route local files to external terminal viewers\n"
            "License: MIT\n"
            "URL: https://github.com/yuxiaoli/term-render\n"
            f"BuildArch: {rpm_arch}\n"
            "Requires: glibc >= 2.35\n"
            "Source0: render\n"
            "\n%description\n"
            "Standalone render command with an embedded Python runtime.\n"
            "Python and uv are not required. External viewers are installed separately.\n"
            "\n%prep\n"
            "\n%build\n"
            "\n%install\n"
            "install -D -m 0755 \"%{SOURCE0}\" \"%{buildroot}/usr/bin/render\"\n"
            "\n%files\n"
            "%attr(0755,root,root) /usr/bin/render\n",
            encoding="utf-8",
        )
        run([
            "rpmbuild", "--define", f"_topdir {root}", "--define", "debug_package %{nil}",
            "--target", rpm_arch, "-bb", str(spec),
        ])
        built = list((root / "RPMS").rglob("*.rpm"))
        if len(built) != 1:
            raise RuntimeError(f"Expected one native RPM, found: {built}")
        shutil.copy2(built[0], target)
    return target


def verify_deb(package: Path, binary: Path, version: str, arch: str) -> None:
    installed = Path("/usr/bin/render")
    if installed.exists() or installed.is_symlink():
        raise RuntimeError(f"Refusing to overwrite an existing command: {installed}")
    existing = subprocess.run(["dpkg-query", "--status", PACKAGE], capture_output=True)
    if existing.returncode == 0:
        raise RuntimeError(f"Refusing to replace an existing package: {PACKAGE}")
    try:
        run(["sudo", "-n", "dpkg", "--install", str(package)])
        metadata = run([
            "dpkg-query", "--show", "--showformat=${Version}:${Architecture}", PACKAGE,
        ], capture_output=True, text=True).stdout
        deb_arch = {"x86_64": "amd64", "arm64": "arm64"}[arch]
        if metadata != f"{version}:{deb_arch}":
            raise RuntimeError(f"Unexpected installed Debian metadata: {metadata}")
        if digest(installed) != digest(binary):
            raise RuntimeError("The installed Debian binary differs from the tested build")
        smoke(installed, version)
    finally:
        run(["sudo", "-n", "dpkg", "--remove", PACKAGE])
    if installed.exists():
        raise RuntimeError("Debian uninstall left the render executable installed")
    print("PASS: native Debian install, metadata, command smoke, and uninstall")


def verify_rpm(package: Path, binary: Path, version: str, arch: str) -> None:
    files = run(["rpm", "--query", "--package", "--list", str(package)],
                capture_output=True, text=True).stdout.splitlines()
    if files != ["/usr/bin/render"]:
        raise RuntimeError(f"Unexpected RPM payload: {files}")
    rpm_arch = {"x86_64": "x86_64", "arm64": "aarch64"}[arch]
    with tempfile.TemporaryDirectory(prefix="term-render-rpm-installed-") as temporary:
        root = Path(temporary) / "root"
        root.mkdir()
        installed = root / "usr/bin/render"
        try:
            run(["sudo", "-n", "rpm", "--root", str(root), "--initdb"])
            run([
                "sudo", "-n", "rpm", "--root", str(root), "--install", "--nodeps",
                "--noscripts", str(package),
            ])
            metadata = run([
                "sudo", "-n", "rpm", "--root", str(root), "--query",
                "--queryformat", "%{VERSION}:%{ARCH}", PACKAGE,
            ], capture_output=True, text=True).stdout
            if metadata != f"{version}:{rpm_arch}":
                raise RuntimeError(f"Unexpected installed RPM metadata: {metadata}")
            if digest(installed) != digest(binary):
                raise RuntimeError("The installed RPM binary differs from the tested build")
            smoke(installed, version)
            run([
                "sudo", "-n", "rpm", "--root", str(root), "--erase", "--nodeps",
                "--noscripts", PACKAGE,
            ])
            if installed.exists():
                raise RuntimeError("RPM uninstall left the render executable installed")
        finally:
            # rpm's database belongs to root; return only this fresh temp tree
            # to the caller so TemporaryDirectory can remove its own files.
            run(["sudo", "-n", "chown", "-R", f"{os.getuid()}:{os.getgid()}", temporary])
    print("PASS: native RPM install, metadata, command smoke, and uninstall in an isolated root")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--arch", choices=("x86_64", "arm64"), required=True)
    parser.add_argument("--skip-verify", action="store_true", help="build without privileged install tests")
    args = parser.parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        parser.error("--version must be a stable numeric release version, such as 0.1.0")
    host_arch = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine().lower())
    if host_arch != args.arch:
        parser.error(f"native builds require a matching host; detected {host_arch}, requested {args.arch}")
    binary = args.binary.resolve(strict=True)
    if not binary.is_file():
        parser.error("--binary must be an existing executable file")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        packages = package_macos(binary, output, args.version, args.arch, not args.skip_verify)
    elif sys.platform == "linux":
        deb = build_deb(binary, output, args.version, args.arch)
        rpm = build_rpm(binary, output, args.version, args.arch)
        if not args.skip_verify:
            verify_deb(deb, binary, args.version, args.arch)
            verify_rpm(rpm, binary, args.version, args.arch)
        packages = [deb, rpm]
    else:
        parser.error("this helper supports only native macOS and Linux hosts")
    for package in packages:
        print(package)


if __name__ == "__main__":
    main()
