import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import settings
from app import database
from app.main import app, on_startup
from app.runtime import resolve_runtime_paths
from app.services.docx_generator.generator import generate_project_docx


class RuntimePathsTests(unittest.TestCase):
    def setUp(self) -> None:
        files_app = self._files_application()
        self._original_files_directory = files_app.directory
        self._original_files_directories = files_app.all_directories
        self._original_files_config_checked = files_app.config_checked

    def tearDown(self) -> None:
        files_app = self._files_application()
        files_app.directory = self._original_files_directory
        files_app.all_directories = self._original_files_directories
        files_app.config_checked = self._original_files_config_checked
        self.assertEqual(files_app.directory, self._original_files_directory)
        self.assertEqual(files_app.all_directories, self._original_files_directories)
        self.assertEqual(files_app.config_checked, self._original_files_config_checked)

    @staticmethod
    def _files_application():
        return next(route.app for route in app.routes if getattr(route, "name", None) == "files")

    @staticmethod
    def _read_files_route(path: str) -> tuple[int, bytes]:
        messages: list[dict[str, object]] = []

        async def receive() -> dict[str, object]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, object]) -> None:
            messages.append(message)

        async def request() -> None:
            await RuntimePathsTests._files_application()(
                {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": "GET",
                    "scheme": "http",
                    "path": f"/api/files/{path}",
                    "raw_path": f"/api/files/{path}".encode(),
                    "query_string": b"",
                    "root_path": "/api/files",
                    "headers": [],
                    "client": ("127.0.0.1", 8000),
                    "server": ("127.0.0.1", 8000),
                },
                receive,
                send,
            )

        asyncio.run(request())
        status = next(message["status"] for message in messages if message["type"] == "http.response.start")
        body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
        return int(status), body

    def test_development_defaults_keep_existing_database_and_storage_locations(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            paths = resolve_runtime_paths()

        root = Path(__file__).resolve().parents[1]
        self.assertEqual(paths.mode, "development")
        self.assertEqual(paths.database_path, root / "backend" / "data" / "app.db")
        self.assertEqual(paths.storage_path, root / "storage")

    def test_desktop_data_root_derives_all_runtime_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir) / "FuLuA"
            with patch.dict(os.environ, {"FULUA_DATA_DIR": str(data_root)}, clear=True):
                paths = resolve_runtime_paths()

        self.assertEqual(paths.mode, "desktop")
        self.assertEqual(paths.data_root, data_root)
        self.assertEqual(paths.database_path, data_root / "data" / "app.db")
        self.assertEqual(paths.storage_path, data_root / "storage")
        self.assertEqual(paths.log_path, data_root / "logs")
        self.assertEqual(paths.backup_path, data_root / "backups")
        self.assertEqual(paths.migration_path, data_root / "migration")

    def test_explicit_database_and_storage_override_desktop_derivations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database_path = root / "database" / "custom.db"
            storage_path = root / "custom-storage"
            with patch.dict(
                os.environ,
                {
                    "FULUA_DATA_DIR": str(root / "FuLuA"),
                    "FULUA_DATABASE_PATH": str(database_path),
                    "FULUA_STORAGE_PATH": str(storage_path),
                },
                clear=True,
            ):
                paths = resolve_runtime_paths()

        self.assertEqual(paths.mode, "desktop")
        self.assertEqual(paths.database_path, database_path)
        self.assertEqual(paths.storage_path, storage_path)

    def test_explicit_paths_do_not_change_development_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.dict(
                os.environ,
                {
                    "FULUA_DATABASE_PATH": str(root / "custom.db"),
                    "FULUA_STORAGE_PATH": str(root / "storage"),
                },
                clear=True,
            ):
                paths = resolve_runtime_paths()

        self.assertEqual(paths.mode, "development")
        self.assertEqual(paths.database_path, root / "custom.db")
        self.assertEqual(paths.storage_path, root / "storage")

    def test_settings_reads_storage_path_from_current_runtime_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir) / "FuLuA"
            with patch.dict(os.environ, {"FULUA_DATA_DIR": str(data_root)}, clear=True):
                self.assertEqual(settings.database_path, data_root / "data" / "app.db")
                self.assertEqual(settings.storage_path, data_root / "storage")

    def test_health_response_reports_non_sensitive_runtime_diagnostics(self) -> None:
        from app.main import health

        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir) / "FuLuA"
            database_path = data_root / "data" / "app.db"
            database_path.parent.mkdir(parents=True)
            connection = __import__("sqlite3").connect(database_path)
            try:
                connection.execute("PRAGMA user_version = 7")
            finally:
                connection.close()
            with patch.dict(os.environ, {"FULUA_DATA_DIR": str(data_root)}, clear=True):
                response = health()

        self.assertEqual(response.runtime_mode, "desktop")
        self.assertEqual(response.data_root, str(data_root))
        self.assertEqual(response.schema_version, "7")
        self.assertTrue(response.backend_version)

    def test_startup_rebinds_files_route_to_desktop_storage_path_after_app_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir) / "FuLuA"
            with patch.dict(os.environ, {"FULUA_DATA_DIR": str(data_root)}, clear=True):
                on_startup()
                files_app = self._files_application()

        self.assertEqual(Path(files_app.directory), data_root / "storage")
        self.assertEqual([Path(path) for path in files_app.all_directories], [data_root / "storage"])

    def test_startup_rebinds_files_route_to_explicit_storage_path_after_app_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir) / "FuLuA"
            storage_path = Path(temp_dir) / "explicit-storage"
            with patch.dict(
                os.environ,
                {"FULUA_DATA_DIR": str(data_root), "FULUA_STORAGE_PATH": str(storage_path)},
                clear=True,
            ):
                on_startup()
                files_app = self._files_application()

        self.assertEqual(Path(files_app.directory), storage_path)
        self.assertEqual([Path(path) for path in files_app.all_directories], [storage_path])

    def test_desktop_startup_creates_runtime_directories_and_sqlite_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir) / "FuLuA"
            with patch.dict(os.environ, {"FULUA_DATA_DIR": str(data_root)}, clear=True):
                on_startup()
                for name in ("data", "storage", "logs", "backups", "migration"):
                    self.assertTrue((data_root / name).is_dir())
                self.assertTrue((data_root / "data" / "app.db").is_file())

    def test_desktop_export_uses_current_storage_without_writing_repository_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir) / "FuLuA"
            with patch.dict(os.environ, {"FULUA_DATA_DIR": str(data_root)}, clear=True):
                on_startup()
                project = database.create_project("运行时导出验收")
                export_path = generate_project_docx(project["id"])

            repository_storage = Path(__file__).resolve().parents[1] / "storage"
            self.assertTrue(export_path.is_file())
            self.assertTrue(export_path.is_relative_to(data_root / "storage" / "exports"))
            self.assertFalse((repository_storage / export_path.relative_to(data_root / "storage")).exists())

    def test_files_route_serves_file_from_current_desktop_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir) / "FuLuA"
            with patch.dict(os.environ, {"FULUA_DATA_DIR": str(data_root)}, clear=True):
                on_startup()
                expected = b"desktop static file"
                (data_root / "storage" / "runtime-check.txt").write_bytes(expected)
                status, body = self._read_files_route("runtime-check.txt")

        self.assertEqual(status, 200)
        self.assertEqual(body, expected)

    def test_first_desktop_start_can_import_application_before_storage_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir) / "FuLuA"
            environment = os.environ | {
                "FULUA_DATA_DIR": str(data_root),
                "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "backend"),
            }
            completed = subprocess.run(
                [sys.executable, "-c", "from app.main import app; print(app.title)"],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "附录A编写工具")


if __name__ == "__main__":
    unittest.main()
