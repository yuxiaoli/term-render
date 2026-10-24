# Configuration

`render --show-config` prints the selected configuration. `render config` creates
or edits the per-user JSON file with `$EDITOR`, validates it, and checks tools.
Use `render --config PATH ...` or `TERM_RENDER_CONFIG` to select a different file.
Default user locations are `%APPDATA%/term-render/config.json` on Windows,
`~/Library/Application Support/term-render/config.json` on macOS, and
`$XDG_CONFIG_HOME/term-render/config.json` (normally `~/.config/term-render/config.json`)
on Linux. See `render --help` for command options.

[Full example](../data/config.example.json), [JSON schema](../data/config.schema.json),
and [packaged defaults](../configs/default-config.json) describe the supported format.
The examples contain public tool names and literal argument placeholders only.

A minimal configuration for a viewer named `glow`:

```json
{
  "version": 1,
  "tools": {
    "glow": {
      "platforms": {
        "linux": {
          "executable": "glow",
          "healthcheck": ["--version"],
          "install": "Install glow with your system package manager."
        }
      }
    }
  },
  "extensions": {
    ".md": {"linux": {"tool": "glow", "args": ["{file}"]}}
  }
}
```

Add `windows` and/or `macos` entries to use a route there. `{file}` is a whole
argument replaced with the absolute filename; shell expansion does not occur.
Conversion routes use a temporary `{output}` file and an `output_extension`.
The source file is preserved. `fallbacks` can define text/image/document routes;
`pending` can record unsupported extensions. Configuration is validated strictly.
An external program controls its own behavior, output fidelity, and network access.
