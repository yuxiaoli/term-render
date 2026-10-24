"""Behavior checks for the shipped Bat, viu, and anydoc integrations."""

import copy
import json
import subprocess
import tempfile
import unittest
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from term_render.runner import RenderError, check_tools, render, select_route


class DefaultViewerTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(
            files("term_render.configs").joinpath("default-config.json").read_text(encoding="utf-8")
        )

    def test_windows_images_skip_terminal_graphics_detection(self):
        for extension in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".ico"):
            with self.subTest(extension=extension):
                _, _, args = select_route(self.config, Path("image" + extension), "windows")
                self.assertIn("--blocks", args[:args.index("--")])

    def test_bat_text_and_source_routes_do_not_require_a_pager(self):
        for platform in ("windows", "macos", "linux"):
            for extension in (".TXT", ".log", ".py", ".js", ".ts", ".sh", ".ps1"):
                with self.subTest(platform=platform, extension=extension):
                    path = Path("source with spaces" + extension)
                    tool, command, args = select_route(self.config, path, platform)
                    self.assertEqual(tool, "bat")
                    self.assertEqual(command["executable"], "bat")
                    self.assertEqual(args, ["--paging=never", "--", str(path)])
                    self.assertEqual(command["healthcheck"], ["--version"])

    def test_viu_images_request_a_single_gif_loop(self):
        for platform in ("windows", "macos", "linux"):
            for extension in (".PNG", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".ico"):
                with self.subTest(platform=platform, extension=extension):
                    path = Path("image with spaces" + extension)
                    tool, command, args = select_route(self.config, path, platform)
                    self.assertEqual(tool, "viu")
                    self.assertEqual(command["executable"], "viu")
                    expected = ["--once", "--", str(path)] if extension == ".gif" else ["--", str(path)]
                    if platform == "windows":
                        expected.insert(0, "--blocks")
                    self.assertEqual(args, expected)
                    self.assertEqual(command["healthcheck"], ["--version"])

    def test_new_viewers_keep_literal_paths_streams_and_exit_status(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension, executable, args in (
                (".txt", "bat.exe", ["--paging=never", "--"]),
                (".png", "viu.exe", ["--blocks", "--"]),
                (".gif", "viu.exe", ["--blocks", "--once", "--"]),
            ):
                with self.subTest(extension=extension):
                    path = Path(directory) / ("-Unicode 测试 & $() ; [file]" + extension)
                    original = b"original viewer input"
                    path.write_bytes(original)
                    with patch("term_render.runner.resolve_executable", return_value=executable), patch(
                        "term_render.runner.subprocess.run",
                        return_value=subprocess.CompletedProcess([], 7),
                    ) as run:
                        self.assertEqual(render(self.config, str(path), "windows"), 7)
                        run.assert_called_once_with(
                            [executable, *args, str(path.resolve())], shell=False, check=False
                        )
                    self.assertEqual(path.read_bytes(), original)

    def test_setup_includes_bat_and_viu_and_actionable_missing_guidance(self):
        with patch("term_render.runner.resolve_executable", return_value=None):
            statuses = {status.tool: status for status in check_tools(self.config, "linux")}
        for tool, source in (("bat", "https://github.com/sharkdp/bat"), ("viu", "https://github.com/atanunq/viu")):
            with self.subTest(tool=tool):
                self.assertEqual(statuses[tool].status, "missing")
                self.assertIn(source, statuses[tool].guidance)
        self.assertIn("batcat", statuses["bat"].guidance)

    def test_missing_image_viewer_does_not_attempt_to_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.png"
            path.touch()
            with patch("term_render.runner.resolve_executable", return_value=None), patch(
                "term_render.runner.subprocess.run"
            ) as run, self.assertRaisesRegex(RenderError, "Missing tool viu:.*cargo install viu"):
                render(self.config, str(path), "windows")
            run.assert_not_called()

    def test_anydoc_fallback_document_routes_produce_markdown_for_configured_renderer(self):
        for platform in ("windows", "macos", "linux"):
            for extension in (
                ".DOC", ".docm", ".ppt", ".pps", ".pot", ".pptx", ".pptm", ".ppsx", ".ppsm",
                ".xls", ".xlsm", ".xlsb", ".odt", ".ods", ".odp", ".rtf", ".epub",
            ):
                with self.subTest(platform=platform, extension=extension):
                    path = Path("document with spaces" + extension)
                    tool, command, args = select_route(self.config, path, platform)
                    self.assertEqual(tool, "anydoc")
                    self.assertEqual(command["executable"], "anydoc")
                    self.assertEqual(command["healthcheck"], ["--help"])
                    self.assertIn("npm install -g @firecrawl/anydoc", command["install"])
                    self.assertEqual(args, [str(path), "-o", "{output}"])
                    self.assertEqual(
                        self.config["fallbacks"][extension.lower()][platform]["output_extension"], ".md"
                    )
                    self.assertNotIn(extension.lower(), self.config["extensions"])
                    self.assertNotIn(extension.lower(), self.config["pending"])
            markdown_tool, _, _ = select_route(self.config, Path("converted.md"), platform)
            self.assertEqual(markdown_tool, "glow")


    def test_direct_document_viewers_are_preserved_with_anydoc_fallback(self):
        for platform in ("windows", "macos", "linux"):
            for extension, expected in ((".docx", "doxx"), (".xlsx", "xleak"), (".pdf", "pdfly"), (".csv", "tidy-viewer")):
                with self.subTest(platform=platform, extension=extension):
                    tool, _, _ = select_route(self.config, Path("report" + extension), platform)
                    self.assertEqual(tool, expected)
                    fallback = self.config["fallbacks"][extension][platform]
                    self.assertEqual(fallback["tool"], "anydoc")
                    self.assertEqual(fallback["output_extension"], ".md")
                    self.assertEqual(fallback["args"], ["{file}", "-o", "{output}"])
        self.assertTrue(all(
            route["tool"] != "anydoc"
            for routes in self.config["extensions"].values()
            for route in routes.values()
        ))
        self.assertEqual(self.config["pending"], {})

    def test_config_schemas_allow_optional_fallback_routes(self):
        root = Path(__file__).resolve().parents[1]
        for filename in ("config.schema.json", "default-config.schema.json"):
            with self.subTest(schema=filename):
                schema = json.loads((root / "data" / filename).read_text(encoding="utf-8"))
                Draft202012Validator.check_schema(schema)
                validator = Draft202012Validator(schema)
                validator.validate(self.config)
                primary_only = copy.deepcopy(self.config)
                del primary_only["fallbacks"]
                validator.validate(primary_only)
                malformed = copy.deepcopy(self.config)
                malformed["fallbacks"][".pdf"]["windows"]["unknown"] = True
                self.assertFalse(validator.is_valid(malformed))
