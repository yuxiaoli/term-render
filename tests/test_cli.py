import contextlib
import io
import json
import re
import unittest
from unittest.mock import patch

from term_render.cli import main
from term_render.config import ConfigError
from term_render.runner import RenderError, ToolStatus
from test_config import fixture


class CliTests(unittest.TestCase):
    def invoke(self, args, *, terminal=False, error_terminal=False, **mocks):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(patch.object(stdout, "isatty", return_value=terminal))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            stack.enter_context(patch.object(stderr, "isatty", return_value=error_terminal))
            stack.enter_context(patch("term_render.cli.current_platform", return_value="windows"))
            stack.enter_context(patch("term_render.cli.load_config", return_value=(fixture(), "test config")))
            for target, options in mocks.items():
                stack.enter_context(patch(f"term_render.cli.{target}", **options))
            code = main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_dispatch_preserves_child_exit(self):
        for code in [0, 19]:
            result, out, err = self.invoke(["file.md"], render={"return_value": code})
            self.assertEqual(result, code)
            self.assertEqual(out + err, "")

    def test_errors_and_interrupt(self):
        for error, code in [(ConfigError("bad config"), 2), (RenderError("cannot launch"), 2), (KeyboardInterrupt(), 130)]:
            result, out, err = self.invoke(["file.md"], render={"side_effect": error})
            self.assertEqual(result, code)
            self.assertIn("render:", err)
            self.assertNotIn("Traceback", err)

    def test_setup_reports_all_statuses_and_returns_failure(self):
        statuses = [ToolStatus("a", "available", "path"), ToolStatus("b", "missing", "missing", "manual install"),
                    ToolStatus("c", "check failed", "timeout"), ToolStatus("d", "not applicable", "foreign")]
        code, out, err = self.invoke(["setup"], check_tools={"return_value": statuses})
        self.assertEqual(code, 1)
        for word in ["available", "missing", "check failed", "not applicable", "manual install", "does not verify"]:
            self.assertIn(word, out)
        self.assertEqual(err, "")

    def test_setup_colors_statuses_and_subdues_guidance_in_a_terminal(self):
        statuses = [
            ToolStatus("good", "available", "viewer.exe", "Install viewer."),
            ToolStatus("absent", "missing", "viewer"),
            ToolStatus("broken", "check failed", "timeout"),
            ToolStatus("foreign", "not applicable", "other platform"),
        ]
        with (
            patch("term_render.cli.sys.platform", "linux"),
            patch.dict("os.environ", {"NO_COLOR": "", "TERM": "xterm-256color"}),
        ):
            code, out, err = self.invoke(["setup"], terminal=True, check_tools={"return_value": statuses})
        self.assertEqual(code, 1)
        self.assertIn("\x1b[38;2;105;255;148mavailable\x1b[0m", out)
        self.assertIn("\x1b[38;2;241;250;140mmissing\x1b[0m", out)
        self.assertIn("\x1b[31mcheck failed\x1b[0m", out)
        self.assertIn("\x1b[2mnot applicable\x1b[0m", out)
        self.assertIn("\x1b[2m  Install guidance (not executed): Install viewer.\x1b[0m", out)
        self.assertEqual(err, "")

    def test_check_output_stays_plain_when_redirected_or_color_is_disabled(self):
        statuses = [ToolStatus("viewer", "available", "viewer.exe", "Install viewer.")]
        for arguments in (["setup"], ["config"]):
            for terminal, environment in (
                (False, {"NO_COLOR": "", "TERM": "xterm-256color"}),
                (True, {"NO_COLOR": "1", "TERM": "xterm-256color"}),
                (True, {"NO_COLOR": "", "TERM": "dumb"}),
            ):
                with self.subTest(arguments=arguments, terminal=terminal, environment=environment), patch(
                    "term_render.cli.sys.platform", "linux",
                ), patch.dict("os.environ", environment):
                    code, out, err = self.invoke(
                        arguments, terminal=terminal, check_tools={"return_value": statuses},
                        edit_config={"return_value": (fixture(), "test config")},
                    )
                self.assertEqual(code, 0)
                self.assertNotIn("\x1b[", out)
                self.assertIn("viewer: available - viewer.exe", out)
                self.assertIn("Install guidance (not executed): Install viewer.", out)
                self.assertEqual(err, "")

    def test_setup_success_for_empty_or_available_tools(self):
        for statuses in [[], [ToolStatus("a", "available", "path")], [ToolStatus("a", "not applicable", "foreign")]]:
            self.assertEqual(self.invoke(["setup"], check_tools={"return_value": statuses})[0], 0)

    def test_show_config_is_valid_json(self):
        for terminal in (False, True):
            with self.subTest(terminal=terminal), patch("term_render.cli.sys.platform", "linux"), patch.dict(
                "os.environ", {"NO_COLOR": "", "TERM": "xterm-256color"},
            ):
                code, out, err = self.invoke(["--show-config"], terminal=terminal)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out), fixture())
            self.assertNotIn("\x1b[", out)
            self.assertEqual(err, "")

    def test_help_colors_preserve_layout_for_both_help_forms(self):
        for arguments in (["help"], ["--help"]):
            for width in (50, 100):
                outputs = []
                for terminal in (False, True):
                    output = io.StringIO()
                    with self.subTest(arguments=arguments, width=width, terminal=terminal), contextlib.redirect_stdout(output), patch.object(
                        output, "isatty", return_value=terminal,
                    ), patch("term_render.cli.sys.platform", "linux"), patch.dict(
                        "os.environ", {"NO_COLOR": "", "TERM": "xterm-256color", "COLUMNS": str(width)},
                    ):
                        if arguments == ["--help"]:
                            with self.assertRaises(SystemExit) as caught:
                                main(arguments)
                            self.assertEqual(caught.exception.code, 0)
                        else:
                            self.assertEqual(main(arguments), 0)
                    outputs.append(output.getvalue())
                plain, colored = outputs
                self.assertNotIn("\x1b[", plain)
                self.assertEqual(re.sub(r"\x1b\[[0-9;]*m", "", colored), plain)
                for heading in ("usage:", "options:", "Commands:", "Examples:"):
                    self.assertIn(f"\x1b[1;38;2;214;172;255m{heading}\x1b[0m", colored)
                self.assertIn("\x1b[1;38;2;255;146;223mrender\x1b[0m", colored)
                self.assertIn("\x1b[1;38;2;105;255;148mconfig\x1b[0m", colored)
                self.assertIn("\x1b[1;38;2;105;255;148mtarget\x1b[0m", colored)
                self.assertIn(
                    "\x1b[1;38;2;105;255;148m-h\x1b[0m, \x1b[1;38;2;139;233;253m--help\x1b[0m",
                    colored,
                )
                self.assertIn("\x1b[1;38;2;139;233;253m--config\x1b[0m \x1b[38;2;241;250;140mPATH\x1b[0m", colored)
                self.assertIn(
                    "\x1b[1;38;2;255;146;223muv\x1b[0m \x1b[1;38;2;105;255;148mrun\x1b[0m "
                    "\x1b[1;38;2;255;146;223mrender\x1b[0m \x1b[1;38;2;105;255;148mconfig\x1b[0m",
                    colored,
                )
                self.assertIn("\x1b[38;2;241;250;140mcustom-config.json\x1b[0m", colored)
                self.assertIn("\x1b[38;2;241;250;140mexamples/sample.md\x1b[0m", colored)

    def test_help_respects_no_color_and_dumb_terminals(self):
        for environment in ({"NO_COLOR": "1", "TERM": "xterm-256color"}, {"NO_COLOR": "", "TERM": "dumb"}):
            with self.subTest(environment=environment), patch("term_render.cli.sys.platform", "linux"), patch.dict(
                "os.environ", environment,
            ):
                code, out, err = self.invoke(["help"], terminal=True)
            self.assertEqual(code, 0)
            self.assertIn("Commands:", out)
            self.assertNotIn("\x1b[", out + err)

    def test_error_colors_follow_stderr_independently_of_stdout(self):
        for terminal in (False, True):
            for error_terminal in (False, True):
                with self.subTest(terminal=terminal, error_terminal=error_terminal), patch(
                    "term_render.cli.sys.platform", "linux",
                ), patch.dict("os.environ", {"NO_COLOR": "", "TERM": "xterm-256color"}):
                    code, out, err = self.invoke(
                        ["file.md"], terminal=terminal, error_terminal=error_terminal,
                        render={"side_effect": RenderError("literal --config: file.md")},
                    )
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                expected = "\x1b[31mrender:\x1b[0m" if error_terminal else "render:"
                self.assertEqual(err, f"{expected} literal --config: file.md\n")

    def test_usage_errors_share_the_error_palette(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), patch.object(stderr, "isatty", return_value=True), patch(
            "term_render.cli.sys.platform", "linux",
        ), patch.dict("os.environ", {"NO_COLOR": "", "TERM": "xterm-256color"}), self.assertRaises(SystemExit) as caught:
            main(["--unknown"])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("\x1b[1;38;2;214;172;255musage:\x1b[0m", stderr.getvalue())
        self.assertIn("\x1b[31mrender: error:\x1b[0m unrecognized arguments: --unknown", stderr.getvalue())

    def test_usage_errors(self):
        for args in [[], ["--show-config", "setup"], ["--show-config", "help"], ["a.md", "b.md"], ["--unknown"]]:
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                main(args)
            self.assertEqual(caught.exception.code, 2)

    def test_option_before_or_after_target_and_dash_filename(self):
        with patch("term_render.cli.current_platform", return_value="windows"), patch("term_render.cli.load_config", return_value=(fixture(), "test")) as load, patch("term_render.cli.render", return_value=0) as render:
            for args in [["--config", "c.json", "file.md"], ["file.md", "--config", "c.json"], ["--config", "c.json", "--", "-file.md"]]:
                self.assertEqual(main(args), 0)
                load.assert_called_with("c.json")
            self.assertEqual(render.call_args.args[1], "-file.md")

    def test_command_name_in_help_and_version(self):
        for option in ("--help", "--version"):
            output = io.StringIO()
            with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as caught:
                main([option])
            self.assertEqual(caught.exception.code, 0)
            self.assertIn("render", output.getvalue())
            self.assertNotIn("term-render", output.getvalue())

    def test_help_command_does_not_load_configuration_or_launch_tools(self):
        for arguments in (["help"], ["help", "--config", "missing.json"]):
            with self.subTest(arguments=arguments):
                code, out, err = self.invoke(
                    arguments,
                    current_platform={"side_effect": AssertionError("help needs no platform")},
                    load_config={"side_effect": AssertionError("help needs no config")},
                    render={"side_effect": AssertionError("help launches no viewer")},
                )
                self.assertEqual(code, 0)
                self.assertIn("usage: render", out)
                self.assertIn("--show-config", out)
                self.assertEqual(err, "")

    def test_relative_help_path_is_dispatched_as_a_file(self):
        with patch("term_render.cli.render", return_value=0) as render:
            code, out, err = self.invoke(["./help"])
        self.assertEqual(code, 0)
        self.assertEqual(render.call_args.args[1], "./help")
        self.assertEqual(out + err, "")

    def test_config_edits_and_applies_the_saved_current_config(self):
        edited = fixture()
        with patch("term_render.cli.edit_config", return_value=(edited, "saved.json")) as edit, patch(
            "term_render.cli.check_tools", return_value=[],
        ) as check:
            code, out, err = self.invoke(
                ["config"], load_config={"side_effect": AssertionError("must edit before loading")},
            )
        self.assertEqual(code, 0)
        edit.assert_called_once_with(None)
        check.assert_called_once_with(edited, "windows")
        self.assertIn("saved and validated: saved.json", out)
        self.assertIn("Configuration: saved.json", out)
        self.assertEqual(err, "")

    def test_config_uses_selected_path_without_loading_invalid_config_first(self):
        with patch("term_render.cli.edit_config", return_value=(fixture(), "edited.json")) as edit:
            code, out, err = self.invoke(
                ["config", "--config", "edited.json"],
                load_config={"side_effect": AssertionError("must edit before loading")},
                check_tools={"return_value": []},
            )
        self.assertEqual(code, 0)
        self.assertIn("saved and validated", out)
        self.assertEqual(err, "")
        edit.assert_called_once_with("edited.json")

    def test_config_checks_new_config_and_propagates_failed_checks(self):
        edited = fixture()
        edited["pending"] = {}
        with patch("term_render.cli.edit_config", return_value=(edited, "edited.json")), patch(
            "term_render.cli.check_tools", return_value=[ToolStatus("viewer", "missing", "viewer")],
        ) as check:
            code, out, err = self.invoke(["config"])
        self.assertEqual(code, 1)
        check.assert_called_once_with(edited, "windows")
        self.assertIn("edited.json", out)
        self.assertEqual(err, "")

    def test_config_does_not_check_tools_after_editor_or_validation_failure(self):
        for error, expected in ((ConfigError("invalid saved JSON"), 2), (KeyboardInterrupt(), 130)):
            with self.subTest(error=error), patch("term_render.cli.check_tools") as check:
                code, out, err = self.invoke(["config"], edit_config={"side_effect": error})
            self.assertEqual(code, expected)
            check.assert_not_called()
            self.assertEqual(out, "")
            self.assertIn("render:", err)

    def test_config_action_usage_errors(self):
        for args in [["config", "edit"], ["config", "apply"], ["config", "--apply"],
                     ["file.md", "edit"], ["--apply", "file.md"], ["config", "--show-config"]]:
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                main(args)
            self.assertEqual(caught.exception.code, 2)
