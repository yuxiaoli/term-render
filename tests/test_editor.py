import contextlib
from importlib.resources import files
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from term_render.config import ConfigError, load_config
from term_render.editor import _split_editor, edit_config


def fixture():
    return {"version": 1, "tools": {}, "extensions": {}}


class EditorTests(unittest.TestCase):
    def invoke(self, path=None, *, environ=None, run=None, user_path=None):
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {"EDITOR": "editor", **(environ or {})}, clear=True))
            stack.enter_context(patch("term_render.editor.resolve_executable", return_value="editor"))
            if user_path is not None:
                stack.enter_context(patch("term_render.editor.user_config_path", return_value=user_path))
            process = stack.enter_context(patch("term_render.editor.subprocess.run", side_effect=run,
                                                return_value=subprocess.CompletedProcess([], 0)))
            result = edit_config(path)
        return result, process

    def test_explicit_environment_and_user_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ("explicit.json", "environment.json", "user.json")]
            for path in paths:
                path.write_text(json.dumps(fixture()), encoding="utf-8")
            for explicit, environment, expected in [
                (str(paths[0]), {"TERM_RENDER_CONFIG": str(paths[1])}, paths[0]),
                (None, {"TERM_RENDER_CONFIG": str(paths[1])}, paths[1]),
                (None, {}, paths[2]),
            ]:
                with self.subTest(expected=expected):
                    (config, source), process = self.invoke(explicit, environ=environment, user_path=paths[2])
                    self.assertEqual(config, fixture())
                    self.assertEqual(source, str(expected.resolve()))
                    process.assert_called_once_with(["editor", str(expected.resolve())], shell=False, check=False)

    def test_default_is_copied_to_user_config_before_editor_starts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "config.json"
            def inspect_copy(command, **kwargs):
                self.assertTrue(path.is_file())
                self.assertEqual(path.read_text(encoding="utf-8"),
                                 files("term_render.configs").joinpath("default-config.json").read_text(encoding="utf-8"))
                return subprocess.CompletedProcess(command, 0)
            (config, source), _ = self.invoke(run=inspect_copy, user_path=path)
            self.assertEqual(source, str(path.resolve()))
            self.assertEqual(config, load_config(str(path))[0])

    def test_existing_invalid_config_can_be_repaired(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text("{", encoding="utf-8")
            def repair(command, **kwargs):
                self.assertEqual(path.read_text(encoding="utf-8"), "{")
                path.write_text(json.dumps(fixture()), encoding="utf-8")
                return subprocess.CompletedProcess(command, 0)
            (config, _), _ = self.invoke(str(path), run=repair)
            self.assertEqual(config, fixture())

    def test_saved_config_is_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "Cannot load configuration"):
                self.invoke(str(path))
            self.assertEqual(path.read_text(encoding="utf-8"), "{")

    def test_editor_flags_and_config_path_are_separate_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config & $() ; [x] 测试.json"
            path.write_text(json.dumps(fixture()), encoding="utf-8")
            _, process = self.invoke(str(path), environ={"EDITOR": 'editor --wait --title="two words"'})
            process.assert_called_once_with(["editor", "--wait", "--title=two words", str(path.resolve())],
                                            shell=False, check=False)

    def test_posix_editor_quoting(self):
        self.assertEqual(_split_editor("'/Applications/My Editor' --wait 'two words'", windows=False),
                         ["/Applications/My Editor", "--wait", "two words"])

    def test_windows_editor_quoting_preserves_paths_and_flags(self):
        self.assertEqual(_split_editor(r'"C:\Program Files\Editor\editor.exe" --wait --title="two words"', windows=True),
                         [r"C:\Program Files\Editor\editor.exe", "--wait", "--title=two words"])
        self.assertEqual(_split_editor(r'C:\Editors\editor.exe "C:\with space\\"', windows=True),
                         [r"C:\Editors\editor.exe", "C:\\with space\\"])
        self.assertEqual(_split_editor(r'editor.exe "a\"b"', windows=True), ["editor.exe", 'a"b'])
        self.assertEqual(_split_editor('editor.exe ""', windows=True), ["editor.exe", ""])

    def test_missing_blank_and_malformed_editor_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "not-created" / "config.json"
            for editor in (None, "", " ", '"unterminated', '""'):
                environment = {} if editor is None else {"EDITOR": editor}
                with self.subTest(editor=editor), patch.dict(os.environ, environment, clear=True), \
                     patch("term_render.editor.user_config_path", return_value=path), \
                     patch("term_render.editor.subprocess.run") as process, self.assertRaises(ConfigError):
                    edit_config()
                process.assert_not_called()
                self.assertFalse(path.exists())

    def test_nul_editor_is_rejected(self):
        with self.assertRaises(ConfigError):
            _split_editor("bad\0editor", windows=os.name == "nt")

    def test_missing_editor_executable(self):
        with patch.dict(os.environ, {"EDITOR": "missing-editor --wait"}, clear=True), \
             patch("term_render.editor.resolve_executable", return_value=None), \
             self.assertRaisesRegex(ConfigError, "native executable"):
            edit_config()

    def test_editor_resolution_and_config_preparation_errors_are_actionable(self):
        with patch.dict(os.environ, {"EDITOR": "editor"}, clear=True), \
             patch("term_render.editor.resolve_executable", side_effect=OSError("access denied")), \
             self.assertRaisesRegex(ConfigError, "Cannot resolve EDITOR"):
            edit_config()
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "not-a-directory"
            parent.write_text("existing file", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "Cannot prepare configuration"):
                self.invoke(user_path=parent / "config.json")

    def test_failed_editor_and_launch_errors_are_actionable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(fixture()), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "exited.*7"):
                self.invoke(str(path), run=lambda *args, **kwargs: subprocess.CompletedProcess([], 7))
            with self.assertRaisesRegex(ConfigError, "Cannot launch.*editor"):
                self.invoke(str(path), run=OSError("access denied"))

    def test_blank_selected_config_path_never_falls_back(self):
        for explicit, environment in [("", {}), (None, {"TERM_RENDER_CONFIG": " "})]:
            with self.subTest(explicit=explicit), self.assertRaisesRegex(ConfigError, "path must not be blank"):
                self.invoke(explicit, environ=environment)

    def test_real_editor_process_receives_args_and_updates_selected_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "config & $() ; [x] 测试.json"
            path.write_text("{", encoding="utf-8")
            script = root / "edit config.py"
            script.write_text("import json,sys\nfrom pathlib import Path\n"
                              "assert sys.argv[1] == '--wait'\n"
                              "Path(sys.argv[2]).write_text(json.dumps({'version':1,'tools':{},'extensions':{}}),encoding='utf-8')\n",
                              encoding="utf-8")
            editor = subprocess.list2cmdline([sys.executable, str(script), "--wait"])
            if os.name != "nt":
                import shlex
                editor = shlex.join([sys.executable, str(script), "--wait"])
            with patch.dict(os.environ, {"EDITOR": editor}, clear=True):
                config, source = edit_config(str(path))
            self.assertEqual(config, fixture())
            self.assertEqual(source, str(path.resolve()))
