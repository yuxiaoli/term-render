import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch

from test_config import fixture

from term_render.runner import (
    RenderError,
    check_tools,
    render,
    resolve_executable,
    resolve_file,
    select_route,
)


class RunnerTests(unittest.TestCase):
    def test_case_insensitive_extension_and_distinct_platform_tools(self):
        config = fixture()
        config["tools"]["other"] = {"platforms": {"macos": {
            "executable": "other", "healthcheck": ["--help"], "install": "Install other."
        }}}
        config["extensions"][".md"]["macos"]["tool"] = "other"
        for platform, tool in [("windows", "viewer"), ("linux", "viewer"), ("macos", "other")]:
            with self.subTest(platform=platform):
                selected, command, args = select_route(config, Path("UPPER.MD"), platform)
                self.assertEqual(selected, tool)
                self.assertEqual(command["executable"], tool)
                self.assertEqual(args, ["UPPER.MD"])

    def test_unsupported_extension_pending_and_platform(self):
        for filename, message in [("file.pdf", "Pending"), ("file.zzz", "Unsupported extension"),
                                  ("no_extension", "(none)")]:
            with self.subTest(filename=filename), self.assertRaisesRegex(RenderError, message):
                select_route(fixture(), Path(filename), "windows")
        with self.assertRaisesRegex(RenderError, "platform"):
            select_route(fixture(), Path("file.md"), "freebsd")

    def test_final_extension_only(self):
        with self.assertRaisesRegex(RenderError, "Unsupported extension"):
            select_route(fixture(), Path("file.md.exe"), "windows")

    def test_file_resolution_and_missing_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Unicode 测试 & $() ; [file].md"
            path.write_text("# Original", encoding="utf-8")
            self.assertEqual(resolve_file(str(path)), path.resolve())
            self.assertEqual(resolve_file(str(path.parent / "." / path.name)), path.resolve())
            with self.assertRaisesRegex(RenderError, "regular file"):
                resolve_file(directory)
            with self.assertRaisesRegex(RenderError, "Cannot access file"):
                resolve_file(str(path.parent / "missing.md"))
            with self.assertRaises(RenderError):
                resolve_file("bad\0.md")

    def test_home_expansion(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"HOME": directory, "USERPROFILE": directory}):
            path = Path(directory) / "example.md"
            path.touch()
            self.assertEqual(resolve_file("~/example.md"), path.resolve())

    def test_literal_argv_inherited_streams_and_exit_status(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "-测试 & $() ; [file].md"
            path.write_text("# Original", encoding="utf-8")
            config = fixture()
            config["extensions"][".md"]["windows"]["args"] = ["--label", "$literal; &", "{file}"]
            for code, expected in [(0, 0), (7, 7), (-15, 143), (-2, 130)]:
                with patch("term_render.runner.resolve_executable", return_value="native.exe"), patch("term_render.runner.subprocess.run", return_value=subprocess.CompletedProcess([], code)) as run:
                    self.assertEqual(render(config, str(path), "windows"), expected)
                    run.assert_called_once_with(["native.exe", "--label", "$literal; &", str(path.resolve())], shell=False, check=False)
            self.assertEqual(path.read_text(encoding="utf-8"), "# Original")

    def test_missing_tool_and_launch_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "file.md"
            path.touch()
            with patch("term_render.runner.resolve_executable", return_value=None), self.assertRaisesRegex(RenderError, "Install viewer manually"):
                render(fixture(), str(path), "windows")
            with patch("term_render.runner.resolve_executable", return_value="viewer"), patch("term_render.runner.subprocess.run", side_effect=PermissionError("denied")), self.assertRaisesRegex(RenderError, "Cannot launch"):
                render(fixture(), str(path), "windows")

    def test_interrupt_is_not_swallowed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "file.md"
            path.touch()
            with patch("term_render.runner.resolve_executable", return_value="viewer"), patch("term_render.runner.subprocess.run", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
                render(fixture(), str(path), "windows")

    def test_batch_files_rejected_even_when_found_via_path(self):
        with patch("shutil.which", return_value="C:/tools/viewer.cmd"):
            self.assertIsNone(resolve_executable("viewer"))
        with patch("shutil.which", return_value="C:/tools/viewer.exe"):
            self.assertEqual(resolve_executable("viewer"), "C:/tools/viewer.exe")

    def test_setup_deduplicates_shared_routes_and_inherits_no_tty(self):
        config = fixture()
        config["extensions"][".txt"] = config["extensions"][".md"]
        with patch("term_render.runner.resolve_executable", return_value="viewer.exe"), patch("term_render.runner.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run:
            statuses = check_tools(config, "windows")
            self.assertEqual([s.status for s in statuses], ["available"])
            run.assert_called_once_with(["viewer.exe", "--help"], shell=False, check=False,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5.0)

    def test_setup_skips_foreign_and_unused_tools(self):
        config = fixture()
        config["tools"]["foreign"] = {"platforms": {"linux": config["tools"]["viewer"]["platforms"]["linux"]}}
        config["tools"]["unused"] = config["tools"]["viewer"]
        with patch("term_render.runner.resolve_executable", return_value=None) as resolve:
            statuses = check_tools(config, "windows")
            self.assertEqual({s.tool: s.status for s in statuses}, {"foreign": "not applicable", "unused": "not applicable", "viewer": "missing"})
            resolve.assert_called_once_with("viewer")

    def test_setup_failed_exit_timeout_and_oserror(self):
        for outcome in [subprocess.CompletedProcess([], 9), subprocess.TimeoutExpired("viewer", 5), PermissionError("denied")]:
            run_patch = (
                patch("term_render.runner.subprocess.run", side_effect=outcome)
                if isinstance(outcome, Exception)
                else patch("term_render.runner.subprocess.run", return_value=outcome)
            )
            with self.subTest(outcome=outcome), patch("term_render.runner.resolve_executable", return_value="viewer.exe"), run_patch:
                status = check_tools(fixture(), "windows")[0]
                self.assertEqual(status.status, "check failed")
                self.assertIn("Install viewer", status.guidance)


class PdfRouteTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(
            files("term_render.configs").joinpath("default-config.json").read_text(encoding="utf-8")
        )

    def test_default_pdf_text_route_on_every_platform(self):
        path = Path("香港理工大学 & [report].PDF")
        for platform in ("windows", "macos", "linux"):
            with self.subTest(platform=platform):
                tool, command, args = select_route(self.config, path, platform)
                self.assertEqual(tool, "pdfly")
                self.assertEqual(command["executable"], "pdfly")
                self.assertEqual(args, ["extract-text", str(path)])
                self.assertEqual(command["healthcheck"], ["extract-text", "--help"])
                self.assertIn("uv tool install pdfly", command["install"])
        self.assertNotIn(".pdf", self.config["pending"])

    def test_pdf_dispatch_inherits_terminal_and_preserves_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "香港理工大学 & $() [report].PDF"
            original = b"%PDF-1.7\nsynthetic dispatch input"
            path.write_bytes(original)
            with patch("term_render.runner.resolve_executable", return_value="pdfly.exe"), patch(
                "term_render.runner.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0),
            ) as run:
                self.assertEqual(render(self.config, str(path), "windows"), 0)
                run.assert_called_once_with(
                    ["pdfly.exe", "extract-text", str(path.resolve())],
                    shell=False, check=False,
                )
            self.assertEqual(path.read_bytes(), original)

    def test_missing_pdf_tool_and_fallback_report_install_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.pdf"
            path.touch()
            stderr = io.StringIO()
            with (
                contextlib.redirect_stderr(stderr),
                patch("term_render.runner.resolve_command", return_value=None),
                self.assertRaisesRegex(RenderError, "Missing tool anydoc:.*npm install -g @firecrawl/anydoc"),
            ):
                render(self.config, str(path), "windows")
            self.assertIn("Missing tool pdfly:", stderr.getvalue())
            self.assertIn("uv tool install pdfly", stderr.getvalue())
