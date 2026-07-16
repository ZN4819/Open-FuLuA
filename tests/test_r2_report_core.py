from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app import database
from app.contracts import (
    FULL_REPORT_TEMPLATE_ASSET_SET_HASH,
    FULL_REPORT_TEMPLATE_EDITION,
    FULL_REPORT_TEMPLATE_PACKAGE_ID,
    FULL_REPORT_TEMPLATE_REVISION,
)
from app.report_core import initializer
from app.report_core.contracts import (
    REPORT_CORE_AUXILIARY_TABLES,
    REPORT_CORE_ENTITY_TABLES,
)
from app.report_core.initializer import (
    ReportDomainInitializationError,
    initialize_report_domain,
)
from app.runtime import SCHEMA_VERSION
from app.services.report_templates.registry import ReportTemplateUnavailable


class R2ReportCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "app.db"
        self.previous_database = os.environ.get("FULUA_DATABASE_PATH")
        os.environ["FULUA_DATABASE_PATH"] = str(self.database_path)
        database.init_db()

    def tearDown(self) -> None:
        if self.previous_database is None:
            os.environ.pop("FULUA_DATABASE_PATH", None)
        else:
            os.environ["FULUA_DATABASE_PATH"] = self.previous_database
        self.temporary.cleanup()

    @staticmethod
    def _full_report_arguments() -> dict[str, str]:
        return {
            "project_type": "full_report",
            "template_package_id": FULL_REPORT_TEMPLATE_PACKAGE_ID,
            "template_edition": FULL_REPORT_TEMPLATE_EDITION,
            "template_revision": FULL_REPORT_TEMPLATE_REVISION,
            "template_asset_set_hash": FULL_REPORT_TEMPLATE_ASSET_SET_HASH,
        }

    def _create_full_report(self, name: str = "完整报告") -> sqlite3.Row:
        return database.create_project(name, **self._full_report_arguments())

    def test_schema_five_contains_all_fifteen_entities_and_revision_columns(self) -> None:
        with database.connect() as connection:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertEqual(set(REPORT_CORE_ENTITY_TABLES) - tables, set())
            self.assertEqual(set(REPORT_CORE_AUXILIARY_TABLES) - tables, set())
            for table_name in REPORT_CORE_ENTITY_TABLES:
                columns = {
                    row["name"]
                    for row in connection.execute(f"PRAGMA table_info({table_name})")
                }
                self.assertIn("revision", columns, table_name)
            assessment_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(assessment_rows)")
            }
            self.assertIn("assessment_object_uuid", assessment_columns)
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                int(SCHEMA_VERSION),
            )

    def test_full_report_creation_initializes_singletons_tree_blocks_and_constants(self) -> None:
        project = self._create_full_report()
        with database.connect() as connection:
            singleton_counts = {
                table: connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE project_id = ?", (project["id"],)
                ).fetchone()[0]
                for table in (
                    "report_metadata",
                    "report_phase_dates",
                    "report_distribution",
                    "system_profiles",
                )
            }
            counts = {
                table: connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE project_id = ?", (project["id"],)
                ).fetchone()[0]
                for table in (
                    "report_organizations",
                    "report_standards",
                    "report_sections",
                    "report_blocks",
                )
            }
            root_keys = [
                row["section_key"]
                for row in connection.execute(
                    """
                    SELECT section_key FROM report_sections
                    WHERE project_id = ? AND parent_section_id IS NULL
                    ORDER BY sort_order
                    """,
                    (project["id"],),
                )
            ]
            bindings = connection.execute(
                """
                SELECT DISTINCT template_package_id, template_edition,
                       template_revision, template_asset_set_hash
                FROM report_sections WHERE project_id = ?
                """,
                (project["id"],),
            ).fetchall()

        self.assertEqual(singleton_counts, {name: 1 for name in singleton_counts})
        self.assertEqual(counts["report_organizations"], 1)
        self.assertEqual(counts["report_standards"], 5)
        self.assertEqual(counts["report_sections"], 109)
        self.assertEqual(counts["report_blocks"], 55)
        self.assertEqual(root_keys[-2:], ["appendix.a", "appendix.b"])
        self.assertEqual(len(bindings), 1)
        self.assertEqual(
            tuple(bindings[0]),
            (
                FULL_REPORT_TEMPLATE_PACKAGE_ID,
                FULL_REPORT_TEMPLATE_EDITION,
                FULL_REPORT_TEMPLATE_REVISION,
                FULL_REPORT_TEMPLATE_ASSET_SET_HASH,
            ),
        )

    def test_repeated_initialization_is_idempotent_and_keeps_binding(self) -> None:
        project = self._create_full_report()
        before_binding = tuple(
            project[name]
            for name in (
                "template_package_id",
                "template_edition",
                "template_revision",
                "template_asset_set_hash",
            )
        )
        with database.connect() as connection:
            first = initialize_report_domain(connection, project_id=project["id"])
            second = initialize_report_domain(connection, project_uuid=project["project_uuid"])
            counts = tuple(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE project_id = ?", (project["id"],)
                ).fetchone()[0]
                for table in ("report_sections", "report_blocks", "report_standards")
            )
            stored = connection.execute(
                "SELECT * FROM projects WHERE id = ?", (project["id"],)
            ).fetchone()
        after_binding = tuple(
            stored[name]
            for name in (
                "template_package_id",
                "template_edition",
                "template_revision",
                "template_asset_set_hash",
            )
        )
        self.assertEqual(first, second)
        self.assertEqual(counts, (109, 55, 5))
        self.assertEqual(after_binding, before_binding)

    def test_appendix_a_project_is_explicitly_rejected_without_hidden_rows(self) -> None:
        project = database.create_project("仅附录A")
        with database.connect() as connection:
            with self.assertRaises(ReportDomainInitializationError) as error:
                initialize_report_domain(connection, project_id=project["id"])
            count = connection.execute(
                "SELECT COUNT(*) FROM report_metadata WHERE project_id = ?", (project["id"],)
            ).fetchone()[0]
        self.assertEqual(error.exception.code, "REPORT_DOMAIN_NOT_AVAILABLE")
        self.assertEqual(count, 0)

    def test_reinitialization_preserves_manual_standard_and_manual_block(self) -> None:
        project = self._create_full_report()
        timestamp = database.utc_now()
        with database.connect() as connection:
            section = connection.execute(
                "SELECT id FROM report_sections WHERE project_id = ? AND section_key = 'chapter.1.1'",
                (project["id"],),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO report_standards (
                    standard_uuid, project_id, standard_kind, standard_name,
                    sort_order, created_at, updated_at
                ) VALUES (?, ?, 'manual', '人工标准', 100, ?, ?)
                """,
                (str(uuid.uuid4()), project["id"], timestamp, timestamp),
            )
            connection.execute(
                """
                INSERT INTO report_blocks (
                    block_uuid, project_id, section_id, block_key, block_type,
                    payload_json, source_kind, edit_policy, sort_order,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'paragraph', '{"text":"人工内容"}',
                          'manual', 'editable', 0, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    project["id"],
                    section["id"],
                    f"manual.{uuid.uuid4()}",
                    timestamp,
                    timestamp,
                ),
            )
            initialize_report_domain(connection, project_id=project["id"])
            counts = (
                connection.execute(
                    "SELECT COUNT(*) FROM report_standards WHERE project_id = ?",
                    (project["id"],),
                ).fetchone()[0],
                connection.execute(
                    "SELECT COUNT(*) FROM report_blocks WHERE project_id = ?",
                    (project["id"],),
                ).fetchone()[0],
            )
        self.assertEqual(counts, (6, 56))

    def test_bound_template_tampering_is_rejected_before_any_reinitialization(self) -> None:
        project = self._create_full_report()
        with database.connect() as connection:
            connection.execute("DELETE FROM report_blocks WHERE project_id = ?", (project["id"],))
            connection.execute("DROP TRIGGER projects_identity_immutable_guard")
            connection.execute(
                "UPDATE projects SET template_asset_set_hash = ? WHERE id = ?",
                ("f" * 64, project["id"]),
            )
        with database.connect() as connection:
            with self.assertRaises(ReportDomainInitializationError) as error:
                initialize_report_domain(connection, project_id=project["id"])
            count = connection.execute(
                "SELECT COUNT(*) FROM report_blocks WHERE project_id = ?", (project["id"],)
            ).fetchone()[0]
        self.assertEqual(error.exception.code, "BOUND_TEMPLATE_UNAVAILABLE")
        self.assertEqual(error.exception.details["reason"], "PROJECT_TEMPLATE_BINDING_MISMATCH")
        self.assertEqual(count, 0)

    def test_unavailable_registry_rolls_back_project_creation(self) -> None:
        with patch.object(
            initializer.report_template_registry,
            "load",
            side_effect=ReportTemplateUnavailable("REPORT_TEMPLATE_HASH_MISMATCH", "manifest.json"),
        ):
            with self.assertRaises(ReportDomainInitializationError) as error:
                self._create_full_report("不可用母版")
        with database.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM projects WHERE name = '不可用母版'"
            ).fetchone()[0]
        self.assertEqual(error.exception.code, "BOUND_TEMPLATE_UNAVAILABLE")
        self.assertEqual(count, 0)

    def test_initializer_failure_rolls_back_every_insert_in_its_savepoint(self) -> None:
        project = self._create_full_report()
        tables = (
            "report_blocks",
            "report_sections",
            "report_standards",
            "report_metadata",
            "report_phase_dates",
            "report_distribution",
            "system_profiles",
            "report_organizations",
        )
        with database.connect() as connection:
            for table in tables:
                connection.execute(f"DELETE FROM {table} WHERE project_id = ?", (project["id"],))

        with database.connect() as connection, patch.object(
            initializer,
            "_verify_initialized_counts",
            side_effect=RuntimeError("INJECTED_INITIALIZER_FAILURE"),
        ):
            with self.assertRaisesRegex(RuntimeError, "INJECTED_INITIALIZER_FAILURE"):
                initialize_report_domain(connection, project_id=project["id"])

        with database.connect() as connection:
            counts = {
                table: connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE project_id = ?", (project["id"],)
                ).fetchone()[0]
                for table in tables
            }
        self.assertEqual(counts, {table: 0 for table in tables})

    def test_invalid_semantic_manifest_rolls_back_without_partial_tree(self) -> None:
        project = self._create_full_report()
        with database.connect() as connection:
            connection.execute("DELETE FROM report_blocks WHERE project_id = ?", (project["id"],))
            connection.execute("DELETE FROM report_sections WHERE project_id = ?", (project["id"],))

        invalid = initializer._load_r2_template_manifest()
        invalid = json.loads(json.dumps(invalid))
        invalid["blocks"] = invalid["blocks"][:-1]
        with database.connect() as connection, patch.object(
            initializer, "_load_r2_template_manifest", return_value=invalid
        ):
            with self.assertRaises(ReportDomainInitializationError) as error:
                initialize_report_domain(connection, project_id=project["id"])

        with database.connect() as connection:
            counts = tuple(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE project_id = ?", (project["id"],)
                ).fetchone()[0]
                for table in ("report_sections", "report_blocks")
            )
        self.assertEqual(error.exception.code, "BOUND_TEMPLATE_UNAVAILABLE")
        self.assertEqual(counts, (0, 0))

    def test_schema_four_full_report_is_initialized_during_upgrade(self) -> None:
        project = self._create_full_report()
        with database.connect() as connection:
            for table in (
                "report_blocks",
                "report_sections",
                "report_standards",
                "report_metadata",
                "report_phase_dates",
                "report_distribution",
                "system_profiles",
                "report_organizations",
            ):
                connection.execute(f"DELETE FROM {table} WHERE project_id = ?", (project["id"],))
            connection.execute("PRAGMA user_version = 4")

        database.init_db()
        with database.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            counts = tuple(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE project_id = ?", (project["id"],)
                ).fetchone()[0]
                for table in ("report_metadata", "report_sections", "report_blocks")
            )
        self.assertEqual(version, int(SCHEMA_VERSION))
        self.assertEqual(counts, (1, 109, 55))

    def test_schema_four_upgrade_failure_rolls_back_tables_and_version(self) -> None:
        self._create_full_report()
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("PRAGMA foreign_keys = OFF")
            for table in (
                *REPORT_CORE_AUXILIARY_TABLES,
                *reversed(REPORT_CORE_ENTITY_TABLES),
            ):
                connection.execute(f"DROP TABLE {table}")
            connection.execute("PRAGMA user_version = 4")
            connection.commit()
        finally:
            connection.close()

        with patch.object(
            initializer,
            "_validate_manifest",
            side_effect=RuntimeError("INJECTED_SCHEMA_FIVE_FAILURE"),
        ):
            with self.assertRaisesRegex(RuntimeError, "INJECTED_SCHEMA_FIVE_FAILURE"):
                database.init_db()

        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)
            self.assertIsNone(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'report_metadata'"
                ).fetchone()
            )
        finally:
            connection.close()

    def test_schema_five_claim_with_missing_entity_is_rejected_without_repair(self) -> None:
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("DROP TABLE report_blocks")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(RuntimeError, "REPORT_CORE_SCHEMA_INCOMPLETE"):
            database.init_db()
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                int(SCHEMA_VERSION),
            )
            self.assertIsNone(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'report_blocks'"
                ).fetchone()
            )
        finally:
            connection.close()

    def test_schema_five_claim_with_missing_constraint_index_is_not_silently_repaired(self) -> None:
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("DROP INDEX idx_report_blocks_section_order")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(RuntimeError, "REPORT_CORE_SCHEMA_INCOMPLETE"):
            database.init_db()
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertIsNone(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'idx_report_blocks_section_order'"
                ).fetchone()
            )
        finally:
            connection.close()

    def test_project_delete_cascades_all_report_entities(self) -> None:
        project = self._create_full_report()
        with database.connect() as connection:
            connection.execute(
                """
                INSERT INTO report_warning_confirmations (
                    project_id, relation_id, entity_path, warning_code,
                    source_hash, confirmed_at
                ) VALUES (?, 'r2.test', 'system_profiles.system_name',
                          'TEST_WARNING', ?, ?)
                """,
                (project["id"], "a" * 64, database.utc_now()),
            )
        database.delete_project(project["id"])
        with database.connect() as connection:
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in REPORT_CORE_ENTITY_TABLES
            }
            auxiliary_count = connection.execute(
                "SELECT COUNT(*) FROM report_warning_confirmations"
            ).fetchone()[0]
        self.assertEqual(counts, {table: 0 for table in REPORT_CORE_ENTITY_TABLES})
        self.assertEqual(auxiliary_count, 0)

    def test_database_constraints_reject_invalid_revision_json_and_self_relation(self) -> None:
        project = self._create_full_report()
        timestamp = database.utc_now()
        with database.connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO system_crypto_products (
                        product_uuid, project_id, revision, created_at, updated_at
                    ) VALUES (?, ?, 0, ?, ?)
                    """,
                    (str(uuid.uuid4()), project["id"], timestamp, timestamp),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE system_profiles SET service_scope_json = 'not-json' WHERE project_id = ?",
                    (project["id"],),
                )
            object_uuid = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO assessment_objects (
                    object_uuid, project_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (object_uuid, project["id"], timestamp, timestamp),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO object_relations (
                        relation_uuid, project_id, source_object_uuid,
                        target_object_uuid, relation_type, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'contains', ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        project["id"],
                        object_uuid,
                        object_uuid,
                        timestamp,
                        timestamp,
                    ),
                )


if __name__ == "__main__":
    unittest.main()
