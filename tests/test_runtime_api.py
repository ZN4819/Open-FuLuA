from __future__ import annotations

import unittest
import threading
import time
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch


class RuntimeApiTests(unittest.TestCase):
    def test_preflight_response_uses_safe_summary_without_project_body(self) -> None:
        from app.api.runtime import migration_preflight
        from app.services.data_migration import MigrationPreflight

        preflight = MigrationPreflight(
            source_root="C:/legacy",
            database_path="C:/legacy/backend/data/app.db",
            storage_path="C:/legacy/storage",
            database_integrity="ok",
            project_count=1,
            image_count=2,
            missing_files=(),
            can_migrate=True,
        )
        with patch("app.api.runtime.preflight_migration", return_value=preflight):
            response = migration_preflight({"source_root": "C:/legacy"})

        self.assertTrue(response["can_migrate"])
        self.assertEqual(response["blocking_reasons"], [])
        self.assertNotIn("项目正文", repr(response))

    def test_exclusive_maintenance_blocks_new_business_writes(self) -> None:
        from app.api.runtime import runtime_operations

        self.assertFalse(runtime_operations.writes_blocked())
        with runtime_operations.exclusive():
            self.assertTrue(runtime_operations.writes_blocked())
        self.assertFalse(runtime_operations.writes_blocked())

    def test_status_reports_active_business_write_count(self) -> None:
        from app.api.runtime import RuntimeOperations

        operations = RuntimeOperations()
        self.assertEqual(operations.business_writes_active(), 0)
        with operations.business_write():
            self.assertEqual(operations.business_writes_active(), 1)
        self.assertEqual(operations.business_writes_active(), 0)

    def test_background_write_reservation_is_counted_from_queue_until_completion(self) -> None:
        from app.api.runtime import RuntimeOperations

        operations = RuntimeOperations()
        reservation = operations.reserve_business_write()
        self.assertEqual(operations.business_writes_active(), 1)
        reservation.release()
        reservation.release()
        self.assertEqual(operations.business_writes_active(), 0)

    def test_only_mutating_business_methods_reserve_writes(self) -> None:
        from app.main import is_business_write_request

        self.assertFalse(is_business_write_request("GET", "/api/projects"))
        self.assertFalse(is_business_write_request("HEAD", "/api/projects"))
        self.assertTrue(is_business_write_request("POST", "/api/projects"))
        self.assertTrue(is_business_write_request("PUT", "/api/projects/1"))
        self.assertFalse(is_business_write_request("POST", "/api/runtime/upgrade/prepare"))
        self.assertFalse(is_business_write_request("POST", "/api/record-templates/import-preview"))

    def test_upgrade_prepare_creates_pre_upgrade_backup_inside_exclusive_gate(self) -> None:
        from app.api.runtime import prepare_upgrade

        backup = type("Backup", (), {"path": Path("C:/backups/pre_upgrade-safe")})()
        with patch("app.api.runtime.resolve_runtime_paths") as paths, patch(
            "app.api.runtime.create_backup", return_value=backup
        ) as create_backup:
            response = prepare_upgrade()

        create_backup.assert_called_once_with(paths.return_value, "pre_upgrade")
        self.assertTrue(response["ready"])
        self.assertEqual(response["backup_id"], "pre_upgrade-safe")
        self.assertEqual(response["schema_version"], "1")
        self.assertRegex(response["lease_id"], r"^[A-Za-z0-9._-]+$")
        from app.api.runtime import cancel_upgrade, runtime_operations
        self.assertTrue(runtime_operations.writes_blocked())
        self.assertEqual(cancel_upgrade({"lease_id": response["lease_id"]}), {"cancelled": True})
        self.assertFalse(runtime_operations.writes_blocked())

    def test_integrity_check_returns_only_integrity_and_schema_version(self) -> None:
        from app.api.runtime import integrity_check

        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "app.db"
            connection = sqlite3.connect(database_path)
            try:
                connection.execute("CREATE TABLE private_body (body TEXT)")
                connection.execute("INSERT INTO private_body VALUES ('不得泄露的正文')")
                connection.commit()
            finally:
                connection.close()
            with patch("app.api.runtime.resolve_runtime_paths") as paths:
                paths.return_value.database_path = database_path
                response = integrity_check()

        self.assertEqual(response, {"integrity": "ok", "schema_version": "1"})
        self.assertNotIn("不得泄露", repr(response))

    def test_exclusive_waits_for_started_write_and_rejects_new_write(self) -> None:
        from app.api.runtime import RuntimeOperations

        operations = RuntimeOperations()
        entered = threading.Event()
        release = threading.Event()
        exclusive_entered = threading.Event()
        with operations.business_write():
            entered.set()
            worker = threading.Thread(target=lambda: self._enter_exclusive(operations, exclusive_entered))
            worker.start()
            self.assertTrue(entered.is_set())
            time.sleep(0.03)
            self.assertFalse(exclusive_entered.is_set())
            with self.assertRaisesRegex(RuntimeError, "正在进行"):
                with operations.business_write():
                    pass
        worker.join(timeout=1)
        self.assertTrue(exclusive_entered.is_set())

    @staticmethod
    def _enter_exclusive(operations, entered) -> None:
        with operations.exclusive():
            entered.set()

    def test_restore_response_requires_controlled_sidecar_restart(self) -> None:
        from app.api.runtime import restore_backup
        from app.services.backups import RestoreResult

        with patch("app.api.runtime.resolve_runtime_paths") as paths, patch(
            "app.api.runtime.resolve_backup_id", return_value=Path("C:/backups/daily-safe")
        ), patch(
            "app.api.runtime.restore_runtime_backup", return_value=RestoreResult(True, True)
        ):
            paths.return_value.backup_path = __import__("pathlib").Path("C:/backups")
            response = restore_backup("daily-safe")

        self.assertEqual(response, {"restored": True, "restart_required": True, "message": "恢复完成"})

    def test_migration_creates_pre_migration_backup_before_installing_copy(self) -> None:
        from app.api.runtime import MigrationRequest, migrate
        from app.services.data_migration import MigrationResult

        with patch("app.api.runtime.resolve_runtime_paths") as paths, patch(
            "app.api.runtime.create_backup"
        ) as create_backup, patch(
            "app.api.runtime.migrate_legacy_data", return_value=MigrationResult(True, restart_required=True)
        ) as migrate_legacy:
            response = migrate(MigrationRequest(source_root="C:/legacy"))

        create_backup.assert_called_once_with(paths.return_value, "pre_migration")
        migrate_legacy.assert_called_once_with("C:/legacy", paths.return_value)
        self.assertEqual(response, {"migrated": True, "restart_required": True, "message": "旧数据已复制，正在重新启动本地服务"})


if __name__ == "__main__":
    unittest.main()
