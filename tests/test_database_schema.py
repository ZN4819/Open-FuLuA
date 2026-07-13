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
            self.assertEqual(version, 2)

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
