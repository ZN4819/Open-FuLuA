from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.runtime import SCHEMA_VERSION


class DatabaseSchemaTests(unittest.TestCase):
    def test_init_db_promotes_actual_user_version_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "app.db"
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA user_version = 0")
            finally:
                connection.close()
            with patch.dict(os.environ, {"FULUA_DATABASE_PATH": str(path)}):
                database.init_db()
                self.assertEqual(database.read_schema_version(path, readonly=True), SCHEMA_VERSION)
                connection = sqlite3.connect(path)
                try:
                    columns = {row[1] for row in connection.execute("PRAGMA table_info(metric_results)")}
                finally:
                    connection.close()
                self.assertIn("ra", columns)
                self.assertIn("rk", columns)

    def test_schema_one_database_is_upgraded_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "app.db"
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    """
                    CREATE TABLE metric_results (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        row_id INTEGER NOT NULL UNIQUE,
                        d TEXT,
                        a TEXT,
                        k TEXT,
                        object_score TEXT,
                        unit_score TEXT,
                        compliance TEXT
                    )
                    """
                )
                connection.execute("INSERT INTO metric_results (row_id, d) VALUES (1, '√')")
                connection.execute("PRAGMA user_version = 1")
                connection.commit()
            finally:
                connection.close()

            with patch.dict(os.environ, {"FULUA_DATABASE_PATH": str(path)}):
                database.init_db()
                database.init_db()

            connection = sqlite3.connect(path)
            try:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(metric_results)")}
                stored = connection.execute("SELECT d, ra, rk FROM metric_results WHERE row_id = 1").fetchone()
                version = connection.execute("PRAGMA user_version").fetchone()[0]
            finally:
                connection.close()
            self.assertTrue({"ra", "rk"}.issubset(columns))
            self.assertEqual(stored, ("√", None, None))
            self.assertEqual(version, int(SCHEMA_VERSION))

    def test_schema_two_migration_repairs_deterministic_management_scores(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "app.db"
            with patch.dict(os.environ, {"FULUA_DATABASE_PATH": str(path)}):
                database.init_db()
                project = database.create_project("管理评分迁移")
                section = database.get_section(project["id"], "A-5")
                self.assertIsNotNone(section)
                with database.connect() as connection:
                    timestamp = database.utc_now()
                    first = connection.execute(
                        """
                        INSERT INTO assessment_rows
                            (section_id, unit, object_name, subsystem, record_text, sort_order, created_at, updated_at)
                        VALUES (?, '密钥管理规则', '管理体系', '', '记录', 1, ?, ?)
                        """,
                        (section["id"], timestamp, timestamp),
                    )
                    connection.execute(
                        "INSERT INTO metric_results (row_id, unit_score, compliance) VALUES (?, '1.5000', '符合')",
                        (first.lastrowid,),
                    )
                    second = connection.execute(
                        """
                        INSERT INTO assessment_rows
                            (section_id, unit, object_name, subsystem, record_text, sort_order, created_at, updated_at)
                        VALUES (?, '建立操作规程', '管理体系', '', '记录', 2, ?, ?)
                        """,
                        (section["id"], timestamp, timestamp),
                    )
                    connection.execute(
                        "INSERT INTO metric_results (row_id, unit_score, compliance) VALUES (?, '0.9000', NULL)",
                        (second.lastrowid,),
                    )
                    connection.execute("PRAGMA user_version = 2")

                database.init_db()
                database.init_db()
                with database.connect() as connection:
                    scores = connection.execute(
                        "SELECT unit_score FROM metric_results ORDER BY row_id"
                    ).fetchall()
                    version = connection.execute("PRAGMA user_version").fetchone()[0]

            self.assertEqual([row["unit_score"] for row in scores], ["1.0000", "0.9000"])
            self.assertEqual(version, int(SCHEMA_VERSION))

    def test_init_db_failure_does_not_promote_user_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "app.db"
            connection = sqlite3.connect(path)
            try:
                connection.execute("CREATE TABLE sentinel (value TEXT)")
                connection.execute("PRAGMA user_version = 0")
                connection.commit()
            finally:
                connection.close()
            with patch.dict(os.environ, {"FULUA_DATABASE_PATH": str(path)}), patch(
                "app.database._ensure_column", side_effect=RuntimeError("migration failed")
            ):
                with self.assertRaisesRegex(RuntimeError, "migration failed"):
                    database.init_db()
            connection = sqlite3.connect(path)
            try:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
                self.assertIsNotNone(connection.execute("SELECT name FROM sqlite_master WHERE name='sentinel'").fetchone())
            finally:
                connection.close()

    def test_init_db_rejects_database_newer_than_backend_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "app.db"
            connection = sqlite3.connect(path)
            try:
                connection.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION) + 1}")
            finally:
                connection.close()
            with patch.dict(os.environ, {"FULUA_DATABASE_PATH": str(path)}):
                with self.assertRaisesRegex(RuntimeError, "schema|版本"):
                    database.init_db()
