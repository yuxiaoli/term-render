# Tests and native releases

Use Python 3.11+ for development. Release binaries use CPython 3.13 and pinned
PyInstaller 6.22.3. Build each target on its matching operating system and CPU;
these are native builds, not cross-builds.

```sh
python -m pip install '.[dev]'
python -m unittest discover -s tests -v
python -m build
python scripts/smoke_install.py
python -m pip install pyinstaller==6.22.3
python scripts/native/release.py check --source . --tag v0.1.0 --arch x86_64
python scripts/native/build.py --source . --output-dir output/frozen --version 0.1.0
```

Replace the example version with the version in `pyproject.toml` and
`src/term_render/__init__.py`; both must match the release tag. Install the package
non-editably before freezing. Windows needs Inno Setup 6 (`ISCC` can point to its compiler):

```sh
python scripts/native/package_windows.py --binary output/frozen/render.exe --output-dir output/installers --version 0.1.0 --arch x86_64
```

macOS needs the Apple command-line tools (`pkgbuild`, `installer`, `pkgutil`):

```sh
python scripts/native/package_unix.py --binary output/frozen/render --output-dir output/installers --version 0.1.0 --arch arm64
```

Linux x86_64 builds use Ubuntu 22.04 with `rpm`/`rpmbuild`, `dpkg-deb`, and sudo.
Use the same Unix command with `--arch x86_64`. The package helpers install,
smoke-test, and remove packages, so run them on a disposable build machine with
no existing native term-render installation. The frozen executable smoke checks
packaged configuration, help/version, Unicode filenames, literal arguments,
streams, exit codes, the config editor, and input-file preservation. Synthetic
viewers check dispatch; they do not establish third-party viewer fidelity.

`.github/workflows/test.yml` checks supported Python versions and OS families.
`.github/workflows/release.yml` runs tests and native installer checks on Windows
x86_64, macOS x86_64/arm64, and Linux x86_64. A stable `vMAJOR.MINOR.PATCH` tag
validates the source version, builds five installers, aggregates their hashes
and source provenance, and publishes a GitHub Release only after every native
job succeeds. The publish job alone has `contents: write`; builds have read access.
Failed jobs leave no newly published incomplete release. Inspect logs with
`gh run list --workflow release.yml` and `gh run view RUN_ID --log-failed`.
Retry a failed run with `gh run rerun RUN_ID --failed`; existing differing assets
are rejected rather than silently replaced.

Current binaries are unsigned and macOS packages are not notarized. No signing
credentials are configured. These packages intentionally do not claim signing,
Linux arm64, Windows arm64, or universal macOS support.
