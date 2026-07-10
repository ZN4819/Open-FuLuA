import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"


class DesktopServerTests(unittest.TestCase):
    def _command(self, *arguments: str) -> list[str]:
        return [str(PYTHON), "-m", "app.desktop_server", *arguments]

    def test_emits_loopback_random_port_ready_event_after_import_environment_setup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            data_root = root / "user-data"
            web_dist = root / "web"
            web_dist.mkdir()
            (web_dist / "index.html").write_text("<h1>desktop</h1>", encoding="utf-8")
            process = subprocess.Popen(
                self._command("--data-root", str(data_root), "--web-dist", str(web_dist), "--session-token", "must-not-leak"),
                cwd=ROOT / "backend",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            try:
                assert process.stdout is not None
                line = process.stdout.readline().strip()
                event = json.loads(line)
                self.assertEqual(event["event"], "FULUA_READY")
                self.assertRegex(event["health_url"], r"^http://127\.0\.0\.1:\d+/api/health$")
                self.assertNotIn("must-not-leak", line)
                with urlopen(event["health_url"], timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    payload = json.loads(response.read())
                self.assertEqual(payload["runtime_mode"], "desktop")
                self.assertEqual(Path(payload["data_root"]), data_root)
                self.assertTrue((data_root / "data" / "app.db").is_file())
            finally:
                process.terminate()
                process.communicate(timeout=5)

    def test_invalid_data_root_emits_failed_event_without_session_token_and_nonzero_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            invalid_data_root = root / "not-a-directory"
            invalid_data_root.write_text("x", encoding="utf-8")
            result = subprocess.run(
                self._command("--data-root", str(invalid_data_root), "--web-dist", str(root), "--session-token", "must-not-leak"),
                cwd=ROOT / "backend",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
            event = json.loads(result.stdout.strip())
            self.assertEqual(event["event"], "FULUA_FAILED")
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("must-not-leak", result.stdout)
            self.assertNotIn("must-not-leak", result.stderr)


if __name__ == "__main__":
    unittest.main()
