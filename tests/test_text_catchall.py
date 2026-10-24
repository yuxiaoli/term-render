"""Unmapped file types reuse the current-platform .txt route without renaming."""

import contextlib
import io
import json
from importlib.resources import files
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from term_render.config import ConfigError, current_platform, validate_config
from term_render.runner import RenderError, render, select_route


PLATFORMS = ("windows", "macos", "linux")


def text_config():
    return {
        "version": 1,
        "tools": {
            tool: {"platforms": {
                platform: {"executable": executable, "healthcheck": ["--help"], "install": f"Install {tool}."}
                for platform in PLATFORMS
            }} for tool, executable in (("plain", "custom-text"), ("special", "specialized"), ("backup", "backup"))
        },
        "extensions": {
            ".txt": {platform: {"tool": "plain", "args": ["--literal", "{file}"]} for platform in PLATFORMS},
            ".md": {platform: {"tool": "special", "args": ["{file}"]} for platform in PLATFORMS},
        },
        "pending": {".pdf": "No specialized renderer configured."},
    }


class TextCatchallTests(unittest.TestCase):
    def test_defaults_use_bat_for_unknown_extensionless_dotfiles_and_final_unknown_suffix(self):
        config = json.loads(files("term_render.configs").joinpath("default-config.json").read_text(encoding="utf-8"))
        for platform in PLATFORMS:
            for filename in ("report.unknown", "README", ".env", "report.md.unknown"):
                with self.subTest(platform=platform, filename=filename):
                    path = Path(filename)
                    tool, command, args = select_route(config, path, platform)
                    self.assertEqual(tool, "bat")
                    self.assertEqual(command["executable"], "bat")
                    self.assertEqual(args, ["--paging=never", "--", str(path)])

    def test_exact_platform_routes_and_fallbacks_win_before_text_catchall(self):
        config = text_config()
        self.assertEqual(select_route(config, Path("report.MD"), "windows")[0], "special")
        config["fallbacks"] = {".custom": {"windows": {"tool": "backup", "args": ["{file}"]}}}
        self.assertEqual(select_route(config, Path("report.custom"), "windows")[0], "backup")
        self.assertEqual(select_route(config, Path("report.custom"), "linux")[0], "plain")
        del config["extensions"][".md"]["windows"]
        self.assertEqual(select_route(config, Path("report.md"), "windows")[0], "plain")
        self.assertEqual(select_route(config, Path("report.md"), "linux")[0], "special")
        config["fallbacks"][".md"] = {"windows": {"tool": "backup", "args": ["{file}"]}}
        self.assertEqual(select_route(config, Path("report.md"), "windows")[0], "backup")
        self.assertEqual(select_route(config, Path("report.pdf"), "windows")[0], "plain")

    def test_custom_text_primary_and_fallback_receive_the_original_absolute_path(self):
        for fallback_only in (False, True):
            with self.subTest(fallback_only=fallback_only), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "输入 & $() ; [report].unknown"
                path.write_bytes(b"unchanged original")
                config = text_config()
                config["fallbacks"] = {".txt": {"windows": {"tool": "backup", "args": ["--backup", "{file}"]}}}
                if fallback_only:
                    config["extensions"].pop(".txt")
                calls = []

                def run(argv, **options):
                    self.assertEqual(options, {"shell": False, "check": False})
                    self.assertEqual(argv[-1], str(path.resolve()))
                    self.assertEqual(Path(argv[-1]).suffix, ".unknown")
                    calls.append(argv)
                    return subprocess.CompletedProcess(argv, 7 if argv[0] == "custom-text" else 0)

                with patch("term_render.runner.resolve_command", side_effect=lambda executable: [executable]), patch(
                    "term_render.runner.subprocess.run", side_effect=run,
                ), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(render(config, str(path), "windows"), 0)
                expected = [["backup", "--backup", str(path.resolve())]]
                if not fallback_only:
                    expected.insert(0, ["custom-text", "--literal", str(path.resolve())])
                self.assertEqual(calls, expected)
                self.assertEqual(path.read_bytes(), b"unchanged original")
                self.assertEqual(list(path.parent.iterdir()), [path])

    def test_exact_renderer_failures_do_not_switch_to_text_catchall(self):
        for failure in ("missing", "exit", "launch", "explicit fallback"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "report.md"
                path.touch()
                config = text_config()
                if failure == "explicit fallback":
                    config["fallbacks"] = {".md": {"windows": {"tool": "backup", "args": ["{file}"]}}}
                resolved = []

                def resolve(executable):
                    resolved.append(executable)
                    return None if failure == "missing" else [executable]

                def run(argv, **options):
                    if failure == "launch":
                        raise PermissionError("viewer denied")
                    return subprocess.CompletedProcess(argv, 7 if argv[0] == "specialized" else 9)

                with patch("term_render.runner.resolve_command", side_effect=resolve), patch(
                    "term_render.runner.subprocess.run", side_effect=run,
                ), contextlib.redirect_stderr(io.StringIO()):
                    if failure in ("missing", "launch"):
                        with self.assertRaises(RenderError):
                            render(config, str(path), "windows")
                    else:
                        self.assertEqual(render(config, str(path), "windows"), 9 if failure == "explicit fallback" else 7)
                self.assertEqual(resolved, ["specialized", "backup"] if failure == "explicit fallback" else ["specialized"])

    def test_configs_without_applicable_text_route_preserve_existing_errors(self):
        for text_mode in ("absent", "other platform"):
            config = text_config()
            if text_mode == "absent":
                config["extensions"].pop(".txt")
            else:
                del config["extensions"][".txt"]["windows"]
            del config["extensions"][".md"]["windows"]
            for filename, message in (("report.unknown", "Unsupported extension"), ("README", r"\(none\)"),
                                      ("report.pdf", "Pending"), ("report.md", "platform")):
                with self.subTest(text_mode=text_mode, filename=filename), self.assertRaisesRegex(RenderError, message):
                    select_route(config, Path(filename), "windows")

    def test_real_python_text_viewer_receives_original_file_and_preserves_contents(self):
        for filename in ("输入 & $() ; [report].unknown", "README", ".env"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / filename
                path.write_text("original Unicode 内容", encoding="utf-8")
                script = root / "viewer.py"
                record = root / "received.json"
                script.write_text("import json,sys\nfrom pathlib import Path\n"
                                  "p=Path(sys.argv[2])\n"
                                  "Path(sys.argv[1]).write_text(json.dumps([str(p),p.read_text(encoding='utf-8')]),encoding='utf-8')\n"
                                  "sys.exit(23)\n", encoding="utf-8")
                platform = current_platform()
                config = text_config()
                config["tools"]["plain"]["platforms"][platform]["executable"] = sys.executable
                config["extensions"][".txt"][platform]["args"] = [str(script), str(record), "{file}"]
                validate_config(config)
                self.assertEqual(render(config, str(path), platform), 23)
                received, contents = json.loads(record.read_text(encoding="utf-8"))
                self.assertEqual(received, str(path.resolve()))
                self.assertEqual(contents, "original Unicode 内容")
                self.assertEqual(path.read_text(encoding="utf-8"), contents)
                self.assertEqual({item.name for item in root.iterdir()}, {filename, "viewer.py", "received.json"})

    def test_converter_output_can_use_text_catchall_and_is_cleaned(self):
        for fallback_only in (False, True):
            with self.subTest(fallback_only=fallback_only), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "report.doc"
                path.touch()
                config = text_config()
                config["extensions"].pop(".md")
                config["extensions"][".doc"] = {"windows": {
                    "tool": "special", "args": ["{file}", "{output}"], "output_extension": ".md",
                }}
                if fallback_only:
                    config["fallbacks"] = {".txt": config["extensions"].pop(".txt")}
                validate_config(config)
                outputs = []

                def run(argv, **options):
                    if argv[0] == "specialized":
                        output = Path(argv[-1])
                        output.write_text("# converted", encoding="utf-8")
                        outputs.append(output)
                        self.assertEqual(output.suffix, ".md")
                    else:
                        self.assertEqual(argv, ["custom-text", "--literal", str(outputs[0].resolve())])
                        self.assertEqual(outputs[0].read_text(encoding="utf-8"), "# converted")
                    return subprocess.CompletedProcess(argv, 0 if argv[0] == "specialized" else 17)

                with patch("term_render.runner.resolve_command", side_effect=lambda executable: [executable]), patch(
                    "term_render.runner.subprocess.run", side_effect=run,
                ):
                    self.assertEqual(render(config, str(path), "windows"), 17)
                self.assertEqual(len(outputs), 1)
                self.assertFalse(outputs[0].parent.exists())

    def test_text_catchall_alias_conversion_cycles_are_rejected_and_stopped_at_runtime(self):
        config = text_config()
        config["extensions"].pop(".md")
        config["extensions"][".txt"]["windows"] = {
            "tool": "special", "args": ["{file}", "{output}"], "output_extension": ".md",
        }
        with self.assertRaisesRegex(ConfigError, "cycle"):
            validate_config(config)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.unknown"
            path.touch()
            outputs = []

            def run(argv, **options):
                output = Path(argv[-1])
                output.touch()
                outputs.append(output)
                return subprocess.CompletedProcess(argv, 0)

            with patch("term_render.runner.resolve_command", return_value=["specialized"]), patch(
                "term_render.runner.subprocess.run", side_effect=run,
            ), self.assertRaisesRegex(RenderError, "cycle"):
                render(config, str(path), "windows")
            self.assertLessEqual(len(outputs), 2)
            self.assertTrue(all(not output.parent.exists() for output in outputs))
