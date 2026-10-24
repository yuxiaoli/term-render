"""Specialized viewers take precedence over optional conversion fallbacks."""

import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from term_render.cli import main
from term_render.config import ConfigError, current_platform, validate_config
from term_render.runner import RenderError, check_tools, render, select_route


PLATFORMS = ("windows", "macos", "linux")


def fallback_config():
    def routes(tool, *, converts=False):
        route = {"tool": tool, "args": ["{file}"]}
        if converts:
            route.update(args=["{file}", "-o", "{output}"], output_extension=".md")
        return {platform: copy.deepcopy(route) for platform in PLATFORMS}

    return {
        "version": 1,
        "tools": {
            tool: {"platforms": {
                platform: {"executable": executable, "healthcheck": ["--help"], "install": f"Install {tool}."}
                for platform in PLATFORMS
            }} for tool, executable in (("primary", "specialized"), ("anydoc", "anydoc"), ("text", "text"))
        },
        "extensions": {".doc": routes("primary"), ".md": routes("text")},
        "fallbacks": {".doc": routes("anydoc", converts=True)},
    }


class FallbackDispatchTests(unittest.TestCase):
    def test_select_route_prefers_primary_and_uses_fallback_per_platform(self):
        config = fallback_config()
        for platform in PLATFORMS:
            with self.subTest(platform=platform):
                self.assertEqual(select_route(config, Path("REPORT.DOC"), platform)[0], "primary")
                del config["extensions"][".doc"][platform]
                self.assertEqual(select_route(config, Path("REPORT.DOC"), platform)[0], "anydoc")
        del config["extensions"][".doc"]
        self.assertEqual(select_route(config, Path("REPORT.DOC"), "windows")[0], "anydoc")

    def test_successful_primary_does_not_resolve_or_launch_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.doc"
            path.touch()
            with patch("term_render.runner.resolve_command", return_value=["specialized"]) as resolve, patch(
                "term_render.runner.subprocess.run", return_value=subprocess.CompletedProcess([], 0),
            ) as process, contextlib.redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(render(fallback_config(), str(path), "windows"), 0)
            resolve.assert_called_once_with("specialized")
            process.assert_called_once_with(["specialized", str(path.resolve())], shell=False, check=False)
            self.assertEqual(stderr.getvalue(), "")

    def test_missing_failed_and_unlaunchable_primary_use_conversion_fallback(self):
        for failure in ("missing", "exit", "launch"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "report.doc"
                path.write_bytes(b"unchanged input")
                outputs = []

                def resolve(executable):
                    return None if executable == "specialized" and failure == "missing" else [executable]

                def run(argv, **options):
                    self.assertEqual(options, {"shell": False, "check": False})
                    if argv[0] == "specialized":
                        if failure == "launch":
                            raise PermissionError("viewer denied")
                        return subprocess.CompletedProcess(argv, 7)
                    if argv[0] == "anydoc":
                        self.assertEqual(argv[1], str(path.resolve()))
                        output = Path(argv[-1])
                        output.write_text("# converted", encoding="utf-8")
                        outputs.append(output)
                        return subprocess.CompletedProcess(argv, 0)
                    self.assertEqual(argv, ["text", str(outputs[0].resolve())])
                    self.assertEqual(outputs[0].read_text(encoding="utf-8"), "# converted")
                    return subprocess.CompletedProcess(argv, 19)

                with patch("term_render.runner.resolve_command", side_effect=resolve), patch(
                    "term_render.runner.subprocess.run", side_effect=run,
                ), contextlib.redirect_stderr(io.StringIO()) as stderr:
                    self.assertEqual(render(fallback_config(), str(path), "windows"), 19)
                self.assertIn("fallback", stderr.getvalue().lower())
                self.assertEqual(len(outputs), 1)
                self.assertFalse(outputs[0].parent.exists())
                self.assertEqual(path.read_bytes(), b"unchanged input")

    def test_interrupt_status_and_keyboard_interrupt_never_trigger_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.doc"
            path.touch()
            for outcome in (130, -2, KeyboardInterrupt()):
                with self.subTest(outcome=outcome), patch(
                    "term_render.runner.resolve_command", return_value=["specialized"],
                ) as resolve, patch("term_render.runner.subprocess.run") as process:
                    if isinstance(outcome, BaseException):
                        process.side_effect = outcome
                        with self.assertRaises(KeyboardInterrupt):
                            render(fallback_config(), str(path), "windows")
                    else:
                        process.return_value = subprocess.CompletedProcess([], outcome)
                        self.assertEqual(render(fallback_config(), str(path), "windows"), 130)
                    resolve.assert_called_once_with("specialized")
                    process.assert_called_once()

    def test_fallback_only_conversion_can_render_through_a_fallback_output_route(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.doc"
            path.touch()
            config = fallback_config()
            config["fallbacks"][".md"] = config["extensions"].pop(".md")
            config["extensions"].pop(".doc")
            outputs = []

            def run(argv, **options):
                if argv[0] == "anydoc":
                    output = Path(argv[-1])
                    output.write_text("converted", encoding="utf-8")
                    outputs.append(output)
                else:
                    self.assertEqual(argv, ["text", str(outputs[0].resolve())])
                return subprocess.CompletedProcess(argv, 0)

            with patch("term_render.runner.resolve_command", side_effect=lambda executable: [executable]) as resolve, patch(
                "term_render.runner.subprocess.run", side_effect=run,
            ):
                self.assertEqual(render(config, str(path), "windows"), 0)
            self.assertEqual([call.args[0] for call in resolve.call_args_list], ["anydoc", "text"])
            self.assertFalse(outputs[0].parent.exists())

    def test_fallback_failure_propagates_once_without_retrying_primary(self):
        for failure in ("missing", "exit", "launch"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "report.doc"
                path.touch()
                resolved = []

                def resolve(executable):
                    resolved.append(executable)
                    return None if executable == "specialized" or failure == "missing" else [executable]

                def run(argv, **options):
                    if failure == "launch":
                        raise PermissionError("converter denied")
                    return subprocess.CompletedProcess(argv, 9)

                with patch("term_render.runner.resolve_command", side_effect=resolve), patch(
                    "term_render.runner.subprocess.run", side_effect=run,
                ), contextlib.redirect_stderr(io.StringIO()):
                    if failure == "exit":
                        self.assertEqual(render(fallback_config(), str(path), "windows"), 9)
                    else:
                        with self.assertRaises(RenderError):
                            render(fallback_config(), str(path), "windows")
                self.assertEqual(resolved, ["specialized", "anydoc"])

    def test_primary_converter_downstream_failure_does_not_retry_original_fallback(self):
        for failure in ("missing", "exit", "launch"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "report.doc"
                path.touch()
                config = fallback_config()
                config["extensions"][".doc"]["windows"].update(
                    args=["{file}", "{output}"], output_extension=".md",
                )
                resolved, outputs = [], []

                def resolve(executable):
                    resolved.append(executable)
                    return None if executable == "text" and failure == "missing" else [executable]

                def run(argv, **options):
                    if argv[0] == "specialized":
                        output = Path(argv[-1])
                        output.touch()
                        outputs.append(output)
                        return subprocess.CompletedProcess(argv, 0)
                    self.assertEqual(argv[0], "text")
                    if failure == "launch":
                        raise PermissionError("downstream denied")
                    return subprocess.CompletedProcess(argv, 8)

                with patch("term_render.runner.resolve_command", side_effect=resolve), patch(
                    "term_render.runner.subprocess.run", side_effect=run,
                ):
                    if failure == "exit":
                        self.assertEqual(render(config, str(path), "windows"), 8)
                    else:
                        with self.assertRaises(RenderError):
                            render(config, str(path), "windows")
                self.assertEqual(resolved, ["specialized", "text"])
                self.assertFalse(outputs[0].parent.exists())

    def test_fallback_does_not_make_unknown_or_unconfigured_formats_supported(self):
        config = fallback_config()
        with self.assertRaisesRegex(RenderError, "Unsupported extension"):
            select_route(config, Path("report.unknown"), "windows")
        del config["extensions"][".doc"]["windows"]
        del config["fallbacks"][".doc"]["windows"]
        with self.assertRaisesRegex(RenderError, "platform"):
            select_route(config, Path("report.doc"), "windows")

    def test_unvalidated_fallback_cycle_is_stopped_and_output_cleaned(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.doc"
            path.touch()
            config = fallback_config()
            config["extensions"] = {}
            config["fallbacks"][".md"] = copy.deepcopy(config["fallbacks"][".doc"])
            outputs = []

            def run(argv, **options):
                output = Path(argv[-1])
                output.touch()
                outputs.append(output)
                return subprocess.CompletedProcess(argv, 0)

            with patch("term_render.runner.resolve_command", return_value=["anydoc"]), patch(
                "term_render.runner.subprocess.run", side_effect=run,
            ), self.assertRaisesRegex(RenderError, "cycle"):
                render(config, str(path), "windows")
            self.assertLessEqual(len(outputs), 2)
            self.assertTrue(all(not output.parent.exists() for output in outputs))

    def test_real_fallback_converter_uses_normal_renderer_and_cleans_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "输入 & $() ; [report].doc"
            path.write_text("original", encoding="utf-8")
            converter = root / "convert.py"
            viewer = root / "view.py"
            record = root / "received.json"
            converter.write_text("import sys\nfrom pathlib import Path\n"
                                 "assert Path(sys.argv[1]).read_text(encoding='utf-8') == 'original'\n"
                                 "Path(sys.argv[2]).write_text('# converted',encoding='utf-8')\n", encoding="utf-8")
            viewer.write_text("import json,sys\nfrom pathlib import Path\n"
                              "p=Path(sys.argv[2])\n"
                              "Path(sys.argv[1]).write_text(json.dumps([str(p),p.read_text(encoding='utf-8')]),encoding='utf-8')\n"
                              "sys.exit(23)\n", encoding="utf-8")
            platform = current_platform()
            config = fallback_config()
            config["tools"]["primary"]["platforms"][platform]["executable"] = "render-test-missing-specialized-viewer"
            for tool in ("anydoc", "text"):
                config["tools"][tool]["platforms"][platform]["executable"] = sys.executable
            config["fallbacks"][".doc"][platform]["args"] = [str(converter), "{file}", "{output}"]
            config["extensions"][".md"][platform]["args"] = [str(viewer), str(record), "{file}"]
            validate_config(config)
            with contextlib.redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(render(config, str(path), platform), 23)
            output, contents = json.loads(record.read_text(encoding="utf-8"))
            self.assertEqual(contents, "# converted")
            self.assertFalse(Path(output).parent.exists())
            self.assertEqual(path.read_text(encoding="utf-8"), "original")
            self.assertIn("fallback", stderr.getvalue().lower())


class FallbackValidationTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.validator = Draft202012Validator(json.loads((root / "data/config.schema.json").read_text(encoding="utf-8")))

    def test_fallback_only_input_and_output_routes_are_valid(self):
        config = fallback_config()
        config["fallbacks"][".md"] = config["extensions"].pop(".md")
        config["extensions"].pop(".doc")
        self.assertIs(validate_config(config), config)
        self.validator.validate(config)
        self.assertEqual(select_route(config, Path("report.doc"), "windows")[0], "anydoc")
        self.assertEqual(select_route(config, Path("converted.md"), "windows")[0], "text")

    def test_invalid_fallback_shapes_and_placeholders_fail_schema_and_runtime(self):
        mutations = [
            lambda c: c.update(fallbacks=[]),
            lambda c: c["fallbacks"].update({"DOC": {"windows": {"tool": "anydoc", "args": ["{file}"]}}}),
            lambda c: c["fallbacks"][".doc"]["windows"].update(args=["{file}"]),
            lambda c: c["fallbacks"][".doc"]["windows"].update(args=["{file}", "--out={output}"]),
            lambda c: c["fallbacks"][".doc"]["windows"].update(output_extension=".html"),
            lambda c: c["fallbacks"][".doc"]["windows"].update(output_extension=None),
        ]
        for mutate in mutations:
            config = fallback_config()
            mutate(config)
            with self.subTest(config=config), self.assertRaises(ConfigError):
                validate_config(config)
            self.assertFalse(self.validator.is_valid(config))

    def test_invalid_fallback_references_output_routes_and_pending_overlap_fail_runtime(self):
        mutations = [
            lambda c: c["fallbacks"][".doc"]["windows"].update(tool="unknown"),
            lambda c: c["tools"]["anydoc"]["platforms"].pop("windows"),
            lambda c: c["extensions"][".md"].pop("windows"),
            lambda c: c.update(pending={".doc": "already has a fallback"}),
        ]
        for mutate in mutations:
            config = fallback_config()
            config["extensions"].pop(".doc")
            mutate(config)
            with self.subTest(config=config), self.assertRaises(ConfigError):
                validate_config(config)

    def test_cycle_across_primary_and_fallback_edges_is_rejected(self):
        config = fallback_config()
        config["fallbacks"][".md"] = copy.deepcopy(config["fallbacks"][".doc"])
        config["fallbacks"][".md"]["windows"]["output_extension"] = ".txt"
        config["extensions"][".txt"] = copy.deepcopy(config["fallbacks"][".doc"])
        with self.assertRaisesRegex(ConfigError, "cycle"):
            validate_config(config)


class OptionalFallbackToolTests(unittest.TestCase):
    def test_fallback_only_tools_are_checked_once_and_marked_optional(self):
        config = fallback_config()
        config["fallbacks"][".ppt"] = copy.deepcopy(config["fallbacks"][".doc"])
        with patch("term_render.runner.resolve_command", side_effect=lambda executable: None if executable == "anydoc" else [executable]), patch(
            "term_render.runner.subprocess.run", return_value=subprocess.CompletedProcess([], 0),
        ) as process:
            statuses = {status.tool: status for status in check_tools(config, "windows")}
        self.assertEqual(statuses["anydoc"].status, "missing")
        self.assertTrue(statuses["anydoc"].optional)
        self.assertFalse(statuses["primary"].optional)
        self.assertFalse(statuses["text"].optional)
        self.assertEqual(process.call_count, 2)
        config["extensions"][".doc"]["windows"] = copy.deepcopy(config["fallbacks"][".doc"]["windows"])
        with patch("term_render.runner.resolve_command", return_value=None):
            statuses = {status.tool: status for status in check_tools(config, "windows")}
        self.assertFalse(statuses["anydoc"].optional)

    def test_setup_and_config_ignore_optional_failures_but_report_them(self):
        config = fallback_config()
        for failure in ("missing", "check failed"):
            for arguments in (["setup"], ["config"]):
                def resolve(executable):
                    return None if executable == "anydoc" and failure == "missing" else [executable]

                def run(argv, **options):
                    return subprocess.CompletedProcess(argv, 7 if argv[0] == "anydoc" else 0)

                with self.subTest(arguments=arguments, failure=failure), patch(
                    "term_render.cli.current_platform", return_value="windows",
                ), patch("term_render.cli.load_config", return_value=(config, "fallback config")), patch(
                    "term_render.cli.edit_config", return_value=(config, "fallback config"),
                ), patch("term_render.runner.resolve_command", side_effect=resolve,
                ), patch("term_render.runner.subprocess.run", side_effect=run), contextlib.redirect_stdout(io.StringIO()) as stdout:
                    self.assertEqual(main(arguments), 0)
                self.assertIn("anydoc", stdout.getvalue())
                self.assertIn(failure, stdout.getvalue())
        with patch("term_render.cli.current_platform", return_value="windows"), patch(
            "term_render.cli.load_config", return_value=(config, "fallback config"),
        ), patch("term_render.runner.resolve_command", return_value=None), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["setup"]), 1)
