# term-render

Open a local file with the right terminal viewer. `render` chooses a configured
viewer by file extension and operating system, forwards your terminal streams,
and returns the viewer's exit code.

**[Download native installers](https://github.com/yuxiaoli/term-render/releases/latest)**
· [Installation and updates](docs/INSTALL.md) · [Configuration](docs/CONFIGURATION.md)
· [Build from source](docs/BUILDING.md)

## Install

Download the asset for your operating system and CPU from the latest release.
These are native installers containing the `render` command and its Python runtime;
you do not need Python, uv, or a compiler to use them.

| Platform | Asset suffix | Install |
| --- | --- | --- |
| Windows 10 1809+ / 11, x86_64 | `windows-x86_64-setup.exe` | Run the per-user installer, then open a new terminal |
| macOS 13+, Intel | `macos-x86_64.pkg` | Open the package or use `sudo installer -pkg FILE.pkg -target /` |
| macOS 13+, Apple Silicon | `macos-arm64.pkg` | Open the package or use `sudo installer -pkg FILE.pkg -target /` |
| Linux x86_64, glibc 2.35+ | `linux-x86_64.deb` / `.rpm` | Use your package manager; see installation guide |

Packages are **unsigned**; macOS packages are **not notarized**. Review the source
and SHA-256 checksums before installing. GitHub's source ZIP/TAR files are for developers.

## Use

```sh
render --version
render --help
render setup
render config
render path/to/document.md
render --config custom-config.json path/to/document.md
render -- ./-filename.md
```

`render setup` checks viewer availability and prints installation guidance. It
does not install tools. Viewers are separate prerequisites: the default config
uses tools such as Glow, rich-cli, bat, viu, and anydoc. Run `render --show-config`
to inspect the exact routes, fallback viewers, and installation guidance.
Only install the viewers you need, and customize routes for your system.
Office/PDF conversion routes require anydoc; scanned documents may need OCR.
Image display depends on your terminal. No third-party viewers are bundled.

`render config` opens your user configuration in `$EDITOR`, validates it, then
checks viewers. Unknown extensions can use the configured text fallback after
a text-content check; unrecognized binary data is rejected.
Filenames and arguments are passed directly to executables without a shell.
Configuration can launch programs, so use a configuration you trust.

## Development

Python 3.11+ is required only for source installs and development:

```sh
python -m pip install -e '.[dev]'
python -m unittest discover -s tests -v
python -m build
```

Native builds and installation checks run on matching Windows, macOS, and Linux
GitHub runners. [Build instructions](docs/BUILDING.md) use the same commands as CI.
Tagged versions publish checksummed native packages after all build/install
checks pass. Bug reports and contributions are welcome through GitHub issues
and pull requests.

MIT licensed. See [LICENSE](LICENSE) and [third-party notices](docs/THIRD_PARTY.md).
