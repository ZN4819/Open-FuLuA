from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from app.runtime import RuntimePaths
from app.services.data_migration import migrate_legacy_data, preflight_migration


def _runtime_paths(root: Path) -> RuntimePaths:
    return RuntimePaths(
        data_root=root,
        database_path=root / "data" / "app.db",
        storage_path=root / "storage",
        log_path=root / "logs",
        backup_path=root / "backups",
        migration_path=root / "migration",
        mode="desktop",
    )


def _create_source(root: Path, *, missing_image: bool = False) -> Path:
    database_path = root / "backend" / "data" / "app.db"
    storage_path = root / "storage" / "evidence"
    database_path.parent.mkdir(parents=True)
    storage_path.mkdir(parents=True)
    db = sqlite3.connect(database_path)
    try:
        db.executescript(
            """
            CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE appendix_sections (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, code TEXT NOT NULL);
            CREATE TABLE assessment_rows (id INTEGER PRIMARY KEY, section_id INTEGER NOT NULL, record_text TEXT NOT NULL);
            CREATE TABLE evidence_images (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, section_code TEXT NOT NULL, file_path TEXT NOT NULL);
            CREATE TABLE cross_references (id INTEGER PRIMARY KEY, source_row_id INTEGER NOT NULL, target_image_id INTEGER, token TEXT NOT NULL);
            INSERT INTO projects VALUES (1, '仅用于测试的项目');
            INSERT INTO appendix_sections VALUES (1, 1, 'A-1');
            INSERT INTO assessment_rows VALUES (1, 1, '仅用于验证迁移的正文');
            INSERT INTO evidence_images VALUES (1, 1, 'A-1', 'evidence/photo.png');
            INSERT INTO cross_references VALUES (1, 1, 1, '[[FIG:1]]');
            """
        )
    finally:
        db.close()
    if not missing_image:
        (storage_path / "photo.png").write_bytes(b"test image")
    return root


class DataMigrationTests(unittest.TestCase):
    def test_preflight_recognises_legacy_repository_and_reports_safe_statistics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = _create_source(Path(temp_dir) / "legacy")

            result = preflight_migration(source)

        self.assertTrue(result.can_migrate)
        self.assertEqual(result.project_count, 1)
        self.assertEqual(result.image_count, 1)
        self.assertEqual(result.missing_files, ())
        self.assertEqual(result.database_integrity, "ok")
        self.assertNotIn("仅用于验证迁移的正文", result.to_dict().__repr__())

    def test_missing_referenced_image_blocks_without_changing_source_or_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = _create_source(root / "legacy", missing_image=True)
            source_db = source / "backend" / "data" / "app.db"
            source_hash_before = hashlib.sha256(source_db.read_bytes()).hexdigest()
            paths = _runtime_paths(root / "desktop")

            result = migrate_legacy_data(source, paths)

            self.assertFalse(result.migrated)
            self.assertIn("缺失", result.reason)
            self.assertEqual(source_hash_before, hashlib.sha256(source_db.read_bytes()).hexdigest())
            self.assertFalse(paths.database_path.exists())
            self.assertFalse(paths.storage_path.exists())
            self.assertIn('"status": "failed"', (paths.migration_path / "migration-state.json").read_text(encoding="utf-8"))

    def test_corrupt_source_database_blocks_without_creating_runtime_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "legacy"
            database_path = source / "data" / "app.db"
            database_path.parent.mkdir(parents=True)
            (source / "storage").mkdir()
            database_path.write_bytes(b"not a sqlite database")
            paths = _runtime_paths(root / "desktop")

            result = migrate_legacy_data(source, paths)

            self.assertFalse(result.migrated)

    def test_source_that_is_the_desktop_target_is_blocked_without_deleting_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = _runtime_paths(Path(temp_dir) / "desktop")
            _create_source(paths.data_root)
            # 将 fixture 改为桌面数据根布局；源与目标完全重叠。
            legacy_db = paths.data_root / "backend" / "data" / "app.db"
            paths.database_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_db.replace(paths.database_path)
            legacy_db.parent.rmdir()
            (paths.data_root / "backend").rmdir()
            source_hash = hashlib.sha256(paths.database_path.read_bytes()).hexdigest()

            result = migrate_legacy_data(paths.data_root, paths)

            self.assertFalse(result.migrated)
            self.assertIn("源目录与桌面目标重叠", result.reason)
            self.assertEqual(source_hash, hashlib.sha256(paths.database_path.read_bytes()).hexdigest())

    def test_existing_desktop_project_blocks_without_overwriting_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = _create_source(root / "legacy")
            paths = _runtime_paths(root / "desktop")
            paths.database_path.parent.mkdir(parents=True)
            db = sqlite3.connect(paths.database_path)
            try:
                db.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT)")
                db.execute("INSERT INTO projects VALUES (1, '桌面已有项目')")
                db.commit()
            finally:
                db.close()
            result = migrate_legacy_data(source, paths)
            self.assertFalse(result.migrated)
            self.assertIn("已有用户数据", result.reason)

    def test_zero_project_database_with_unknown_or_user_rows_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir); source = _create_source(root / "legacy"); paths = _runtime_paths(root / "desktop")
            paths.database_path.parent.mkdir(parents=True)
            db = sqlite3.connect(paths.database_path)
            db.executescript("CREATE TABLE projects (id INTEGER PRIMARY KEY); CREATE TABLE private_settings (value TEXT); INSERT INTO private_settings VALUES ('keep');")
            db.close()
            result = migrate_legacy_data(source, paths)
            self.assertFalse(result.migrated)
            self.assertIn("已有用户数据", result.reason)


    def test_migration_copies_consistent_data_then_is_idempotent_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = _create_source(root / "legacy")
            source_db = source / "backend" / "data" / "app.db"
            source_hash_before = hashlib.sha256(source_db.read_bytes()).hexdigest()
            paths = _runtime_paths(root / "desktop")

            first = migrate_legacy_data(source, paths)
            second = migrate_legacy_data(source, paths)

            self.assertTrue(first.migrated)
            self.assertTrue(first.restart_required)
            self.assertTrue(second.already_installed)
            self.assertEqual(source_hash_before, hashlib.sha256(source_db.read_bytes()).hexdigest())
            db = sqlite3.connect(paths.database_path)
            try:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM appendix_sections").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM assessment_rows").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM evidence_images").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM cross_references").fetchone()[0], 1)
            finally:
                db.close()
            self.assertEqual((paths.storage_path / "evidence" / "photo.png").read_bytes(), b"test image")

    def test_published_snapshot_after_install_failure_can_retry_and_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir); source = _create_source(root / "legacy"); paths = _runtime_paths(root / "desktop")
            from app.services import data_migration
            real_replace = data_migration.os.replace
            calls = {"count": 0}
            def fail_storage(src, dst):
                calls["count"] += 1
                if calls["count"] == 3: raise OSError("强制安装失败")
                return real_replace(src, dst)
            with patch("app.services.data_migration.os.replace", side_effect=fail_storage):
                first = migrate_legacy_data(source, paths)
            self.assertFalse(first.migrated)
            second = migrate_legacy_data(source, paths)
            self.assertTrue(second.migrated)


if __name__ == "__main__":
    unittest.main()
