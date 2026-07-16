from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app import database
from app.runtime import SCHEMA_VERSION


class DatabaseSchemaTests(unittest.TestCase):
    def test_schema_nine_creates_queryable_report_import_tables_idempotently(self) -> None:
        expected_columns = {
            "report_import_jobs": {
                "id",
                "mode",
                "status",
                "job_revision",
                "original_name",
                "source_docx_path",
                "source_sha256",
                "detected_edition",
                "detected_revision",
                "fingerprint_json",
                "parsed_json_path",
                "summary_json",
                "appendix_a_source",
                "created_project_id",
                "created_at",
                "started_at",
                "finished_at",
                "error_message",
            },
            "report_import_issues": {
                "id",
                "job_id",
                "code",
                "severity",
                "association_id",
                "authority_field_id",
                "field_path",
                "source_locator",
                "original_text",
                "source_value_hash",
                "candidate_value_json",
                "confidence",
                "status",
                "revision",
                "created_at",
                "updated_at",
            },
            "report_import_resolutions": {
                "id",
                "job_id",
                "issue_id",
                "association_id",
                "authority_field_id",
                "field_path",
                "action",
                "resolved_value_json",
                "resolved_by_user",
                "issue_revision",
                "created_at",
                "updated_at",
            },
            "report_field_sources": {
                "id",
                "project_id",
                "report_import_job_id",
                "association_id",
                "authority_field_id",
                "field_path",
                "source_kind",
                "source_locator",
                "source_value_hash",
                "original_text",
                "confidence",
                "mapping_status",
                "needs_confirmation",
                "created_at",
                "updated_at",
            },
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "app.db"
            with patch.dict(os.environ, {"FULUA_DATABASE_PATH": str(path)}):
                database.init_db()
                database.init_db()

            connection = sqlite3.connect(path)
            try:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 9)
                for table_name, required in expected_columns.items():
                    actual = {
                        row[1]
                        for row in connection.execute(f"PRAGMA table_info({table_name})")
                    }
                    self.assertTrue(
                        required.issubset(actual),
                        f"{table_name} 缺少字段：{sorted(required - actual)}",
                    )
                issue_foreign_keys = {
                    (row[2], row[3], row[4], row[6])
                    for row in connection.execute("PRAGMA foreign_key_list(report_import_issues)")
                }
                resolution_foreign_keys = {
                    (row[2], row[3], row[4], row[6])
                    for row in connection.execute("PRAGMA foreign_key_list(report_import_resolutions)")
                }
                source_foreign_keys = {
                    (row[2], row[3], row[4], row[6])
                    for row in connection.execute("PRAGMA foreign_key_list(report_field_sources)")
                }
            finally:
                connection.close()

        self.assertIn(("report_import_jobs", "job_id", "id", "CASCADE"), issue_foreign_keys)
        self.assertIn(("report_import_jobs", "job_id", "id", "CASCADE"), resolution_foreign_keys)
        self.assertIn(("report_import_issues", "issue_id", "id", "CASCADE"), resolution_foreign_keys)
        self.assertIn(("projects", "project_id", "id", "CASCADE"), source_foreign_keys)

    def test_schema_eight_upgrade_preserves_projects_and_adds_report_import_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "app.db"
            with patch.dict(os.environ, {"FULUA_DATABASE_PATH": str(path)}):
                database.init_db()
                project = database.create_project("Schema 8 保留项目")

            connection = sqlite3.connect(path)
            try:
                for table_name in (
                    "report_import_resolutions",
                    "report_import_issues",
                    "report_field_sources",
                    "report_import_jobs",
                ):
                    connection.execute(f"DROP TABLE IF EXISTS {table_name}")
                connection.execute("PRAGMA user_version = 8")
                connection.commit()
            finally:
                connection.close()

            with patch.dict(os.environ, {"FULUA_DATABASE_PATH": str(path)}):
                database.init_db()
                migrated = database.get_project_by_id(project["id"])

            connection = sqlite3.connect(path)
            try:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                version = connection.execute("PRAGMA user_version").fetchone()[0]
            finally:
                connection.close()

        self.assertEqual(migrated["name"], "Schema 8 保留项目")
        self.assertEqual(version, 9)
        self.assertTrue(
            {
                "report_import_jobs",
                "report_import_issues",
                "report_import_resolutions",
                "report_field_sources",
            }.issubset(tables)
        )

    def test_schema_three_failure_rolls_back_all_identity_schema_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "app.db"
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    """
                    CREATE TABLE projects (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO projects (name, created_at, updated_at) VALUES ('旧项目', 't', 't')"
                )
                connection.execute("PRAGMA user_version = 3")
                connection.commit()
            finally:
                connection.close()

            with patch.dict(os.environ, {"FULUA_DATABASE_PATH": str(path)}), patch.object(
                database,
                "_audit_project_identity_rows",
                side_effect=RuntimeError("INJECTED_SCHEMA_FAILURE"),
            ):
                with self.assertRaisesRegex(RuntimeError, "INJECTED_SCHEMA_FAILURE"):
                    database.init_db()

            connection = sqlite3.connect(path)
            try:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)
                self.assertEqual(
                    {row[1] for row in connection.execute("PRAGMA table_info(projects)")},
                    {"id", "name", "created_at", "updated_at"},
                )
                schema_objects = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE name IN "
                        "('app_metadata', 'project_upgrade_operations', "
                        "'projects_identity_insert_guard', 'idx_projects_project_uuid')"
                    )
                }
                self.assertEqual(schema_objects, set())
                self.assertEqual(
                    connection.execute("SELECT id, name FROM projects").fetchall(),
                    [(1, "旧项目")],
                )
            finally:
                connection.close()

    def test_schema_four_migration_failure_rolls_back_without_version_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "app.db"
            with patch.dict(os.environ, {"FULUA_DATABASE_PATH": str(path)}):
                database.init_db()
                first = database.create_project("第一个项目")
                second = database.create_project("第二个项目")
            duplicate_uuid = first["project_uuid"]
            connection = sqlite3.connect(path)
            try:
                connection.execute("DROP TRIGGER projects_identity_immutable_guard")
                connection.execute("DROP INDEX idx_projects_project_uuid")
                connection.execute(
                    "UPDATE projects SET project_uuid = ? WHERE id = ?",
                    (duplicate_uuid, second["id"]),
                )
                connection.execute("PRAGMA user_version = 3")
                connection.commit()
            finally:
                connection.close()

            with patch.dict(os.environ, {"FULUA_DATABASE_PATH": str(path)}):
                with self.assertRaises(sqlite3.IntegrityError):
                    database.init_db()

            connection = sqlite3.connect(path)
            try:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM projects WHERE project_uuid = ?",
                        (duplicate_uuid,),
                    ).fetchone()[0],
                    2,
                )
            finally:
                connection.close()

    def test_schema_three_projects_receive_stable_uuid_and_legacy_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "app.db"
            with patch.dict(os.environ, {"FULUA_DATABASE_PATH": str(path)}):
                database.init_db()
                original = database.create_project("schema 3 项目")

            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                for trigger in (
                    "projects_identity_insert_guard",
                    "projects_identity_update_guard",
                    "projects_identity_immutable_guard",
                    "report_evidence_category_project_guard",
                ):
                    connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
                connection.execute("DROP TABLE project_upgrade_operations")
                connection.execute("DROP TABLE app_metadata")
                connection.execute(
                    """
                    CREATE TABLE projects_schema3 (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO projects_schema3 (id, name, created_at, updated_at)
                    SELECT id, name, created_at, updated_at FROM projects
                    """
                )
                connection.execute("DROP TABLE projects")
                connection.execute("ALTER TABLE projects_schema3 RENAME TO projects")
                connection.execute("PRAGMA user_version = 3")
                connection.commit()
            finally:
                connection.close()

            with patch.dict(os.environ, {"FULUA_DATABASE_PATH": str(path)}):
                database.init_db()
                migrated = database.get_project_by_id(original["id"])
                first_uuid = migrated["project_uuid"]
                database.init_db()
                repeated = database.get_project_by_id(original["id"])
                section_count = len(database.list_sections(original["id"]))

            uuid.UUID(first_uuid)
            self.assertEqual(repeated["project_uuid"], first_uuid)
            self.assertEqual(repeated["project_type"], "appendix_a")
            self.assertEqual(repeated["workflow_status"], "draft")
            self.assertEqual(repeated["created_by_operation"], "create")
            self.assertIsNone(repeated["template_package_id"])
            self.assertEqual(section_count, 8)

    def test_schema_four_rejects_forged_full_report_template_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "app.db"
            with patch.dict(os.environ, {"FULUA_DATABASE_PATH": str(path)}):
                database.init_db()
                project = database.create_project(
                    "完整报告",
                    project_type="full_report",
                    template_package_id="report-2023-2025.12.08",
                    template_edition="2023",
                    template_revision="2025-12-08",
                    template_asset_set_hash=(
                        "9017b86afd44a9ba05c55e3eb880d60b4dd6e45fbf87dd1b020bb5bc130d1484"
                    ),
                )

            connection = sqlite3.connect(path)
            try:
                connection.execute("DROP TRIGGER projects_identity_immutable_guard")
                connection.execute(
                    "UPDATE projects SET template_package_id = 'evil', "
                    "template_asset_set_hash = ? WHERE id = ?",
                    ("f" * 64, project["id"]),
                )
                connection.commit()
            finally:
                connection.close()

            with patch.dict(os.environ, {"FULUA_DATABASE_PATH": str(path)}):
                with self.assertRaisesRegex(RuntimeError, "PROJECT_IDENTITY_AUDIT_FAILED"):
                    database.init_db()

            connection = sqlite3.connect(path)
            try:
                stored = connection.execute(
                    "SELECT template_package_id, template_asset_set_hash FROM projects WHERE id = ?",
                    (project["id"],),
                ).fetchone()
                self.assertEqual(stored, ("evil", "f" * 64))
                self.assertEqual(
                    connection.execute("PRAGMA user_version").fetchone()[0],
                    int(SCHEMA_VERSION),
                )
            finally:
                connection.close()

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
                    evidence_columns = {
                        row[1]
                        for row in connection.execute("PRAGMA table_info(evidence_images)")
                    }
                finally:
                    connection.close()
                self.assertIn("ra", columns)
                self.assertIn("rk", columns)
                self.assertIn("evidence_uuid", evidence_columns)
                project_connection = sqlite3.connect(path)
                try:
                    project_columns = {
                        row[1]
                        for row in project_connection.execute("PRAGMA table_info(projects)")
                    }
                finally:
                    project_connection.close()
                self.assertTrue(
                    {
                        "project_uuid",
                        "project_type",
                        "workflow_status",
                        "template_package_id",
                        "template_edition",
                        "template_revision",
                        "template_asset_set_hash",
                        "source_project_uuid",
                        "created_by_operation",
                    }.issubset(project_columns)
                )

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
