import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from term_render.config import (
    ConfigError,
    current_platform,
    load_config,
    user_config_path,
    validate_config,
)


def fixture() -> dict[str, Any]:
    return {"version": 1, "tools": {"viewer": {"platforms": {
        p: {"executable": "viewer", "healthcheck": ["--help"], "install": "Install viewer manually."}
        for p in ("windows", "macos", "linux")
    }}}, "extensions": {".md": {
        p: {"tool": "viewer", "args": ["{file}"]} for p in ("windows", "macos", "linux")
    }}, "pending": {".pdf": "Viewer not yet verified."}}


class ConfigTests(unittest.TestCase):
    def test_valid_minimal_and_empty_config(self):
        self.assertEqual(validate_config(fixture())["version"], 1)
        self.assertEqual(validate_config({"version": 1, "tools": {}, "extensions": {}})["tools"], {})

    def test_invalid_shapes_and_references(self):
        cases: list[Any] = [None, [], {"version": 1}, {**fixture(), "version": True},
                 {**fixture(), "version": 2}, {**fixture(), "unknown": 1}]
        for key, value in [("tools", []), ("extensions", []), ("pending", [])]:
            cases.append({**fixture(), key: value})
        for mutation in [
            lambda c: c["tools"]["viewer"].update(extra=1),
            lambda c: c["tools"]["viewer"].update(platforms={}),
            lambda c: c["tools"]["viewer"]["platforms"].update(freebsd={}),
            lambda c: c["extensions"].update({".MD": {}}),
            lambda c: c["extensions"][".md"].update(freebsd={}),
            lambda c: c["extensions"][".md"]["windows"].update(tool="unknown"),
            lambda c: c["tools"]["viewer"]["platforms"].pop("windows"),
            lambda c: c["pending"].update({".md": "Already routed"}),
            lambda c: c["pending"].update({"pdf": "Missing dot"}),
            lambda c: c["pending"].update({".pdf": " "}),
        ]:
            config = fixture()
            mutation(config)
            cases.append(config)
        for config in cases:
            with self.subTest(config=config), self.assertRaises(ConfigError):
                validate_config(config)

    def test_bad_command_arguments_and_placeholders(self):
        for args in ["{file}", [], [1], ["bad\0"], ["a{file}"], ["{unknown}"],
                     ["{{file}}"], ["--help"], ["{file}", "{file}"]]:
            config = fixture()
            config["extensions"][".md"]["windows"]["args"] = args
            with self.subTest(args=args), self.assertRaises(ConfigError):
                validate_config(config)

    def test_bad_executable_healthcheck_and_guidance(self):
        for key, value in [("executable", " "), ("executable", "bad\0"),
                           ("executable", "viewer.CMD"), ("executable", "viewer.bat"),
                           ("executable", "{file}"), ("install", ""),
                           ("healthcheck", []), ("healthcheck", ["{file}"]),
                           ("healthcheck", "--help")]:
            config = fixture()
            config["tools"]["viewer"]["platforms"]["windows"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ConfigError):
                validate_config(config)

    def test_platform_detection(self):
        for raw, expected in [("win32", "windows"), ("darwin", "macos"), ("linux", "linux")]:
            with patch("sys.platform", raw):
                self.assertEqual(current_platform(), expected)
        with patch("sys.platform", "freebsd"), self.assertRaisesRegex(ConfigError, "Unsupported platform"):
            current_platform()

    def test_user_locations(self):
        with patch.dict(os.environ, {"APPDATA": "/roaming", "XDG_CONFIG_HOME": "/xdg"}):
            self.assertEqual(user_config_path("windows"), Path("/roaming/term-render/config.json"))
            self.assertEqual(user_config_path("linux"), Path("/xdg/term-render/config.json"))
        with patch.dict(os.environ, {}, clear=True), patch("pathlib.Path.home", return_value=Path("/home/test")):
            self.assertEqual(user_config_path("linux"), Path("/home/test/.config/term-render/config.json"))
            self.assertEqual(user_config_path("macos"), Path("/home/test/Library/Application Support/term-render/config.json"))
            self.assertEqual(user_config_path("windows"), Path("/home/test/AppData/Roaming/term-render/config.json"))

    def test_precedence_and_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ("explicit.json", "env.json", "user.json")]
            for index, path in enumerate(paths):
                value = fixture()
                value["pending"] = {".pdf": str(index)}
                path.write_text(json.dumps(value), encoding="utf-8-sig")
            with patch.dict(os.environ, {"TERM_RENDER_CONFIG": str(paths[1])}), patch("term_render.config.user_config_path", return_value=paths[2]):
                self.assertEqual(load_config(str(paths[0]))[0]["pending"][".pdf"], "0")
                self.assertEqual(load_config()[0]["pending"][".pdf"], "1")
            with patch.dict(os.environ, {}, clear=True), patch("term_render.config.user_config_path", return_value=paths[2]):
                self.assertEqual(load_config()[0]["pending"][".pdf"], "2")
                paths[2].unlink()
                config, source = load_config()
                self.assertEqual(source, "packaged default")
                self.assertIn(".docx", config["extensions"])
            paths[0].write_text('{"version":1,"tools":{},"extensions":{}}', encoding="utf-8")
            self.assertEqual(load_config(str(paths[0]))[0]["extensions"], {})

    def test_selected_bad_config_never_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            for raw in ["{", '{"version":1,"version":1,"tools":{},"extensions":{}}', "[]"]:
                path.write_text(raw, encoding="utf-8")
                with self.subTest(raw=raw), self.assertRaises(ConfigError):
                    load_config(str(path))
            with self.assertRaises(ConfigError):
                load_config(str(path.parent / "missing.json"))
            with self.assertRaises(ConfigError):
                load_config("")

    def test_schema_and_packaged_example(self):
        from jsonschema import Draft202012Validator

        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / "data/config.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        example = json.loads((root / "data/config.example.json").read_text(encoding="utf-8"))
        default = json.loads((root / "configs/default-config.json").read_text(encoding="utf-8"))
        self.assertEqual(example, default)
        for value in (example, fixture()):
            validator.validate(value)
            validate_config(value)
        bad = copy.deepcopy(fixture())
        bad["extensions"][".md"]["windows"]["args"] = ["not-a-placeholder"]
        self.assertFalse(validator.is_valid(bad))
