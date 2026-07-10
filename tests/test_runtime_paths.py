import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import settings
from app.main import app, on_startup
from app.runtime import resolve_runtime_paths


class RuntimePathsTests(unittest.TestCase):
    @staticmethod
    def _files_application():
        return next(route.app for route in app.routes if getattr(route, "name", None) == "files")

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
            with patch.dict(os.environ, {"FULUA_DATA_DIR": str(data_root)}, clear=True):
                response = health()

        self.assertEqual(response.runtime_mode, "desktop")
        self.assertEqual(response.data_root, str(data_root))
        self.assertTrue(response.schema_version)
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
