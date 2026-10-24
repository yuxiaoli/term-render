"""Conversion routes use private temporary output and the normal render dispatch."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from term_render.cli import main
from term_render.config import ConfigError, current_platform, validate_config
from term_render.runner import RenderError, check_tools, render, resolve_command
from test_config import fixture


def conversion_config(output_extension=".md"):
    config = fixture()
    if output_extension == ".txt":
        config["extensions"][".txt"] = copy.deepcopy(config["extensions"][".md"])
    config["tools"]["anydoc"] = copy.deepcopy(config["tools"]["viewer"])
    for command in config["tools"]["anydoc"]["platforms"].values():
        command["executable"] = "anydoc"
    config["extensions"][".doc"] = {
        platform: {"tool": "anydoc", "args": ["{file}", "-o", "{output}"],
                   "output_extension": output_extension}
        for platform in ("windows", "macos", "linux")
    }
    return config


class ConversionTests(unittest.TestCase):
    def test_converted_markdown_and_text_are_dispatched_then_cleaned(self):
        for suffix in (".md", ".txt"):
            for platform in ("windows", "macos", "linux"):
                with self.subTest(suffix=suffix, platform=platform), tempfile.TemporaryDirectory() as directory:
                    original = Path(directory) / "输入 & $() ; [report].DOC"
                    original.write_bytes(b"original document")
                    converted = []

                    def run(argv, **options):
                        self.assertEqual(options, {"shell": False, "check": False})
                        if argv[0] == "anydoc":
                            self.assertEqual(argv[1], str(original.resolve()))
                            self.assertEqual(argv[2], "-o")
                            output = Path(argv[3])
                            self.assertEqual(output.suffix, suffix)
                            self.assertNotEqual(output.parent, original.parent)
                            output.write_text("# converted", encoding="utf-8")
                            converted.append(output)
                            return subprocess.CompletedProcess(argv, 0)
                        self.assertEqual(argv, ["viewer", str(converted[0].resolve())])
                        self.assertEqual(converted[0].read_text(encoding="utf-8"), "# converted")
                        return subprocess.CompletedProcess(argv, 17)

                    with patch("term_render.runner.resolve_executable", side_effect=lambda value: value), patch(
                        "term_render.runner.subprocess.run", side_effect=run,
                    ) as process:
                        self.assertEqual(render(conversion_config(suffix), str(original), platform), 17)
                    self.assertEqual(process.call_count, 2)
                    self.assertFalse(converted[0].parent.exists())
                    self.assertEqual(original.read_bytes(), b"original document")

    def test_failed_conversion_does_not_launch_viewer_and_cleans_output(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "report.doc"
            original.touch()
            converted = []

            def run(argv, **options):
                output = Path(argv[-1])
                output.write_text("partial", encoding="utf-8")
                converted.append(output)
                return subprocess.CompletedProcess(argv, 9)

            with patch("term_render.runner.resolve_executable", side_effect=lambda value: value), patch(
                "term_render.runner.subprocess.run", side_effect=run,
            ) as process:
                self.assertEqual(render(conversion_config(), str(original), "windows"), 9)
            process.assert_called_once()
            self.assertFalse(converted[0].parent.exists())

    def test_missing_output_and_viewer_error_are_actionable_and_cleaned(self):
        for missing_output in (True, False):
            with self.subTest(missing_output=missing_output), tempfile.TemporaryDirectory() as directory:
                original = Path(directory) / "report.doc"
                original.touch()
                outputs = []

                def run(argv, **options):
                    if argv[0] == "viewer":
                        raise PermissionError("viewer denied")
                    output = Path(argv[-1])
                    outputs.append(output)
                    if not missing_output:
                        output.touch()
                    return subprocess.CompletedProcess(argv, 0)

                expected = "did not create" if missing_output else "Cannot launch renderer viewer"
                with patch("term_render.runner.resolve_executable", side_effect=lambda value: value), patch(
                    "term_render.runner.subprocess.run", side_effect=run,
                ), self.assertRaisesRegex(RenderError, expected):
                    render(conversion_config(), str(original), "windows")
                self.assertFalse(outputs[0].parent.exists())

    def test_real_converter_then_renderer_preserves_input_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "测试 & $() [original].doc"
            original.write_text("original", encoding="utf-8")
            converter = root / "converter.py"
            converter.write_text(
                "import sys\nfrom pathlib import Path\n"
                "assert Path(sys.argv[1]).read_text(encoding='utf-8') == 'original'\n"
                "Path(sys.argv[3]).write_text('# Converted 测试',encoding='utf-8')\n",
                encoding="utf-8",
            )
            viewer = root / "viewer.py"
            record = root / "record.json"
            viewer.write_text(
                "import json,sys\nfrom pathlib import Path\n"
                "path=Path(sys.argv[2])\n"
                "Path(sys.argv[1]).write_text(json.dumps([str(path),path.read_text(encoding='utf-8')]),encoding='utf-8')\n"
                "sys.exit(19)\n", encoding="utf-8",
            )
            config = conversion_config()
            platform = current_platform()
            for tool in config["tools"].values():
                for command in tool["platforms"].values():
                    command["executable"] = sys.executable
            config["extensions"][".doc"][platform]["args"].insert(0, str(converter))
            config["extensions"][".md"][platform]["args"] = [str(viewer), str(record), "{file}"]
            selected = root / "config.json"
            selected.write_text(json.dumps(config), encoding="utf-8")
            self.assertEqual(main(["--config", str(selected), str(original)]), 19)
            output_path, content = json.loads(record.read_text(encoding="utf-8"))
            self.assertEqual(content, "# Converted 测试")
            self.assertFalse(Path(output_path).parent.exists())
            self.assertEqual(original.read_text(encoding="utf-8"), "original")


class ConversionValidationTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.validator = Draft202012Validator(json.loads((root / "data/config.schema.json").read_text(encoding="utf-8")))

    def test_valid_conversion_routes_match_schema(self):
        for suffix in (".md", ".txt"):
            config = conversion_config(suffix)
            self.assertIs(validate_config(config), config)
            self.validator.validate(config)

    def test_bad_conversion_args_and_suffixes_rejected_by_schema_and_runtime(self):
        changes = [
            {"output_extension": ".html"}, {"output_extension": None},
            {"args": ["{file}", "-o"]},
            {"args": ["{file}", "{output}", "{output}"]},
            {"args": ["{file}", "--out={output}"]},
            {"args": ["{output}"]},
        ]
        for change in changes:
            config = conversion_config()
            config["extensions"][".doc"]["windows"].update(change)
            with self.subTest(change=change), self.assertRaises(ConfigError):
                validate_config(config)
            self.assertFalse(self.validator.is_valid(config))
        config = fixture()
        config["extensions"][".md"]["windows"]["args"].append("{output}")
        with self.assertRaises(ConfigError):
            validate_config(config)
        self.assertFalse(self.validator.is_valid(config))

    def test_missing_output_renderer_and_cycles_are_rejected(self):
        config = conversion_config()
        del config["extensions"][".md"]["windows"]
        with self.assertRaisesRegex(ConfigError, "no renderer"):
            validate_config(config)
        config = conversion_config(".txt")
        config["extensions"][".txt"]["windows"].update(
            output_extension=".md", args=["{file}", "{output}"],
        )
        config["extensions"][".md"]["windows"].update(
            output_extension=".txt", args=["{file}", "{output}"],
        )
        with self.assertRaisesRegex(ConfigError, "conversion cycle"):
            validate_config(config)


class NpmAnyDocTests(unittest.TestCase):
    def test_windows_anydoc_finds_standard_node_install_without_node_or_npm_on_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            appdata = root / "AppData" / "Roaming"
            script = appdata / "npm" / "node_modules" / "@firecrawl" / "anydoc" / "cli.js"
            script.parent.mkdir(parents=True)
            script.touch()
            program_files = root / "Program Files"
            node = program_files / "nodejs" / "node.exe"
            node.parent.mkdir(parents=True)
            node.touch()
            with (
                patch.dict(os.environ, {"APPDATA": str(appdata), "ProgramFiles": str(program_files)}, clear=True),
                patch("term_render.runner.sys.platform", "win32"),
                patch("shutil.which", return_value=None),
            ):
                self.assertEqual(resolve_command("anydoc"), [str(node), str(script)])

    def test_windows_npm_user_install_is_found_without_npm_on_path(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "npm"
            script = prefix / "node_modules" / "@firecrawl" / "anydoc" / "cli.js"
            script.parent.mkdir(parents=True)
            script.touch()
            node = Path(directory) / "node.exe"
            node.touch()
            with (
                patch.dict(os.environ, {"APPDATA": directory}),
                patch("term_render.runner.sys.platform", "win32"),
                patch("shutil.which", side_effect=lambda value: str(node) if value == "node" else None),
            ):
                self.assertEqual(resolve_command("anydoc"), [str(node), str(script)])

    def test_windows_npm_wrapper_uses_native_node_and_known_script(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            wrapper = prefix / "anydoc.cmd"
            wrapper.touch()
            script = prefix / "node_modules" / "@firecrawl" / "anydoc" / "cli.js"
            script.parent.mkdir(parents=True)
            script.touch()
            node = prefix / "node.exe"
            node.touch()
            with patch("term_render.runner.sys.platform", "win32"), patch("shutil.which", return_value=str(wrapper)):
                self.assertEqual(resolve_command("anydoc"), [str(node), str(script)])
                config = conversion_config()
                with patch("term_render.runner.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run:
                    statuses = check_tools(config, "windows")
                self.assertEqual(next(s.status for s in statuses if s.tool == "anydoc"), "available")
                run.assert_called_once_with(
                    [str(node), str(script), "--help"], shell=False, check=False,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5.0,
                )
            node.unlink()
            with patch("term_render.runner.sys.platform", "win32"), patch(
                "shutil.which", side_effect=lambda value: str(wrapper) if value == "anydoc" else "native-node.exe",
            ):
                self.assertEqual(resolve_command("anydoc"), ["native-node.exe", str(script)])

    def test_arbitrary_batch_or_incomplete_anydoc_install_is_rejected(self):
        for name in ("other.cmd", "anydoc.bat", "anydoc.cmd"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                wrapper = Path(directory) / name
                wrapper.touch()
                with patch("term_render.runner.sys.platform", "win32"), patch("shutil.which", return_value=str(wrapper)):
                    self.assertIsNone(resolve_command("anydoc"))
                    self.assertIsNone(resolve_command("viewer"))
