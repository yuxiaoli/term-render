"""Real process tests, independent of any third-party renderer installation."""

import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from term_render.cli import main
from term_render.config import current_platform


class ProcessTests(unittest.TestCase):
    def test_config_command_edits_repairs_and_checks_the_saved_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config 测试 & [saved].json"
            config_path.write_text("{", encoding="utf-8")
            record = root / "checked.txt"
            probe = root / "healthcheck.py"
            probe.write_text(
                "from pathlib import Path\nimport sys\n"
                "Path(sys.argv[1]).write_text('checked saved config', encoding='utf-8')\n"
                "raise SystemExit(7)\n", encoding="utf-8",
            )
            platform = current_platform()
            edited = {"version": 1, "tools": {"probe": {"platforms": {platform: {
                "executable": sys.executable, "healthcheck": [str(probe), str(record)],
                "install": "Test probe.",
            }}}}, "extensions": {".md": {platform: {"tool": "probe", "args": ["{file}"]}}}}
            editor = root / "editor.py"
            editor.write_text(
                "from pathlib import Path\nimport sys\n"
                f"Path(sys.argv[1]).write_text({json.dumps(edited)!r}, encoding='utf-8')\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            # The captured output includes the deliberately Unicode config path.
            environment["PYTHONIOENCODING"] = "utf-8"
            command = [sys.executable, str(editor)]
            if os.name == "nt":
                environment["EDITOR"] = subprocess.list2cmdline(command)
            else:
                import shlex
                environment["EDITOR"] = shlex.join(command)
            result = subprocess.run(
                [sys.executable, "-m", "term_render", "--config", str(config_path), "config"],
                env=environment, capture_output=True, timeout=15,
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(result.stderr, b"")
            self.assertIn(b"saved and validated", result.stdout)
            self.assertIn(b"probe: check failed", result.stdout)
            self.assertEqual(json.loads(config_path.read_text(encoding="utf-8")), edited)
            self.assertEqual(record.read_text(encoding="utf-8"), "checked saved config")

    def test_actual_child_preserves_exit_and_does_not_edit_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "data 测试 & $() ; [x].md"
            file.write_text("original", encoding="utf-8")
            script = root / "viewer.py"
            record = root / "received.json"
            script.write_text("import json,sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(json.dumps(sys.argv[2:]),encoding='utf-8')\nsys.exit(23)\n", encoding="utf-8")
            platform = current_platform()
            config = {"version": 1, "tools": {"python": {"platforms": {platform: {
                "executable": sys.executable, "healthcheck": ["--version"], "install": "Already installed."
            }}}}, "extensions": {".md": {platform: {"tool": "python", "args": [str(script), str(record), "{file}"]}}}}
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            self.assertEqual(main(["--config", str(config_path), str(file)]), 23)
            self.assertEqual(json.loads(record.read_text(encoding="utf-8")), [str(file.resolve())])
            self.assertEqual(file.read_text(encoding="utf-8"), "original")

    @unittest.skipIf(sys.platform == "win32", "POSIX PTY/SIGINT test; Windows uses console event delivery")
    def test_posix_tty_streams_and_control_c(self):
        import pty
        import termios

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "file.md"
            file.touch()
            script = root / "viewer.py"
            script.write_text("import os,signal,sys\nassert all(os.isatty(fd) for fd in (0,1,2))\nprint('TTY_READY',flush=True)\nsignal.pause()\n", encoding="utf-8")
            platform = current_platform()
            config = {"version": 1, "tools": {"python": {"platforms": {platform: {
                "executable": sys.executable, "healthcheck": ["--version"], "install": "Existing Python."
            }}}}, "extensions": {".md": {platform: {"tool": "python", "args": [str(script), "{file}"]}}}}
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            master, slave = pty.openpty()
            attrs = termios.tcgetattr(slave)
            attrs[3] &= ~termios.ECHO
            termios.tcsetattr(slave, termios.TCSANOW, attrs)
            process = None
            try:
                process = subprocess.Popen([sys.executable, "-m", "term_render", "--config", str(config_path), str(file)], stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
                output = b""
                deadline = time.monotonic() + 10
                while b"TTY_READY" not in output and time.monotonic() < deadline:
                    readable, _, _ = select.select([master], [], [], 0.1)
                    if readable:
                        output += os.read(master, 4096)
                self.assertIn(b"TTY_READY", output)
                os.killpg(process.pid, signal.SIGINT)
                self.assertEqual(process.wait(timeout=10), 130)
            finally:
                if process is not None and process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)
                os.close(master)
                os.close(slave)
