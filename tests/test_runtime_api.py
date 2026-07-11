from __future__ import annotations

import unittest
import threading
import time
import re
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
        # validate_project 会 replace_validation_issues，不能按只读 POST 排除。
        self.assertTrue(is_business_write_request("POST", "/api/projects/1/validate"))
        self.assertFalse(is_business_write_request("POST", "/api/runtime/upgrade/prepare"))
        self.assertFalse(is_business_write_request("POST", "/api/record-templates/import-preview"))

    def test_upgrade_prepare_creates_pre_upgrade_backup_inside_exclusive_gate(self) -> None:
        from app.api.runtime import prepare_upgrade

        backup = type("Backup", (), {"path": Path("C:/backups/pre_upgrade-safe")})()
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "app.db"
            connection = sqlite3.connect(database_path)
            try:
                connection.execute("PRAGMA user_version = 7")
            finally:
                connection.close()
            with patch("app.api.runtime.resolve_runtime_paths") as paths, patch(
                "app.api.runtime.create_backup", return_value=backup
            ) as create_backup:
                paths.return_value.database_path = database_path
                response = prepare_upgrade({"lease_id": "client-known"})

        create_backup.assert_called_once_with(paths.return_value, "pre_upgrade")
        self.assertTrue(response["ready"])
        self.assertEqual(response["backup_id"], "pre_upgrade-safe")
        self.assertEqual(response["schema_version"], "7")
        self.assertEqual(response["lease_id"], "client-known")
        from app.api.runtime import cancel_upgrade, runtime_operations
        self.assertTrue(runtime_operations.writes_blocked())
        self.assertEqual(cancel_upgrade({"lease_id": response["lease_id"]}), {"cancelled": True})
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
                connection.execute("PRAGMA user_version = 7")
                connection.commit()
            finally:
                connection.close()
            with patch("app.api.runtime.resolve_runtime_paths") as paths:
                paths.return_value.database_path = database_path
                response = integrity_check()

        self.assertEqual(response, {"integrity": "ok", "schema_version": "7"})
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

    def test_ready_upgrade_lease_timeout_self_heals_but_standard_exclusive_never_times_out(self) -> None:
        from app.api.runtime import RuntimeOperations

        now = [100.0]
        operations = RuntimeOperations(clock=lambda: now[0])
        state, _ = operations.begin_upgrade_prepare("client-known", 5)
        self.assertEqual(state, "started")
        self.assertTrue(operations.complete_upgrade_prepare("client-known", {"ready": True}))
        now[0] = 106.0
        self.assertFalse(operations.writes_blocked())
        with operations.business_write():
            self.assertEqual(operations.business_writes_active(), 1)
        lease = operations.acquire_exclusive()
        now[0] = 10_000.0
        self.assertTrue(operations.writes_blocked())
        operations.release_exclusive(lease)

    def test_desktop_target_schema_contract_matches_backend_schema(self) -> None:
        from app.runtime import SCHEMA_VERSION

        source = (Path(__file__).resolve().parents[1] / "desktop" / "src" / "main.ts").read_text(encoding="utf-8")
        match = re.search(r'const CURRENT_SCHEMA_VERSION = "([^"]+)";', source)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.group(1), SCHEMA_VERSION)

    def test_cancel_during_blocked_upgrade_backup_keeps_gate_closed_until_backup_returns(self) -> None:
        from fastapi import HTTPException
        from app.api.runtime import RuntimeOperations, cancel_upgrade, prepare_upgrade

        operations = RuntimeOperations()
        entered = threading.Event()
        release = threading.Event()
        errors: list[Exception] = []
        backup = type("Backup", (), {"path": Path("C:/backups/pre_upgrade-safe")})()

        def blocked_backup(*_args):
            entered.set()
            release.wait(timeout=2)
            return backup

        def prepare() -> None:
            try:
                prepare_upgrade({"lease_id": "blocked-cancel"})
            except Exception as exc:
                errors.append(exc)

        with patch("app.api.runtime.runtime_operations", operations), patch(
            "app.api.runtime.create_backup", side_effect=blocked_backup
        ), patch("app.api.runtime.resolve_runtime_paths"):
            worker = threading.Thread(target=prepare)
            worker.start()
            self.assertTrue(entered.wait(timeout=1))
            self.assertEqual(cancel_upgrade({"lease_id": "blocked-cancel"}), {"cancelled": True})
            self.assertTrue(operations.writes_blocked())
            with self.assertRaises(RuntimeError):
                operations.reserve_business_write()
            release.set()
            worker.join(timeout=1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], HTTPException)
        self.assertFalse(operations.writes_blocked())

    def test_timeout_during_blocked_upgrade_backup_keeps_gate_closed_until_backup_returns(self) -> None:
        from fastapi import HTTPException
        from app.api.runtime import RuntimeOperations, prepare_upgrade

        now = [100.0]
        operations = RuntimeOperations(clock=lambda: now[0])
        entered = threading.Event()
        release = threading.Event()
        errors: list[Exception] = []
        backup = type("Backup", (), {"path": Path("C:/backups/pre_upgrade-safe")})()

        def blocked_backup(*_args):
            entered.set()
            release.wait(timeout=2)
            return backup

        def prepare() -> None:
            try:
                prepare_upgrade({"lease_id": "blocked-timeout"})
            except Exception as exc:
                errors.append(exc)

        with patch("app.api.runtime.runtime_operations", operations), patch(
            "app.api.runtime.create_backup", side_effect=blocked_backup
        ), patch("app.api.runtime.resolve_runtime_paths"):
            worker = threading.Thread(target=prepare)
            worker.start()
            self.assertTrue(entered.wait(timeout=1))
            now[0] = 401.0
            self.assertTrue(operations.writes_blocked())
            with self.assertRaises(RuntimeError):
                operations.reserve_business_write()
            release.set()
            worker.join(timeout=1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], HTTPException)
        self.assertFalse(operations.writes_blocked())

    def test_same_lease_retry_while_preparing_does_not_create_second_backup(self) -> None:
        from app.api.runtime import RuntimeOperations, cancel_upgrade, prepare_upgrade

        operations = RuntimeOperations()
        entered = threading.Event()
        release = threading.Event()
        calls = 0
        errors: list[Exception] = []
        backup = type("Backup", (), {"path": Path("C:/backups/pre_upgrade-safe")})()

        def blocked_backup(*_args):
            nonlocal calls
            calls += 1
            entered.set()
            release.wait(timeout=2)
            return backup

        with patch("app.api.runtime.runtime_operations", operations), patch(
            "app.api.runtime.create_backup", side_effect=blocked_backup
        ), patch("app.api.runtime.resolve_runtime_paths"):
            def prepare() -> None:
                try:
                    prepare_upgrade({"lease_id": "same-lease"})
                except Exception as exc:
                    errors.append(exc)

            worker = threading.Thread(target=prepare)
            worker.start()
            self.assertTrue(entered.wait(timeout=1))
            retry = prepare_upgrade({"lease_id": "same-lease"})
            self.assertEqual(retry, {"ready": False, "status": "preparing", "lease_id": "same-lease"})
            self.assertEqual(calls, 1)
            self.assertEqual(cancel_upgrade({"lease_id": "same-lease"}), {"cancelled": True})
            release.set()
            worker.join(timeout=1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(calls, 1)
        self.assertEqual(len(errors), 1)

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
