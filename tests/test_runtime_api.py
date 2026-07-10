from __future__ import annotations

import unittest
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

    def test_restore_response_requires_controlled_sidecar_restart(self) -> None:
        from app.api.runtime import restore_backup
        from app.services.backups import RestoreResult

        with patch("app.api.runtime.resolve_runtime_paths") as paths, patch(
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
