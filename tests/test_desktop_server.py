import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.request import urlopen
from unittest.mock import patch

from app import desktop_server


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"


class DesktopServerTests(unittest.TestCase):
    def _command(self, *arguments: str) -> list[str]:
        return [str(PYTHON), "-m", "app.desktop_server", *arguments]

    def test_offline_recovery_lists_safe_summaries_and_restores_known_backup(self) -> None:
        from app.runtime import RuntimePaths
        from app.services.backups import create_backup
        from tests.test_backups import _create_live_data

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            paths = RuntimePaths(root, root / "data" / "app.db", root / "storage", root / "logs", root / "backups", root / "migration", "desktop")
            _create_live_data(paths, "备份正文不能出现在摘要")
            backup = create_backup(paths, "pre_upgrade")

            listed = subprocess.run(self._command("--data-root", str(root), "--offline-recovery", "list"), cwd=ROOT / "backend", capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertIn(backup.path.name, listed.stdout)
            self.assertNotIn("备份正文", listed.stdout)

            restored = subprocess.run(self._command("--data-root", str(root), "--offline-recovery", "restore", "--backup-id", backup.path.name), cwd=ROOT / "backend", capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            self.assertEqual(restored.returncode, 0, restored.stderr)
            self.assertIn('"restored": true', restored.stdout.lower())

    def test_offline_integrity_is_read_only_and_reports_actual_user_version(self) -> None:
        import sqlite3
        from app.runtime import SCHEMA_VERSION

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database_path = root / "data" / "app.db"
            database_path.parent.mkdir(parents=True)
            connection = sqlite3.connect(database_path)
            try:
                connection.execute("CREATE TABLE sentinel (value TEXT)")
                connection.execute("INSERT INTO sentinel VALUES ('unchanged')")
                connection.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")
                connection.commit()
            finally:
                connection.close()
            before = database_path.read_bytes()
            before_mtime = database_path.stat().st_mtime_ns

            result = subprocess.run(self._command("--data-root", str(root), "--offline-recovery", "integrity"), cwd=ROOT / "backend", capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)

            self.assertEqual(result.returncode, 0, result.stderr)
            event = json.loads(result.stdout.strip())
            self.assertEqual(event, {"event": "FULUA_OFFLINE_INTEGRITY", "integrity": "ok", "schema_version": SCHEMA_VERSION})
            self.assertEqual(database_path.read_bytes(), before)
            self.assertEqual(database_path.stat().st_mtime_ns, before_mtime)

    def test_prepare_schema_upgrade_creates_verified_schema_three_backup_before_start(self) -> None:
        import sqlite3

        from app.runtime import RuntimePaths, SCHEMA_VERSION
        from tests.test_backups import _create_live_data

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            paths = RuntimePaths(
                root,
                root / "data" / "app.db",
                root / "storage",
                root / "logs",
                root / "backups",
                root / "migration",
                "desktop",
            )
            _create_live_data(paths, "schema 3 项目")
            connection = sqlite3.connect(paths.database_path)
            try:
                connection.execute("PRAGMA user_version = 3")
                connection.commit()
            finally:
                connection.close()

            result = subprocess.run(
                self._command(
                    "--data-root",
                    str(root),
                    "--offline-recovery",
                    "prepare-schema-upgrade",
                ),
                cwd=ROOT / "backend",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            event = json.loads(result.stdout.strip())
            self.assertEqual(event["event"], "FULUA_OFFLINE_SCHEMA_UPGRADE")
            self.assertTrue(event["prepared"])
            self.assertEqual((event["source_schema"], event["target_schema"]), ("3", SCHEMA_VERSION))
            backup = paths.backup_path / event["backup_id"]
            self.assertTrue((backup / "metadata.json").is_file())
            backup_db = sqlite3.connect(backup / "data" / "app.db")
            try:
                self.assertEqual(backup_db.execute("PRAGMA user_version").fetchone()[0], 3)
                self.assertEqual(
                    backup_db.execute("SELECT name FROM projects").fetchone()[0],
                    "schema 3 项目",
                )
            finally:
                backup_db.close()
            live_db = sqlite3.connect(paths.database_path)
            try:
                self.assertEqual(live_db.execute("PRAGMA user_version").fetchone()[0], 3)
            finally:
                live_db.close()

    def test_offline_integrity_missing_database_fails_closed_without_creating_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "missing-root"
            result = subprocess.run(self._command("--data-root", str(root), "--offline-recovery", "integrity"), cwd=ROOT / "backend", capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout.strip())["integrity"], "corrupt")
            self.assertFalse((root / "data" / "app.db").exists())

    def test_offline_recovery_rejects_unknown_backup_without_modifying_live_database(self) -> None:
        from app.runtime import RuntimePaths
        from tests.test_backups import _create_live_data

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            paths = RuntimePaths(root, root / "data" / "app.db", root / "storage", root / "logs", root / "backups", root / "migration", "desktop")
            _create_live_data(paths, "现场")
            before = paths.database_path.read_bytes()
            result = subprocess.run(self._command("--data-root", str(root), "--offline-recovery", "restore", "--backup-id", "../escape"), cwd=ROOT / "backend", capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(paths.database_path.read_bytes(), before)

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

    def test_missing_arguments_emit_failed_event_and_write_fallback_log(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment = os.environ.copy()
            environment["TEMP"] = temporary_directory
            environment["TMP"] = temporary_directory
            result = subprocess.run(
                self._command("--session-token", "must-not-leak"),
                cwd=ROOT / "backend",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                timeout=10,
                check=False,
            )
            event = json.loads(result.stdout.strip())
            self.assertEqual(event["event"], "FULUA_FAILED")
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("must-not-leak", result.stdout)
            self.assertNotIn("must-not-leak", result.stderr)
            self.assertTrue((Path(temporary_directory) / "fulua-desktop-failures" / "logs" / "desktop-server.log").is_file())

    def test_conservative_data_root_expand_failure_still_emits_failed_event(self) -> None:
        with patch.object(desktop_server.Path, "expanduser", side_effect=RuntimeError("path expansion failed")), patch.object(
            desktop_server, "_write_failure_log"
        ) as write_log, patch.object(desktop_server.sys, "argv", ["desktop_server", "--data-root", "data", "--web-dist", "web", "--session-token", "must-not-leak"]), patch(
            "sys.stdout"
        ) as stdout:
            self.assertEqual(desktop_server.main(), 1)

        self.assertIn('"event": "FULUA_FAILED"', "".join(call.args[0] for call in stdout.write.call_args_list))
        self.assertNotIn("must-not-leak", "".join(call.args[0] for call in stdout.write.call_args_list))
        self.assertEqual(write_log.call_args.args[0], desktop_server._fallback_data_root())


if __name__ == "__main__":
    unittest.main()
