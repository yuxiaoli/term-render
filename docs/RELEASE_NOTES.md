Native packages for the render CLI, with an embedded Python runtime.

- Windows x86_64: `*-setup.exe` is a per-user installer; open a new terminal afterward.
- macOS Intel/Apple Silicon: choose the matching `.pkg`; installs `/usr/local/bin/render`.
- Linux x86_64 (glibc 2.35+): install `.deb` with apt or `.rpm` with dnf.
- Run `render --version`, `render setup`, and `render config` after installation.
- Update by installing the newer package; user configuration is preserved.

External viewers are separate prerequisites; `render setup` prints guidance.
Packages are unsigned; macOS packages are not notarized. No signing claim is made.
Compare downloads with `NATIVE-SHA256SUMS.txt`. `NATIVE-BUILD-INFO.json` records
the exact source commit, build tools, package hashes, and native install checks.
See the repository's docs/INSTALL.md for update/uninstall details and limitations.
RPM is install-tested in an isolated database/root on Ubuntu, not on Fedora/RHEL.
GitHub's automatic source archives are not application installers.
