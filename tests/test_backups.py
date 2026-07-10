from __future__ import annotations

import sqlite3
import tempfile
import unittest
import os
from unittest.mock import patch
from pathlib import Path

from app.runtime import RuntimePaths
from app.services.backups import create_backup, list_backups, restore_backup


def _runtime_paths(root: Path) -> RuntimePaths:
    return RuntimePaths(root, root / "data" / "app.db", root / "storage", root / "logs", root / "backups", root / "migration", "desktop")


def _create_live_data(paths: RuntimePaths, name: str = "初始项目") -> None:
    paths.database_path.parent.mkdir(parents=True)
    paths.storage_path.joinpath("evidence").mkdir(parents=True)
    paths.storage_path.joinpath("evidence", "photo.png").write_bytes(b"initial image")
    db = sqlite3.connect(paths.database_path)
    try:
        db.executescript(
            """
            CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE evidence_images (id INTEGER PRIMARY KEY, file_path TEXT NOT NULL);
            INSERT INTO evidence_images VALUES (1, 'evidence/photo.png');
            """
        )
        db.execute("INSERT INTO projects VALUES (1, ?)", (name,))
        db.commit()
    finally:
        db.close()


class BackupTests(unittest.TestCase):
    def test_backup_uses_consistent_sqlite_copy_and_safe_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = _runtime_paths(Path(temp_dir))
            _create_live_data(paths)

            backup = create_backup(paths, "daily")

            self.assertEqual(backup.kind, "daily")
            self.assertTrue(backup.database_path.is_file())
            self.assertTrue((backup.path / "metadata.json").is_file())
            db = sqlite3.connect(backup.database_path)
            try:
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(db.execute("SELECT name FROM projects").fetchone()[0], "初始项目")
            finally:
                db.close()
            self.assertEqual((backup.storage_path / "evidence" / "photo.png").read_bytes(), b"initial image")
            self.assertNotIn("初始项目", (backup.path / "metadata.json").read_text(encoding="utf-8"))

    def test_retention_keeps_daily_limit_and_never_removes_pre_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = _runtime_paths(Path(temp_dir))
            _create_live_data(paths)
            for _ in range(8):
                create_backup(paths, "daily")
            protected = create_backup(paths, "pre_restore")

            backups = list_backups(paths)

            self.assertEqual(len([item for item in backups if item.kind == "daily"]), 7)
            self.assertTrue(protected.path.exists())

    def test_backup_falls_back_to_copy_when_hard_link_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = _runtime_paths(Path(temp_dir))
            _create_live_data(paths)

            with patch("app.services.backups.os.link", side_effect=OSError("no hard link")):
                backup = create_backup(paths, "daily")

            source = paths.storage_path / "evidence" / "photo.png"
            copied = backup.storage_path / "evidence" / "photo.png"
            self.assertEqual(copied.read_bytes(), source.read_bytes())
            self.assertFalse(os.path.samefile(source, copied))

    def test_wal_database_backup_contains_committed_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = _runtime_paths(Path(temp_dir))
            _create_live_data(paths)
            db = sqlite3.connect(paths.database_path)
            try:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("UPDATE projects SET name = 'WAL 已提交项目'")
                db.commit()
            finally:
                db.close()

            backup = create_backup(paths, "daily")

            db = sqlite3.connect(backup.database_path)
            try:
                self.assertEqual(db.execute("SELECT name FROM projects").fetchone()[0], "WAL 已提交项目")
            finally:
                db.close()

    def test_restore_creates_pre_restore_snapshot_and_returns_restart_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = _runtime_paths(Path(temp_dir))
            _create_live_data(paths, "备份中的项目")
            backup = create_backup(paths, "daily")
            db = sqlite3.connect(paths.database_path)
            try:
                db.execute("UPDATE projects SET name = '当前项目'")
                db.commit()
            finally:
                db.close()
            replacement = paths.storage_path / "evidence" / "replacement.png"
            replacement.write_bytes(b"current image")
            os.replace(replacement, paths.storage_path / "evidence" / "photo.png")

            result = restore_backup(paths, backup.path)

            self.assertTrue(result.restored)
            self.assertTrue(result.restart_required)
            db = sqlite3.connect(paths.database_path)
            try:
                self.assertEqual(db.execute("SELECT name FROM projects").fetchone()[0], "备份中的项目")
            finally:
                db.close()
            self.assertEqual((paths.storage_path / "evidence" / "photo.png").read_bytes(), b"initial image")
            self.assertTrue(any(item.kind == "pre_restore" for item in list_backups(paths)))

    def test_failed_restore_keeps_live_data_and_pre_restore_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = _runtime_paths(Path(temp_dir))
            _create_live_data(paths, "当前项目")
            backup = create_backup(paths, "daily")
            (backup.storage_path / "evidence" / "photo.png").unlink()

            result = restore_backup(paths, backup.path)

            self.assertFalse(result.restored)
            db = sqlite3.connect(paths.database_path)
            try:
                self.assertEqual(db.execute("SELECT name FROM projects").fetchone()[0], "当前项目")
            finally:
                db.close()
            self.assertTrue(any(item.kind == "pre_restore" for item in list_backups(paths)))


if __name__ == "__main__":
    unittest.main()
