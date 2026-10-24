# Install, update, and uninstall

Get packages from [GitHub Releases](https://github.com/yuxiaoli/term-render/releases/latest).
Choose your OS and CPU; `x86_64` means Intel/AMD 64-bit, and `arm64` means Apple Silicon.
Every release includes `NATIVE-SHA256SUMS.txt` and build/source provenance in
`NATIVE-BUILD-INFO.json`. Compare the downloaded file's SHA-256 with the checksum file:

```powershell
Get-FileHash .	erm-render-*-windows-x86_64-setup.exe -Algorithm SHA256
```

On macOS use `shasum -a 256 FILE.pkg`; on Linux use `sha256sum FILE.deb` (or `.rpm`).
Checksums detect corruption; they are not a code-signing certificate.

## Windows

Run `term-render-VERSION-windows-x86_64-setup.exe`. This is an **installer**, not
a standalone executable. It installs to `%LOCALAPPDATA%\Programs\term-render`
without admin rights and adds that directory to your user PATH. Open a new terminal
and run `render --version`. Update by running the newer installer at the same location.
Uninstall from Windows Settings > Apps. The uninstaller removes its own PATH entry
and preserves unrelated entries and your configuration.

## macOS

Choose the Intel or Apple Silicon `.pkg`, then run:

```sh
sudo installer -pkg term-render-VERSION-macos-ARCH.pkg -target /
render --version
```

It installs `/usr/local/bin/render`. Ensure `/usr/local/bin` is in PATH. Update by
installing a newer package. Packages are unsigned and not notarized; macOS may
require explicit approval in System Settings > Privacy & Security after review.
To uninstall: `sudo rm /usr/local/bin/render`, then
`sudo pkgutil --forget io.github.yuxiaoli.term-render`.

## Linux

Linux builds require x86_64 and glibc 2.35+ (Ubuntu 22.04 baseline); Alpine/musl
is not supported. On Debian/Ubuntu:

```sh
sudo apt install ./term-render-VERSION-linux-x86_64.deb
render --version
```

On a compatible RPM distribution: `sudo dnf install ./term-render-VERSION-linux-x86_64.rpm`.
Install the newer package to update. Uninstall with `sudo apt remove term-render`
or `sudo dnf remove term-render`. RPM database install/remove and executable startup
are checked in an isolated root on Ubuntu; a Fedora/RHEL desktop is not verified.

## Configure viewers

Run `render setup`, install required viewers separately, and use `render config`
to select the tools you need. Python and build tools are embedded in the native
command; viewer programs are separate. No automated viewer installation occurs.
Updates and uninstall preserve the user's configuration file.
