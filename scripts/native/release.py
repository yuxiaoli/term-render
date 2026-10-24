"""Validate, record, and upload native installers for an existing GitHub release."""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tomllib


TARGETS = {("windows", "x86_64"), ("macos", "x86_64"), ("macos", "arm64"), ("linux", "x86_64")}


def run_json(*arguments):
    return json.loads(subprocess.check_output(arguments, text=True, encoding="utf-8"))


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def release_version(tag):
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError("Use a stable release tag in vMAJOR.MINOR.PATCH form.")
    return tag[1:]


def source_info(source, tag, arch):
    version = release_version(tag)
    project = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if project["name"] != "term-render" or project["version"] != version:
        raise ValueError("Released source package name/version does not match the requested tag.")
    machine = platform.machine().lower()
    actual_arch = {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    if actual_arch != arch:
        raise ValueError(f"Native runner is {actual_arch}; requested {arch}.")
    target = {"Darwin": "macos", "Windows": "windows", "Linux": "linux"}.get(platform.system())
    if (target, arch) not in TARGETS:
        raise ValueError(f"Unsupported native installer target: {target}/{arch}.")
    commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    return {"tag": tag, "version": version, "source_commit": commit, "platform": target, "arch": arch}


def expected_names(version, target, arch):
    base = f"term-render-{version}-{target}-{arch}"
    if target == "windows":
        return {f"{base}-setup.exe"}
    if target == "macos":
        return {f"{base}.pkg"}
    return {f"{base}.deb", f"{base}.rpm"}


def record(args):
    data = source_info(args.source, args.tag, args.arch)
    names = expected_names(data["version"], data["platform"], args.arch)
    data["python"] = platform.python_version()
    data["pyinstaller"] = importlib.metadata.version("pyinstaller")
    data["packaging_commit"] = os.environ.get("GITHUB_SHA") or subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True,
    ).strip()
    data["verification"] = "frozen executable smoke and native package installation checks passed"
    data["installers"] = []
    for name in sorted(names):
        path = args.directory / name
        if not path.is_file():
            raise ValueError(f"Expected verified installer is missing: {path}")
        data["installers"].append({"name": name, "sha256": digest(path), "size": path.stat().st_size})
    output = args.directory / f"native-{data['platform']}-{args.arch}-build.json"
    output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Recorded {data['platform']}/{args.arch}: {data['source_commit']}")


def upload(args):
    version = release_version(args.tag)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        raise ValueError("Repository must be owner/name.")
    records = []
    files = {}
    seen_targets = set()
    for manifest in sorted(args.directory.rglob("native-*-build.json")):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        target = (data["platform"], data["arch"])
        if target not in TARGETS or target in seen_targets or data["tag"] != args.tag or data["version"] != version:
            raise ValueError(f"Unexpected or duplicate native build record: {manifest}")
        seen_targets.add(target)
        if {item["name"] for item in data["installers"]} != expected_names(version, *target):
            raise ValueError(f"Incomplete installer set in {manifest}")
        for item in data["installers"]:
            path = manifest.parent / item["name"]
            if not path.is_file() or path.stat().st_size != item["size"] or digest(path) != item["sha256"]:
                raise ValueError(f"Downloaded installer does not match its verified build: {path}")
            files[path.name] = path
        records.append(data)
    if seen_targets != TARGETS:
        raise ValueError(f"Native builds are incomplete; missing {sorted(TARGETS - seen_targets)}.")
    commit = run_json("gh", "api", f"repos/{args.repo}/commits/{args.tag}")["sha"]
    if any(data["source_commit"] != commit for data in records):
        raise ValueError("An installer was built from a different commit than the release tag.")
    checksums = args.directory / "NATIVE-SHA256SUMS.txt"
    checksums.write_text("".join(f"{digest(path)}  {name}\n" for name, path in sorted(files.items())), encoding="utf-8", newline="\n")
    provenance = args.directory / "NATIVE-BUILD-INFO.json"
    provenance.write_text(json.dumps({"tag": args.tag, "source_commit": commit, "builds": records}, indent=2) + "\n", encoding="utf-8", newline="\n")
    files.update({checksums.name: checksums, provenance.name: provenance})
    release = run_json("gh", "release", "view", args.tag, "--repo", args.repo, "--json", "assets,url,isDraft")
    existing = {asset["name"]: asset for asset in release["assets"]}
    pending = []
    # Check every collision before uploading anything. Never silently replace assets.
    for name, path in sorted(files.items()):
        asset = existing.get(name)
        if asset:
            if asset.get("digest") != f"sha256:{digest(path)}" or asset["size"] != path.stat().st_size:
                raise ValueError(f"Existing release asset differs: {name}. No assets were replaced.")
            print(f"Already uploaded: {name}")
        else:
            print(f"{'Would upload' if args.dry_run else 'Uploading'}: {name} ({path.stat().st_size} bytes)")
            pending.append(path)
    if not args.dry_run and pending:
        subprocess.run(["gh", "release", "upload", args.tag, "--repo", args.repo, *map(str, pending)], check=True)
    if not args.dry_run:
        assets = run_json("gh", "release", "view", args.tag, "--repo", args.repo, "--json", "assets")["assets"]
        uploaded = {asset["name"]: asset for asset in assets}
        for name, path in files.items():
            if uploaded.get(name, {}).get("digest") != f"sha256:{digest(path)}":
                raise ValueError(f"Uploaded asset digest could not be verified: {name}")
        print(f"Verified all five native installers: {release['url']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("check", "record"):
        child = commands.add_parser(command)
        child.add_argument("--source", type=Path, required=True)
        child.add_argument("--tag", required=True)
        child.add_argument("--arch", choices=("x86_64", "arm64"), required=True)
        if command == "check":
            child.add_argument("--github-output", type=Path)
        else:
            child.add_argument("--directory", type=Path, required=True)
    child = commands.add_parser("upload")
    child.add_argument("--tag", required=True)
    child.add_argument("--repo", required=True)
    child.add_argument("--directory", type=Path, required=True)
    child.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "check":
            data = source_info(args.source, args.tag, args.arch)
            print(json.dumps(data))
            if args.github_output:
                with args.github_output.open("a", encoding="utf-8") as output:
                    output.write(f"version={data['version']}\n")
        elif args.command == "record":
            record(args)
        else:
            upload(args)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"native release: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
