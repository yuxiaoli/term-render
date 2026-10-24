"""The render command surface."""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import TextIO

from . import __version__
from .console import Style
from .config import ConfigError, current_platform, load_config
from .editor import edit_config
from .runner import RenderError, check_tools, render


def _style_syntax(text: str, style: Style) -> str:
    def token_style(match: re.Match[str]) -> str:
        token = match.group()
        if token in ("render", "uv"):
            role = "executable"
        elif token in ("config", "setup", "help", "run"):
            role = "command"
        elif token.startswith("--"):
            role = "option"
        elif re.fullmatch(r"-[A-Za-z]", token):
            role = "short_option"
        elif token == "target":
            role = "positional"
        else:
            role = "value"
        return style(token, role)

    # Match whole tokens so command words inside filenames keep the value color.
    return re.sub(r"[^\s\[\](),]+", token_style, text)


class _ArgumentParser(argparse.ArgumentParser):
    def _print_message(self, message: str | None, file: TextIO | None = None) -> None:
        stream = file if file is not None else sys.stderr
        style = Style(stream)
        if message and message.startswith("usage:"):
            lines = []
            section = ""
            # Style after argparse has measured and wrapped the plain text.
            for line in message.splitlines(keepends=True):
                if line.startswith("usage:"):
                    section = "usage"
                    line = style("usage:", "heading") + _style_syntax(line[len("usage:"):], style)
                elif line.rstrip().endswith(":") and not line[0].isspace():
                    section = line.rstrip()
                    heading = line.rstrip("\r\n")
                    line = style(heading, "heading") + line[len(heading):]
                elif section == "usage" and line.startswith(" "):
                    line = _style_syntax(line, style)
                elif section in ("positional arguments:", "options:", "Commands:"):
                    match = re.match(r"^(  )(\S.*?)(?= {2,}|\r?\n|$)", line)
                    if match:
                        name = match.group(2)
                        line = "  " + _style_syntax(name, style) + line[match.end():]
                elif section == "Examples:" and line.startswith("  "):
                    example = line[2:].rstrip("\r\n")
                    line = "  " + _style_syntax(example, style) + line[2 + len(example):]
                if line.startswith("Use "):
                    guidance = line.rstrip("\r\n")
                    line = style(guidance, "muted") + line[len(guidance):]
                lines.append(line)
            message = "".join(lines)
        elif message and message.startswith(f"{self.prog}: error:"):
            prefix = f"{self.prog}: error:"
            message = style(prefix, "error") + message[len(prefix):]
        super()._print_message(message, stream)


def _check_config(config: dict, source: str, platform: str) -> int:
    style = Style()

    print(f"{style('Configuration:', 'heading')} {source}\n{style('Platform:', 'heading')} {platform}")
    print(style("Checks executable availability only; does not verify file formats or tool identity.", "muted"))
    statuses = check_tools(config, platform)
    colors = {"available": "success", "missing": "warning", "check failed": "error", "not applicable": "muted"}
    for status in statuses:
        label = style(status.status, colors.get(status.status, "plain"))
        print(f"{style(status.tool, 'name')}: {label} - {style(status.detail, 'muted')}")
        if status.guidance:
            print(style(f"  Install guidance (not executed): {status.guidance}", "muted"))
    return int(any(status.status in ("missing", "check failed") and not status.optional for status in statuses))


def main(argv: list[str] | None = None) -> int:
    parser = _ArgumentParser(
        prog="render", description="Open a local file with a configured external terminal viewer.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Commands:\n"
            "  config  Edit and validate configuration, then check renderer availability.\n"
            "  setup   Check configured renderer availability.\n"
            "  help    Show this help message.\n"
            "\n"
            "Examples:\n"
            "  render config\n"
            "  uv run render config\n"
            "  render --config custom-config.json config\n"
            "  render examples/sample.md\n"
            "\n"
            "Use -- before a filename beginning with '-'.\n"
            "Use ./setup, ./config, or ./help for files with reserved names."
        ),
    )
    # Python 3.14 adds its own help palette; keep one palette on every version.
    parser.color = False
    parser.add_argument("target", nargs="?", help="Local file path or a command listed below")
    parser.add_argument("--config", metavar="PATH", help="Use this JSON configuration instead of defaults")
    parser.add_argument("--show-config", action="store_true", help="Print the selected validated configuration and exit")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)
    if args.show_config and args.target is not None:
        parser.error("--show-config cannot be combined with a target")
    if args.target == "help":
        parser.print_help()
        return 0
    if args.target is None and not args.show_config:
        parser.error("a file, setup, config, or --show-config is required")
    try:
        platform = current_platform()
        if args.target == "config":
            config, source = edit_config(args.config)
            style = Style()
            print(f"{style('Configuration saved and validated:', 'success')} {style(source, 'muted')}")
            return _check_config(config, source, platform)
        config, source = load_config(args.config)
        if args.show_config:
            print(json.dumps(config, ensure_ascii=False, indent=2))
            return 0
        if args.target == "setup":
            return _check_config(config, source, platform)
        return render(config, args.target, platform)
    except (ConfigError, RenderError) as error:
        print(f"{Style(sys.stderr)('render:', 'error')} {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(f"{Style(sys.stderr)('render:', 'warning')} interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
