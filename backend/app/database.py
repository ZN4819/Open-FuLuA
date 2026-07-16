from __future__ import annotations

import json
import re
import shutil
import uuid
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import settings
from .contracts import (
    FULL_REPORT_TEMPLATE_ASSET_SET_HASH,
    FULL_REPORT_TEMPLATE_EDITION,
    FULL_REPORT_TEMPLATE_PACKAGE_ID,
    FULL_REPORT_TEMPLATE_REVISION,
    PROJECT_CREATION_OPERATIONS,
    PROJECT_TYPES,
    WORKFLOW_STATUSES,
)
from .runtime import SCHEMA_VERSION
from .report_core.initializer import (
    initialize_existing_full_report_projects,
    initialize_report_domain,
)
from .report_core.schema import audit_report_core_schema, ensure_report_core_schema
from .report_derived.schema import (
    audit_report_derived_schema,
    ensure_report_derived_schema,
    initialize_existing_report_derived_states,
    initialize_report_derived_state,
)
from .report_export.schema import (
    audit_report_export_schema,
    ensure_report_export_schema,
    invalidate_pre_r4_projection_contexts,
)
from .report_evidence.schema import (
    audit_report_evidence_schema,
    ensure_report_evidence_schema,
    initialize_existing_report_evidence_categories,
    initialize_report_evidence_categories,
)
from .report_import.schema import audit_report_import_schema, ensure_report_import_schema
from .services.scoring import (
    MANAGEMENT_SECTION_CODES,
    TECHNICAL_SECTION_CODES,
    calculate_flat_management_rows,
    calculate_flat_technical_rows,
    calculate_management_rows,
    calculate_technical_rows,
)
from .services.template_profile import load_template_profile


FIG_TOKEN_RE = re.compile(r"\[\[FIG:(\d+)\]\]")
PROJECT_UUID_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL,
    "https://github.com/ZN4819/Open-FuLuA/projects",
)
EVIDENCE_UUID_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL,
    "https://github.com/ZN4819/Open-FuLuA/evidence-images",
)


SECTION_SEED = [
    ("A-1", "物理和环境安全", "表A-1物理和环境安全测评结果记录", 1),
    ("A-2", "网络和通信安全", "表A-2网络和通信安全测评结果记录", 2),
    ("A-3", "设备和计算安全", "表A-3设备和计算安全测评结果记录", 3),
    ("A-4", "应用和数据安全", "表A-4应用和数据安全测评结果记录", 4),
    ("A-5", "管理制度", "表A-5管理制度测评结果记录", 5),
    ("A-6", "人员管理", "表A-6人员管理测评结果记录", 6),
    ("A-7", "建设运行", "表A-7建设运行测评结果记录", 7),
    ("A-8", "应急处置", "表A-8应急处置测评结果记录", 8),
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_database_path() -> Path:
    return settings.database_path


def read_schema_version(path: Path | None = None, *, readonly: bool = False) -> str:
    database_path = path or current_database_path()
    if readonly:
        uri = f"{database_path.resolve(strict=True).as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
    else:
        connection = sqlite3.connect(database_path)
    try:
        return str(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()


def ensure_database_dir(path: Path | None = None) -> None:
    database_path = path or current_database_path()
    database_path.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    database_path = current_database_path()
    ensure_database_dir(database_path)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    with connect() as db:
        target_version = int(SCHEMA_VERSION)
        current_version = int(db.execute("PRAGMA user_version").fetchone()[0])
        if current_version > target_version:
            raise RuntimeError("数据库 schema 版本高于当前后端目标版本")
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                project_uuid TEXT NOT NULL,
                project_type TEXT NOT NULL DEFAULT 'appendix_a',
                workflow_status TEXT NOT NULL DEFAULT 'draft',
                template_package_id TEXT,
                template_edition TEXT,
                template_revision TEXT,
                template_asset_set_hash TEXT,
                source_project_uuid TEXT,
                created_by_operation TEXT NOT NULL DEFAULT 'create',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS assessment_rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                section_id INTEGER NOT NULL,
                unit TEXT NOT NULL DEFAULT '',
                object_name TEXT NOT NULL DEFAULT '',
                subsystem TEXT NOT NULL DEFAULT '',
                record_text TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(section_id) REFERENCES appendix_sections(id) ON DELETE CASCADE
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS metric_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                row_id INTEGER NOT NULL UNIQUE,
                d TEXT,
                a TEXT,
                k TEXT,
                ra TEXT,
                rk TEXT,
                object_score TEXT,
                unit_score TEXT,
                compliance TEXT,
                FOREIGN KEY(row_id) REFERENCES assessment_rows(id) ON DELETE CASCADE
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS evidence_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                evidence_uuid TEXT,
                project_id INTEGER NOT NULL,
                section_code TEXT NOT NULL,
                file_path TEXT NOT NULL,
                original_name TEXT NOT NULL DEFAULT '',
                caption TEXT NOT NULL DEFAULT '',
                alt_text TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL,
                pixel_width INTEGER,
                pixel_height INTEGER,
                dpi_x REAL,
                dpi_y REAL,
                display_width_in REAL,
                display_height_in REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS cross_references (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_row_id INTEGER NOT NULL,
                target_image_id INTEGER,
                token TEXT NOT NULL,
                display_text TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(source_row_id) REFERENCES assessment_rows(id) ON DELETE CASCADE,
                FOREIGN KEY(target_image_id) REFERENCES evidence_images(id) ON DELETE SET NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS render_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'editable',
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                output_docx_path TEXT,
                output_pdf_path TEXT,
                page_count INTEGER,
                log_path TEXT,
                error_message TEXT,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS validation_issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                severity TEXT NOT NULL,
                code TEXT NOT NULL,
                message TEXT NOT NULL,
                target_type TEXT,
                target_id TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS docx_import_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL,
                original_name TEXT NOT NULL DEFAULT '',
                source_docx_path TEXT NOT NULL DEFAULT '',
                parsed_json_path TEXT,
                created_project_id INTEGER,
                summary_json TEXT NOT NULL DEFAULT '{}',
                issues_json TEXT NOT NULL DEFAULT '[]',
                error_message TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                FOREIGN KEY(created_project_id) REFERENCES projects(id) ON DELETE SET NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS record_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                template_key TEXT NOT NULL UNIQUE,
                source_type TEXT NOT NULL DEFAULT 'system',
                section_code TEXT NOT NULL,
                table_type TEXT NOT NULL,
                unit TEXT NOT NULL DEFAULT '',
                object_name TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                record_text TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '[]',
                source_row INTEGER,
                is_enabled INTEGER NOT NULL DEFAULT 1,
                deleted_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_record_templates_section_unit
            ON record_templates(section_code, unit)
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_record_templates_source_type
            ON record_templates(source_type)
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_record_templates_enabled
            ON record_templates(is_enabled, deleted_at)
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS record_template_slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                section_code TEXT NOT NULL,
                table_type TEXT NOT NULL,
                unit TEXT NOT NULL DEFAULT '',
                template_group TEXT NOT NULL DEFAULT 'verification_record',
                template_type TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                record_text TEXT NOT NULL DEFAULT '',
                default_record_text TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '[]',
                is_customized INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(section_code, unit, template_group, template_type)
            )
            """
        )
        _ensure_record_template_slots_schema(db)
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_record_template_slots_section_unit
            ON record_template_slots(section_code, unit)
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_record_template_slots_group_type
            ON record_template_slots(template_group, template_type)
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_record_template_slots_type
            ON record_template_slots(template_type)
            """
        )
        _ensure_column(db, "render_jobs", "page_count", "INTEGER")
        _ensure_column(db, "render_jobs", "log_path", "TEXT")
        _ensure_column(db, "assessment_rows", "subsystem", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(db, "metric_results", "ra", "TEXT")
        _ensure_column(db, "metric_results", "rk", "TEXT")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS appendix_sections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                title TEXT NOT NULL,
                table_title TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                UNIQUE(project_id, code),
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS section_subsystems (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                section_code TEXT NOT NULL,
                name TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(project_id, section_code, name),
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_section_subsystems_section
            ON section_subsystems(project_id, section_code, sort_order)
            """
        )
        _ensure_project_identity_schema(db, audit_existing=current_version >= 4)
        _ensure_evidence_identity_schema(db)
        if current_version >= 5:
            audit_report_core_schema(db)
        ensure_report_core_schema(db)
        initialize_existing_full_report_projects(db)
        if current_version >= 6:
            audit_report_derived_schema(db)
        ensure_report_derived_schema(db)
        initialize_existing_report_derived_states(db)
        if current_version >= 7:
            audit_report_export_schema(db)
        ensure_report_export_schema(db)
        if current_version == 6:
            invalidate_pre_r4_projection_contexts(db)
        if current_version >= 8:
            audit_report_evidence_schema(db)
        ensure_report_evidence_schema(db)
        initialize_existing_report_evidence_categories(db)
        if current_version >= 9:
            audit_report_import_schema(db)
        ensure_report_import_schema(db)
        if current_version < 3:
            _migrate_management_unit_scores(db)
        db.execute(f"PRAGMA user_version = {target_version}")


def create_project(
    name: str,
    *,
    project_type: str = "appendix_a",
    workflow_status: str = "draft",
    template_package_id: str | None = None,
    template_edition: str | None = None,
    template_revision: str | None = None,
    template_asset_set_hash: str | None = None,
    source_project_uuid: str | None = None,
    created_by_operation: str = "create",
    project_uuid: str | None = None,
) -> sqlite3.Row:
    with connect() as db:
        return _insert_project(
            db,
            name=name,
            project_type=project_type,
            workflow_status=workflow_status,
            template_package_id=template_package_id,
            template_edition=template_edition,
            template_revision=template_revision,
            template_asset_set_hash=template_asset_set_hash,
            source_project_uuid=source_project_uuid,
            created_by_operation=created_by_operation,
            project_uuid=project_uuid,
        )


def _ensure_column(db: sqlite3.Connection, table_name: str, column_name: str, column_sql: str) -> None:
    columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()}
    if column_name not in columns:
        db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")


def new_evidence_uuid() -> str:
    return str(uuid.uuid4())


def _ensure_evidence_identity_schema(db: sqlite3.Connection) -> None:
    _ensure_column(db, "evidence_images", "evidence_uuid", "TEXT")
    rows = db.execute(
        """
        SELECT e.id, e.project_id, e.evidence_uuid, p.project_uuid
        FROM evidence_images e
        JOIN projects p ON p.id = e.project_id
        ORDER BY e.id
        """
    ).fetchall()
    seen: set[str] = set()
    for row in rows:
        value = str(row["evidence_uuid"] or "").strip()
        if value:
            try:
                uuid.UUID(value)
            except ValueError as exc:
                raise RuntimeError("EVIDENCE_UUID_INVALID") from exc
            if value in seen:
                raise RuntimeError("EVIDENCE_UUID_DUPLICATE")
        else:
            value = str(
                uuid.uuid5(
                    EVIDENCE_UUID_NAMESPACE,
                    f"{row['project_uuid']}:{row['id']}",
                )
            )
            db.execute(
                "UPDATE evidence_images SET evidence_uuid = ? WHERE id = ?",
                (value, row["id"]),
            )
        seen.add(value)
    db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_images_uuid
        ON evidence_images(evidence_uuid)
        WHERE evidence_uuid IS NOT NULL
        """
    )


def _ensure_project_identity_schema(
    db: sqlite3.Connection,
    *,
    audit_existing: bool,
) -> None:
    if audit_existing:
        existing_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(projects)").fetchall()
        }
        required_columns = {
            "project_uuid",
            "project_type",
            "workflow_status",
            "template_package_id",
            "template_edition",
            "template_revision",
            "template_asset_set_hash",
            "source_project_uuid",
            "created_by_operation",
        }
        if not required_columns <= existing_columns:
            raise RuntimeError("PROJECT_IDENTITY_SCHEMA_INCOMPLETE")
    _ensure_column(db, "projects", "project_uuid", "TEXT")
    _ensure_column(db, "projects", "project_type", "TEXT NOT NULL DEFAULT 'appendix_a'")
    _ensure_column(db, "projects", "workflow_status", "TEXT NOT NULL DEFAULT 'draft'")
    _ensure_column(db, "projects", "template_package_id", "TEXT")
    _ensure_column(db, "projects", "template_edition", "TEXT")
    _ensure_column(db, "projects", "template_revision", "TEXT")
    _ensure_column(db, "projects", "template_asset_set_hash", "TEXT")
    _ensure_column(db, "projects", "source_project_uuid", "TEXT")
    _ensure_column(db, "projects", "created_by_operation", "TEXT NOT NULL DEFAULT 'create'")

    if audit_existing:
        _audit_project_identity_rows(db)

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS app_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    metadata = db.execute(
        "SELECT value FROM app_metadata WHERE key = 'database_instance_uuid'"
    ).fetchone()
    if metadata is None:
        timestamp = utc_now()
        instance_uuid = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO app_metadata (key, value, created_at, updated_at)
            VALUES ('database_instance_uuid', ?, ?, ?)
            """,
            (instance_uuid, timestamp, timestamp),
        )
    else:
        instance_uuid = str(metadata["value"])

    db.execute(
        "UPDATE projects SET project_type = 'appendix_a' WHERE project_type IS NULL OR TRIM(project_type) = ''"
    )
    db.execute(
        "UPDATE projects SET workflow_status = 'draft' WHERE workflow_status IS NULL OR TRIM(workflow_status) = ''"
    )
    db.execute(
        "UPDATE projects SET created_by_operation = 'create' "
        "WHERE created_by_operation IS NULL OR TRIM(created_by_operation) = ''"
    )
    for project in db.execute(
        "SELECT id, project_uuid FROM projects ORDER BY id"
    ).fetchall():
        existing_uuid = str(project["project_uuid"] or "").strip()
        if existing_uuid:
            try:
                uuid.UUID(existing_uuid)
            except ValueError as exc:
                raise RuntimeError("PROJECT_UUID_INVALID") from exc
            continue
        stable_uuid = str(
            uuid.uuid5(PROJECT_UUID_NAMESPACE, f"{instance_uuid}:{int(project['id'])}")
        )
        db.execute(
            "UPDATE projects SET project_uuid = ? WHERE id = ?",
            (stable_uuid, int(project["id"])),
        )

    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_project_uuid ON projects(project_uuid)"
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS project_upgrade_operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_project_uuid TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            target_project_id INTEGER,
            status TEXT NOT NULL,
            lease_id TEXT,
            error_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(source_project_uuid, idempotency_key),
            FOREIGN KEY(target_project_id) REFERENCES projects(id) ON DELETE SET NULL,
            CHECK(status IN ('pending', 'completed', 'failed', 'failed_cleanup'))
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_project_upgrade_target "
        "ON project_upgrade_operations(target_project_id)"
    )
    _ensure_column(db, "project_upgrade_operations", "lease_id", "TEXT")

    for trigger_name in (
        "projects_identity_insert_guard",
        "projects_identity_update_guard",
        "projects_identity_immutable_guard",
    ):
        db.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
    project_types = ", ".join(f"'{value}'" for value in PROJECT_TYPES)
    workflow_statuses = ", ".join(f"'{value}'" for value in WORKFLOW_STATUSES)
    creation_operations = ", ".join(f"'{value}'" for value in PROJECT_CREATION_OPERATIONS)
    db.execute(
        f"""
        CREATE TRIGGER projects_identity_insert_guard
        BEFORE INSERT ON projects
        BEGIN
            SELECT CASE
                WHEN NEW.project_uuid IS NULL OR TRIM(NEW.project_uuid) = ''
                THEN RAISE(ABORT, 'PROJECT_UUID_REQUIRED')
            END;
            SELECT CASE
                WHEN NEW.project_type NOT IN ({project_types})
                THEN RAISE(ABORT, 'PROJECT_TYPE_INVALID')
            END;
            SELECT CASE
                WHEN NEW.workflow_status NOT IN ({workflow_statuses})
                THEN RAISE(ABORT, 'WORKFLOW_STATUS_INVALID')
            END;
            SELECT CASE
                WHEN NEW.created_by_operation NOT IN ({creation_operations})
                THEN RAISE(ABORT, 'PROJECT_CREATION_OPERATION_INVALID')
            END;
            SELECT CASE
                WHEN NEW.project_type = 'appendix_a' AND (
                    NEW.template_package_id IS NOT NULL OR
                    NEW.template_edition IS NOT NULL OR
                    NEW.template_revision IS NOT NULL OR
                    NEW.template_asset_set_hash IS NOT NULL OR
                    NEW.source_project_uuid IS NOT NULL
                )
                THEN RAISE(ABORT, 'APPENDIX_A_TEMPLATE_BINDING_FORBIDDEN')
            END;
            SELECT CASE
                WHEN NEW.project_type = 'full_report' AND (
                    NEW.template_package_id IS NULL OR
                    NEW.template_package_id <> '{FULL_REPORT_TEMPLATE_PACKAGE_ID}' OR
                    NEW.template_edition IS NULL OR
                    NEW.template_edition <> '{FULL_REPORT_TEMPLATE_EDITION}' OR
                    NEW.template_revision IS NULL OR
                    NEW.template_revision <> '{FULL_REPORT_TEMPLATE_REVISION}' OR
                    NEW.template_asset_set_hash IS NULL OR
                    NEW.template_asset_set_hash <> '{FULL_REPORT_TEMPLATE_ASSET_SET_HASH}'
                )
                THEN RAISE(ABORT, 'FULL_REPORT_TEMPLATE_BINDING_INVALID')
            END;
            SELECT CASE
                WHEN NEW.created_by_operation = 'upgrade_copy' AND (
                    NEW.project_type <> 'full_report' OR NEW.source_project_uuid IS NULL OR
                    TRIM(NEW.source_project_uuid) = ''
                )
                THEN RAISE(ABORT, 'UPGRADE_SOURCE_REQUIRED')
            END;
            SELECT CASE
                WHEN NEW.created_by_operation <> 'upgrade_copy' AND NEW.source_project_uuid IS NOT NULL
                THEN RAISE(ABORT, 'UPGRADE_SOURCE_FORBIDDEN')
            END;
        END
        """
    )
    db.execute(
        f"""
        CREATE TRIGGER projects_identity_update_guard
        BEFORE UPDATE ON projects
        BEGIN
            SELECT CASE
                WHEN NEW.project_type NOT IN ({project_types})
                THEN RAISE(ABORT, 'PROJECT_TYPE_INVALID')
            END;
            SELECT CASE
                WHEN NEW.workflow_status NOT IN ({workflow_statuses})
                THEN RAISE(ABORT, 'WORKFLOW_STATUS_INVALID')
            END;
        END
        """
    )
    db.execute(
        """
        CREATE TRIGGER projects_identity_immutable_guard
        BEFORE UPDATE OF
            project_uuid,
            project_type,
            template_package_id,
            template_edition,
            template_revision,
            template_asset_set_hash,
            source_project_uuid,
            created_by_operation
        ON projects
        WHEN
            OLD.project_uuid IS NOT NEW.project_uuid OR
            OLD.project_type IS NOT NEW.project_type OR
            OLD.template_package_id IS NOT NEW.template_package_id OR
            OLD.template_edition IS NOT NEW.template_edition OR
            OLD.template_revision IS NOT NEW.template_revision OR
            OLD.template_asset_set_hash IS NOT NEW.template_asset_set_hash OR
            OLD.source_project_uuid IS NOT NEW.source_project_uuid OR
            OLD.created_by_operation IS NOT NEW.created_by_operation
        BEGIN
            SELECT RAISE(ABORT, 'PROJECT_IDENTITY_IMMUTABLE');
        END
        """
    )

    _audit_project_identity_rows(db)


def _audit_project_identity_rows(db: sqlite3.Connection) -> None:
    invalid = db.execute(
        f"""
        SELECT id, project_uuid
        FROM projects
        WHERE project_uuid IS NULL OR TRIM(project_uuid) = ''
           OR project_type NOT IN ({", ".join(f"'{value}'" for value in PROJECT_TYPES)})
           OR workflow_status NOT IN ({", ".join(f"'{value}'" for value in WORKFLOW_STATUSES)})
           OR created_by_operation NOT IN ({", ".join(f"'{value}'" for value in PROJECT_CREATION_OPERATIONS)})
           OR (project_type = 'appendix_a' AND (
               template_package_id IS NOT NULL OR
               template_edition IS NOT NULL OR
               template_revision IS NOT NULL OR
               template_asset_set_hash IS NOT NULL OR
               source_project_uuid IS NOT NULL
           ))
           OR (project_type = 'full_report' AND (
               template_package_id IS NULL OR
               template_package_id <> '{FULL_REPORT_TEMPLATE_PACKAGE_ID}' OR
               template_edition IS NULL OR
               template_edition <> '{FULL_REPORT_TEMPLATE_EDITION}' OR
               template_revision IS NULL OR
               template_revision <> '{FULL_REPORT_TEMPLATE_REVISION}' OR
               template_asset_set_hash IS NULL OR
               template_asset_set_hash <> '{FULL_REPORT_TEMPLATE_ASSET_SET_HASH}'
           ))
           OR (created_by_operation = 'upgrade_copy' AND (
               project_type <> 'full_report' OR
               source_project_uuid IS NULL OR
               TRIM(source_project_uuid) = ''
           ))
           OR (created_by_operation <> 'upgrade_copy' AND source_project_uuid IS NOT NULL)
        LIMIT 1
        """
    ).fetchone()
    if invalid is not None:
        raise RuntimeError("PROJECT_IDENTITY_AUDIT_FAILED")

    duplicate = db.execute(
        """
        SELECT project_uuid
        FROM projects
        GROUP BY project_uuid
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    ).fetchone()
    if duplicate is not None:
        raise RuntimeError("PROJECT_UUID_DUPLICATE")

    for row in db.execute("SELECT project_uuid, source_project_uuid FROM projects"):
        for value in (row["project_uuid"], row["source_project_uuid"]):
            if value is None:
                continue
            try:
                uuid.UUID(str(value))
            except ValueError as exc:
                raise RuntimeError("PROJECT_UUID_INVALID") from exc


def _insert_project(
    db: sqlite3.Connection,
    *,
    name: str,
    project_type: str,
    workflow_status: str,
    template_package_id: str | None,
    template_edition: str | None,
    template_revision: str | None,
    template_asset_set_hash: str | None,
    source_project_uuid: str | None,
    created_by_operation: str,
    project_uuid: str | None = None,
) -> sqlite3.Row:
    timestamp = utc_now()
    project_uuid_value = project_uuid or str(uuid.uuid4())
    cursor = db.execute(
        """
        INSERT INTO projects (
            name,
            project_uuid,
            project_type,
            workflow_status,
            template_package_id,
            template_edition,
            template_revision,
            template_asset_set_hash,
            source_project_uuid,
            created_by_operation,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            project_uuid_value,
            project_type,
            workflow_status,
            template_package_id,
            template_edition,
            template_revision,
            template_asset_set_hash,
            source_project_uuid,
            created_by_operation,
            timestamp,
            timestamp,
        ),
    )
    project_id = int(cursor.lastrowid)
    db.executemany(
        """
        INSERT INTO appendix_sections
            (project_id, code, title, table_title, sort_order)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (project_id, code, title, table_title, sort_order)
            for code, title, table_title, sort_order in SECTION_SEED
        ],
    )
    if project_type == "full_report":
        initialize_report_domain(db, project_id=project_id)
        initialize_report_derived_state(db, project_id)
        initialize_report_evidence_categories(db, project_id)
    project = get_project_by_id(project_id, db)
    if project is None:
        raise RuntimeError("PROJECT_CREATE_FAILED")
    return project


def _migrate_management_unit_scores(db: sqlite3.Connection) -> None:
    sections = db.execute(
        "SELECT id FROM appendix_sections WHERE code IN ('A-5', 'A-6', 'A-7', 'A-8')"
    ).fetchall()
    for section in sections:
        rows = [dict(row) for row in list_assessment_rows(int(section["id"]), db)]
        calculated = calculate_flat_management_rows(rows, strict=False)
        for row in calculated:
            unit_score = str(row.get("unit_score") or "").strip()
            if not unit_score:
                continue
            db.execute(
                "UPDATE metric_results SET unit_score = ? WHERE row_id = ?",
                (unit_score, row["id"]),
            )


def _ensure_record_template_slots_schema(db: sqlite3.Connection) -> None:
    columns = {row["name"] for row in db.execute("PRAGMA table_info(record_template_slots)").fetchall()}
    if "template_group" in columns:
        return

    db.execute("ALTER TABLE record_template_slots RENAME TO record_template_slots_legacy")
    db.execute(
        """
        CREATE TABLE record_template_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            section_code TEXT NOT NULL,
            table_type TEXT NOT NULL,
            unit TEXT NOT NULL DEFAULT '',
            template_group TEXT NOT NULL DEFAULT 'verification_record',
            template_type TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            record_text TEXT NOT NULL DEFAULT '',
            default_record_text TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '[]',
            is_customized INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(section_code, unit, template_group, template_type)
        )
        """
    )
    db.execute(
        """
        INSERT INTO record_template_slots (
            id,
            section_code,
            table_type,
            unit,
            template_group,
            template_type,
            title,
            record_text,
            default_record_text,
            tags,
            is_customized,
            created_at,
            updated_at
        )
        SELECT
            id,
            section_code,
            table_type,
            unit,
            'verification_record',
            template_type,
            title,
            record_text,
            default_record_text,
            tags,
            is_customized,
            created_at,
            updated_at
        FROM record_template_slots_legacy
        """
    )
    db.execute("DROP TABLE record_template_slots_legacy")


def _unique_nonempty_values(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def create_docx_import_job(
    original_name: str,
    source_docx_path: str,
    status: str = "uploaded",
    summary: dict[str, Any] | None = None,
    issues: list[dict[str, Any]] | None = None,
) -> sqlite3.Row:
    timestamp = utc_now()
    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO docx_import_jobs (
                status,
                original_name,
                source_docx_path,
                summary_json,
                issues_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                status,
                original_name,
                source_docx_path,
                _dump_json(summary or {}),
                _dump_json(issues or []),
                timestamp,
            ),
        )
        return get_docx_import_job(int(cursor.lastrowid), db)


def get_docx_import_job(job_id: int, db: sqlite3.Connection | None = None) -> sqlite3.Row | None:
    query = """
        SELECT
            id,
            status,
            original_name,
            source_docx_path,
            parsed_json_path,
            created_project_id,
            summary_json,
            issues_json,
            error_message,
            created_at,
            started_at,
            finished_at
        FROM docx_import_jobs
        WHERE id = ?
    """
    if db is not None:
        return db.execute(query, (job_id,)).fetchone()
    with connect() as connection:
        return connection.execute(query, (job_id,)).fetchone()


def update_docx_import_job(job_id: int, fields: dict[str, Any]) -> sqlite3.Row | None:
    allowed = {
        "status",
        "original_name",
        "source_docx_path",
        "parsed_json_path",
        "created_project_id",
        "summary_json",
        "issues_json",
        "error_message",
        "started_at",
        "finished_at",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if "summary" in fields:
        updates["summary_json"] = _dump_json(fields["summary"] or {})
    if "issues" in fields:
        updates["issues_json"] = _dump_json(fields["issues"] or [])
    if not updates:
        return get_docx_import_job(job_id)

    assignments = ", ".join(f"{key} = ?" for key in updates)
    values = list(updates.values()) + [job_id]
    with connect() as db:
        existing = get_docx_import_job(job_id, db)
        if existing is None:
            return None
        db.execute(f"UPDATE docx_import_jobs SET {assignments} WHERE id = ?", values)
        return get_docx_import_job(job_id, db)


def delete_docx_import_job(job_id: int) -> sqlite3.Row | None:
    with connect() as db:
        existing = get_docx_import_job(job_id, db)
        if existing is None:
            return None
        db.execute("DELETE FROM docx_import_jobs WHERE id = ?", (job_id,))
        return existing


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def upsert_system_record_templates(templates: list[dict[str, Any]]) -> None:
    timestamp = utc_now()
    with connect() as db:
        for template in templates:
            db.execute(
                """
                INSERT INTO record_templates (
                    template_key,
                    source_type,
                    section_code,
                    table_type,
                    unit,
                    object_name,
                    title,
                    record_text,
                    tags,
                    source_row,
                    is_enabled,
                    deleted_at,
                    created_at,
                    updated_at
                )
                VALUES (?, 'system', ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, ?, ?)
                ON CONFLICT(template_key) DO UPDATE SET
                    source_type = 'system',
                    section_code = excluded.section_code,
                    table_type = excluded.table_type,
                    unit = excluded.unit,
                    object_name = excluded.object_name,
                    title = excluded.title,
                    record_text = excluded.record_text,
                    tags = excluded.tags,
                    source_row = excluded.source_row,
                    is_enabled = 1,
                    deleted_at = NULL,
                    updated_at = excluded.updated_at
                WHERE
                    record_templates.source_type != 'system'
                    OR record_templates.section_code != excluded.section_code
                    OR record_templates.table_type != excluded.table_type
                    OR record_templates.unit != excluded.unit
                    OR record_templates.object_name != excluded.object_name
                    OR record_templates.title != excluded.title
                    OR record_templates.record_text != excluded.record_text
                    OR record_templates.tags != excluded.tags
                    OR record_templates.source_row IS NOT excluded.source_row
                    OR record_templates.is_enabled != 1
                    OR record_templates.deleted_at IS NOT NULL
                """,
                (
                    template["id"],
                    template["section_code"],
                    template["table_type"],
                    template["unit"],
                    template["object_name"],
                    template["title"],
                    template["record_text"],
                    json.dumps(template.get("tags", []), ensure_ascii=False),
                    template.get("source_row"),
                    timestamp,
                    timestamp,
                ),
            )


def list_record_template_rows(
    section_code: str | None = None,
    keyword: str | None = None,
    source_type: str | None = None,
) -> list[sqlite3.Row]:
    conditions = ["deleted_at IS NULL", "is_enabled = 1"]
    values: list[Any] = []
    if section_code is not None:
        conditions.append("section_code = ?")
        values.append(section_code)
    if source_type is not None:
        conditions.append("source_type = ?")
        values.append(source_type)
    if keyword:
        conditions.append(
            """
            (
                section_code LIKE ? ESCAPE '\\'
                OR unit LIKE ? ESCAPE '\\'
                OR object_name LIKE ? ESCAPE '\\'
                OR title LIKE ? ESCAPE '\\'
                OR record_text LIKE ? ESCAPE '\\'
                OR tags LIKE ? ESCAPE '\\'
            )
            """
        )
        keyword_value = f"%{_escape_like(keyword)}%"
        values.extend([keyword_value] * 6)
    where_sql = " AND ".join(conditions)

    with connect() as db:
        return db.execute(
            f"""
            SELECT
                template_key,
                source_type,
                section_code,
                table_type,
                unit,
                object_name,
                title,
                record_text,
                tags,
                source_row,
                is_enabled,
                created_at,
                updated_at
            FROM record_templates
            WHERE {where_sql}
            ORDER BY
                CAST(SUBSTR(section_code, 3) AS INTEGER),
                unit,
                CASE source_type WHEN 'system' THEN 0 ELSE 1 END,
                COALESCE(source_row, id),
                id
            """,
            values,
        ).fetchall()


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def upsert_record_template_slots(slots: list[dict[str, Any]]) -> None:
    timestamp = utc_now()
    with connect() as db:
        for slot in slots:
            tags = slot.get("tags", [])
            db.execute(
                """
                INSERT INTO record_template_slots (
                    section_code,
                    table_type,
                    unit,
                    template_group,
                    template_type,
                    title,
                    record_text,
                    default_record_text,
                    tags,
                    is_customized,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(section_code, unit, template_group, template_type) DO UPDATE SET
                    table_type = excluded.table_type,
                    title = CASE
                        WHEN record_template_slots.is_customized = 0 THEN excluded.title
                        ELSE record_template_slots.title
                    END,
                    record_text = CASE
                        WHEN record_template_slots.is_customized = 0 THEN excluded.record_text
                        ELSE record_template_slots.record_text
                    END,
                    default_record_text = excluded.default_record_text,
                    tags = CASE
                        WHEN record_template_slots.is_customized = 0 THEN excluded.tags
                        ELSE record_template_slots.tags
                    END,
                    updated_at = CASE
                        WHEN record_template_slots.table_type != excluded.table_type
                            OR record_template_slots.default_record_text != excluded.default_record_text
                            OR (
                                record_template_slots.is_customized = 0
                                AND (
                                    record_template_slots.title != excluded.title
                                    OR record_template_slots.record_text != excluded.record_text
                                    OR record_template_slots.tags != excluded.tags
                                )
                            )
                        THEN excluded.updated_at
                        ELSE record_template_slots.updated_at
                    END
                """,
                (
                    slot["section_code"],
                    slot["table_type"],
                    slot["unit"],
                    slot["template_group"],
                    slot["template_type"],
                    slot["title"],
                    slot["record_text"],
                    slot["default_record_text"],
                    json.dumps(tags, ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )


def list_record_template_slot_rows(
    section_code: str | None = None,
    unit: str | None = None,
    template_group: str | None = None,
    template_type: str | None = None,
) -> list[sqlite3.Row]:
    conditions: list[str] = []
    values: list[Any] = []
    if section_code is not None:
        conditions.append("section_code = ?")
        values.append(section_code)
    if unit is not None:
        conditions.append("unit = ?")
        values.append(unit)
    if template_group is not None:
        conditions.append("template_group = ?")
        values.append(template_group)
    if template_type is not None:
        conditions.append("template_type = ?")
        values.append(template_type)
    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with connect() as db:
        return db.execute(
            f"""
            SELECT
                id,
                section_code,
                table_type,
                unit,
                template_group,
                template_type,
                title,
                record_text,
                default_record_text,
                tags,
                is_customized,
                created_at,
                updated_at
            FROM record_template_slots
            {where_sql}
            ORDER BY
                CAST(SUBSTR(section_code, 3) AS INTEGER),
                unit,
                CASE template_group
                    WHEN 'verification_record' THEN 1
                    WHEN 'score_basis' THEN 2
                    ELSE 3
                END,
                CASE template_type
                    WHEN 'compliant' THEN 1
                    WHEN 'non_compliant' THEN 2
                    WHEN 'not_applicable' THEN 3
                    WHEN 'fully_compliant' THEN 4
                    WHEN 'score_adjusted' THEN 5
                    ELSE 4
                END,
                id
            """,
            values,
        ).fetchall()


def get_record_template_slot_row(slot_id: int, db: sqlite3.Connection | None = None) -> sqlite3.Row | None:
    query = """
        SELECT
            id,
            section_code,
            table_type,
            unit,
            template_group,
            template_type,
            title,
            record_text,
            default_record_text,
            tags,
            is_customized,
            created_at,
            updated_at
        FROM record_template_slots
        WHERE id = ?
    """
    if db is not None:
        return db.execute(query, (slot_id,)).fetchone()
    with connect() as connection:
        return connection.execute(query, (slot_id,)).fetchone()


def update_record_template_slot_row(slot_id: int, fields: dict[str, Any]) -> sqlite3.Row | None:
    allowed = {"title", "record_text", "tags"}
    updates = {key: value for key, value in fields.items() if key in allowed}
    if "tags" in updates:
        updates["tags"] = json.dumps(updates["tags"], ensure_ascii=False)
    updates["is_customized"] = 1
    updates["updated_at"] = utc_now()

    assignments = ", ".join(f"{key} = ?" for key in updates)
    values = list(updates.values()) + [slot_id]
    with connect() as db:
        if get_record_template_slot_row(slot_id, db=db) is None:
            return None
        db.execute(f"UPDATE record_template_slots SET {assignments} WHERE id = ?", values)
        return get_record_template_slot_row(slot_id, db=db)


def reset_record_template_slot_row(slot_id: int, title: str, tags: list[str]) -> sqlite3.Row | None:
    timestamp = utc_now()
    with connect() as db:
        if get_record_template_slot_row(slot_id, db=db) is None:
            return None
        db.execute(
            """
            UPDATE record_template_slots
            SET title = ?,
                record_text = default_record_text,
                tags = ?,
                is_customized = 0,
                updated_at = ?
            WHERE id = ?
            """,
            (title, json.dumps(tags, ensure_ascii=False), timestamp, slot_id),
        )
        return get_record_template_slot_row(slot_id, db=db)

def get_record_template_row(
    template_key: str,
    include_deleted: bool = False,
    db: sqlite3.Connection | None = None,
) -> sqlite3.Row | None:
    conditions = ["template_key = ?"]
    if not include_deleted:
        conditions.append("deleted_at IS NULL")
    query = f"""
        SELECT
            template_key,
            source_type,
            section_code,
            table_type,
            unit,
            object_name,
            title,
            record_text,
            tags,
            source_row,
            is_enabled,
            created_at,
            updated_at,
            deleted_at
        FROM record_templates
        WHERE {" AND ".join(conditions)}
    """
    if db is not None:
        return db.execute(query, (template_key,)).fetchone()
    with connect() as connection:
        return connection.execute(query, (template_key,)).fetchone()


def create_user_record_template(template_key: str, template: dict[str, Any]) -> sqlite3.Row:
    timestamp = utc_now()
    with connect() as db:
        db.execute(
            """
            INSERT INTO record_templates (
                template_key,
                source_type,
                section_code,
                table_type,
                unit,
                object_name,
                title,
                record_text,
                tags,
                source_row,
                is_enabled,
                deleted_at,
                created_at,
                updated_at
            )
            VALUES (?, 'user', ?, ?, ?, ?, ?, ?, ?, NULL, 1, NULL, ?, ?)
            """,
            (
                template_key,
                template["section_code"],
                template["table_type"],
                template["unit"],
                template["object_name"],
                template["title"],
                template["record_text"],
                json.dumps(template.get("tags", []), ensure_ascii=False),
                timestamp,
                timestamp,
            ),
        )
        row = get_record_template_row(template_key, db=db)
        if row is None:
            raise RuntimeError("用户模板创建失败。")
        return row


def update_record_template_row(template_key: str, fields: dict[str, Any]) -> sqlite3.Row | None:
    allowed = {
        "section_code",
        "table_type",
        "unit",
        "object_name",
        "title",
        "record_text",
        "tags",
        "is_enabled",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if "tags" in updates:
        updates["tags"] = json.dumps(updates["tags"], ensure_ascii=False)
    updates["updated_at"] = utc_now()

    assignments = ", ".join(f"{key} = ?" for key in updates)
    values = list(updates.values()) + [template_key]
    with connect() as db:
        if get_record_template_row(template_key, db=db) is None:
            return None
        db.execute(f"UPDATE record_templates SET {assignments} WHERE template_key = ?", values)
        return get_record_template_row(template_key, db=db)


def soft_delete_record_template_row(template_key: str) -> sqlite3.Row | None:
    timestamp = utc_now()
    with connect() as db:
        if get_record_template_row(template_key, db=db) is None:
            return None
        db.execute(
            """
            UPDATE record_templates
            SET is_enabled = 0,
                deleted_at = ?,
                updated_at = ?
            WHERE template_key = ?
            """,
            (timestamp, timestamp, template_key),
        )
        return get_record_template_row(template_key, include_deleted=True, db=db)


def get_project_by_id(project_id: int, db: sqlite3.Connection | None = None) -> sqlite3.Row | None:
    if db is not None:
        return db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    with connect() as connection:
        return connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()


def get_project_by_uuid(project_uuid: str, db: sqlite3.Connection | None = None) -> sqlite3.Row | None:
    query = "SELECT * FROM projects WHERE project_uuid = ?"
    if db is not None:
        return db.execute(query, (project_uuid,)).fetchone()
    with connect() as connection:
        return connection.execute(query, (project_uuid,)).fetchone()


def list_projects() -> list[sqlite3.Row]:
    with connect() as connection:
        return connection.execute(
            """
            SELECT *
            FROM projects
            ORDER BY updated_at DESC, id DESC
            """
        ).fetchall()


def update_project(project_id: int, name: str) -> sqlite3.Row | None:
    with connect() as db:
        existing = get_project_by_id(project_id, db)
        if existing is None:
            return None
        db.execute(
            "UPDATE projects SET name = ?, updated_at = ? WHERE id = ?",
            (name, utc_now(), project_id),
        )
        return get_project_by_id(project_id, db)


def update_project_workflow(project_uuid: str, workflow_status: str) -> sqlite3.Row | None:
    with connect() as db:
        existing = get_project_by_uuid(project_uuid, db)
        if existing is None:
            return None
        db.execute(
            "UPDATE projects SET workflow_status = ?, updated_at = ? WHERE project_uuid = ?",
            (workflow_status, utc_now(), project_uuid),
        )
        return get_project_by_uuid(project_uuid, db)


def delete_project(
    project_id: int,
    db: sqlite3.Connection | None = None,
) -> sqlite3.Row | None:
    if db is not None:
        existing = get_project_by_id(project_id, db)
        if existing is None:
            return None
        db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        return existing
    with connect() as connection:
        return delete_project(project_id, connection)


def list_sections(project_id: int) -> list[sqlite3.Row]:
    with connect() as db:
        return db.execute(
            """
            SELECT id, project_id, code, title, table_title, sort_order
            FROM appendix_sections
            WHERE project_id = ?
            ORDER BY sort_order
            """,
            (project_id,),
        ).fetchall()


def get_section(project_id: int, code: str, db: sqlite3.Connection | None = None) -> sqlite3.Row | None:
    query = """
        SELECT id, project_id, code, title, table_title, sort_order
        FROM appendix_sections
        WHERE project_id = ? AND code = ?
    """
    if db is not None:
        return db.execute(query, (project_id, code)).fetchone()
    with connect() as connection:
        return connection.execute(query, (project_id, code)).fetchone()


def list_assessment_rows(section_id: int, db: sqlite3.Connection | None = None) -> list[sqlite3.Row]:
    query = """
        SELECT
            r.id,
            r.section_id,
            r.assessment_object_uuid,
            r.unit,
            r.object_name,
            r.subsystem,
            r.record_text,
            r.sort_order,
            m.d,
            m.a,
            m.k,
            m.ra,
            m.rk,
            m.object_score,
            m.unit_score,
            m.compliance
        FROM assessment_rows r
        LEFT JOIN metric_results m ON m.row_id = r.id
        WHERE r.section_id = ?
        ORDER BY r.sort_order, r.id
    """
    if db is not None:
        return db.execute(query, (section_id,)).fetchall()
    with connect() as connection:
        return connection.execute(query, (section_id,)).fetchall()


def list_effective_assessment_rows(
    section_id: int,
    db: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    if db is None:
        with connect() as connection:
            return list_effective_assessment_rows(section_id, connection)

    rows = [dict(row) for row in list_assessment_rows(section_id, db)]
    section = db.execute(
        "SELECT code FROM appendix_sections WHERE id = ?",
        (section_id,),
    ).fetchone()
    if section is None:
        return rows
    if section["code"] in TECHNICAL_SECTION_CODES:
        return calculate_flat_technical_rows(rows, strict=False)
    if section["code"] in MANAGEMENT_SECTION_CODES:
        return calculate_flat_management_rows(rows, strict=False)
    return rows


def list_section_subsystems(project_id: int, section_code: str, db: sqlite3.Connection | None = None) -> list[sqlite3.Row]:
    query = """
        SELECT id, project_id, section_code, name, sort_order, created_at, updated_at
        FROM section_subsystems
        WHERE project_id = ? AND section_code = ?
        ORDER BY sort_order, id
    """
    if db is not None:
        return db.execute(query, (project_id, section_code)).fetchall()
    with connect() as connection:
        return connection.execute(query, (project_id, section_code)).fetchall()


def list_evidence_images(project_id: int, section_code: str, db: sqlite3.Connection | None = None) -> list[sqlite3.Row]:
    query = """
        SELECT
            id,
            evidence_uuid,
            project_id,
            section_code,
            file_path,
            original_name,
            caption,
            alt_text,
            sort_order,
            pixel_width,
            pixel_height,
            dpi_x,
            dpi_y,
            display_width_in,
            display_height_in,
            created_at,
            updated_at
        FROM evidence_images
        WHERE project_id = ? AND section_code = ?
        ORDER BY sort_order, id
    """
    if db is not None:
        return db.execute(query, (project_id, section_code)).fetchall()
    with connect() as connection:
        return connection.execute(query, (project_id, section_code)).fetchall()


def list_project_evidence_images(project_id: int, db: sqlite3.Connection | None = None) -> list[sqlite3.Row]:
    query = """
        SELECT
            e.id,
            e.evidence_uuid,
            e.project_id,
            e.section_code,
            e.file_path,
            e.original_name,
            e.caption,
            e.alt_text,
            e.sort_order,
            e.pixel_width,
            e.pixel_height,
            e.dpi_x,
            e.dpi_y,
            e.display_width_in,
            e.display_height_in,
            e.created_at,
            e.updated_at
        FROM evidence_images e
        JOIN appendix_sections s
            ON s.project_id = e.project_id
            AND s.code = e.section_code
        WHERE e.project_id = ?
        ORDER BY s.sort_order, e.sort_order, e.id
    """
    if db is not None:
        return db.execute(query, (project_id,)).fetchall()
    with connect() as connection:
        return connection.execute(query, (project_id,)).fetchall()


def get_evidence_image(image_id: int, db: sqlite3.Connection | None = None) -> sqlite3.Row | None:
    query = """
        SELECT
            id,
            evidence_uuid,
            project_id,
            section_code,
            file_path,
            original_name,
            caption,
            alt_text,
            sort_order,
            pixel_width,
            pixel_height,
            dpi_x,
            dpi_y,
            display_width_in,
            display_height_in,
            created_at,
            updated_at
        FROM evidence_images
        WHERE id = ?
    """
    if db is not None:
        return db.execute(query, (image_id,)).fetchone()
    with connect() as connection:
        return connection.execute(query, (image_id,)).fetchone()


def next_image_sort_order(project_id: int, section_code: str, db: sqlite3.Connection) -> int:
    row = db.execute(
        """
        SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_order
        FROM evidence_images
        WHERE project_id = ? AND section_code = ?
        """,
        (project_id, section_code),
    ).fetchone()
    return int(row["next_order"])


def create_evidence_image(project_id: int, section_code: str, image: dict[str, Any]) -> sqlite3.Row:
    timestamp = utc_now()
    with connect() as db:
        if get_section(project_id, section_code, db) is None:
            raise ValueError("章节不存在")
        sort_order = int(image.get("sort_order") or next_image_sort_order(project_id, section_code, db))
        cursor = db.execute(
            """
            INSERT INTO evidence_images
                (
                    evidence_uuid,
                    project_id,
                    section_code,
                    file_path,
                    original_name,
                    caption,
                    alt_text,
                    sort_order,
                    pixel_width,
                    pixel_height,
                    dpi_x,
                    dpi_y,
                    display_width_in,
                    display_height_in,
                    created_at,
                    updated_at
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_evidence_uuid(),
                project_id,
                section_code,
                image["file_path"],
                image.get("original_name", ""),
                image.get("caption", ""),
                image.get("alt_text", ""),
                sort_order,
                image.get("pixel_width"),
                image.get("pixel_height"),
                image.get("dpi_x"),
                image.get("dpi_y"),
                image.get("display_width_in"),
                image.get("display_height_in"),
                timestamp,
                timestamp,
            ),
        )
        return get_evidence_image(int(cursor.lastrowid), db)


def update_evidence_image(image_id: int, fields: dict[str, Any]) -> sqlite3.Row | None:
    allowed = {
        "section_code",
        "caption",
        "alt_text",
        "sort_order",
        "display_width_in",
        "display_height_in",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return get_evidence_image(image_id)

    updates["updated_at"] = utc_now()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    values = list(updates.values()) + [image_id]
    with connect() as db:
        existing = get_evidence_image(image_id, db)
        if existing is None:
            return None
        db.execute(f"UPDATE evidence_images SET {assignments} WHERE id = ?", values)
        return get_evidence_image(image_id, db)


def replace_evidence_image_file(image_id: int, image: dict[str, Any]) -> sqlite3.Row | None:
    with connect() as db:
        existing = get_evidence_image(image_id, db)
        if existing is None:
            return None
        db.execute(
            """
            UPDATE evidence_images
            SET
                file_path = ?,
                original_name = ?,
                pixel_width = ?,
                pixel_height = ?,
                dpi_x = ?,
                dpi_y = ?,
                display_width_in = ?,
                display_height_in = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                image["file_path"],
                image.get("original_name", ""),
                image.get("pixel_width"),
                image.get("pixel_height"),
                image.get("dpi_x"),
                image.get("dpi_y"),
                image.get("display_width_in"),
                image.get("display_height_in"),
                utc_now(),
                image_id,
            ),
        )
        return get_evidence_image(image_id, db)


def delete_evidence_image(image_id: int) -> sqlite3.Row | None:
    with connect() as db:
        existing = get_evidence_image(image_id, db)
        if existing is None:
            return None
        evidence_uuid = str(existing["evidence_uuid"] or "").strip()
        report_blocks_exist = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='report_blocks'"
        ).fetchone()
        if evidence_uuid and report_blocks_exist:
            reference_count = int(
                db.execute(
                    "SELECT COUNT(*) FROM report_blocks WHERE project_id=? AND instr(payload_json,?)>0",
                    (existing["project_id"], evidence_uuid),
                ).fetchone()[0]
            )
            if reference_count:
                raise ValueError("该图片仍被报告章节引用，不能删除。")
        db.execute("DELETE FROM evidence_images WHERE id = ?", (image_id,))
        return existing


def reorder_evidence_images(project_id: int, section_code: str, image_ids: list[int]) -> list[sqlite3.Row]:
    with connect() as db:
        if get_section(project_id, section_code, db) is None:
            raise ValueError("章节不存在")
        existing_ids = {
            int(row["id"])
            for row in list_evidence_images(project_id, section_code, db)
        }
        if set(image_ids) != existing_ids:
            raise ValueError("排序列表必须包含当前章节的全部图片。")
        for index, image_id in enumerate(image_ids, start=1):
            db.execute(
                "UPDATE evidence_images SET sort_order = ?, updated_at = ? WHERE id = ?",
                (index, utc_now(), image_id),
            )
        return list_evidence_images(project_id, section_code, db)


def list_cross_references(section_id: int, db: sqlite3.Connection | None = None) -> list[sqlite3.Row]:
    query = """
        SELECT
            c.id,
            c.source_row_id,
            c.target_image_id,
            c.token,
            c.display_text
        FROM cross_references c
        JOIN assessment_rows r ON r.id = c.source_row_id
        WHERE r.section_id = ?
        ORDER BY r.sort_order, c.id
    """
    if db is not None:
        return db.execute(query, (section_id,)).fetchall()
    with connect() as connection:
        return connection.execute(query, (section_id,)).fetchall()


def replace_section_rows(
    project_id: int,
    code: str,
    rows: list[dict[str, Any]],
    title: str | None = None,
    table_title: str | None = None,
    subsystems: list[str] | None = None,
) -> sqlite3.Row | None:
    timestamp = utc_now()
    rows = prepare_section_rows(code, rows)
    subsystem_names = _unique_nonempty_values(
        (subsystems or []) + [str(row.get("subsystem", "")) for row in rows]
    )
    with connect() as db:
        section = get_section(project_id, code, db)
        if section is None:
            return None

        if title is not None or table_title is not None:
            db.execute(
                """
                UPDATE appendix_sections
                SET title = COALESCE(?, title),
                    table_title = COALESCE(?, table_title)
                WHERE id = ?
                """,
                (title, table_title, section["id"]),
            )

        existing_rows = db.execute(
            "SELECT id, assessment_object_uuid FROM assessment_rows WHERE section_id = ?",
            (section["id"],),
        ).fetchall()
        existing_by_id = {int(row["id"]): row for row in existing_rows}
        submitted_ids: set[int] = set()
        db.execute(
            "DELETE FROM section_subsystems WHERE project_id = ? AND section_code = ?",
            (project_id, code),
        )

        for index, subsystem_name in enumerate(subsystem_names, start=1):
            db.execute(
                """
                INSERT INTO section_subsystems
                    (project_id, section_code, name, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (project_id, code, subsystem_name, index, timestamp, timestamp),
            )

        for index, row in enumerate(rows, start=1):
            sort_order = int(row.get("sort_order") or index)
            record_text = "" if row.get("record_text") is None else str(row.get("record_text"))
            submitted_id = row.get("id")
            if submitted_id is None:
                cursor = db.execute(
                    """
                    INSERT INTO assessment_rows
                        (section_id, unit, object_name, subsystem, record_text, sort_order, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        section["id"],
                        row.get("unit", ""),
                        row.get("object_name", ""),
                        row.get("subsystem", ""),
                        record_text,
                        sort_order,
                        timestamp,
                        timestamp,
                    ),
                )
                row_id = int(cursor.lastrowid)
            else:
                row_id = int(submitted_id)
                if row_id in submitted_ids:
                    raise ValueError("同一测评记录不能在一次保存中重复提交。")
                existing = existing_by_id.get(row_id)
                if existing is None:
                    raise ValueError("测评记录标识无效或不属于当前章节。")
                db.execute(
                    """
                    UPDATE assessment_rows
                    SET unit=?, object_name=?, subsystem=?, record_text=?, sort_order=?, updated_at=?
                    WHERE id=? AND section_id=?
                    """,
                    (
                        row.get("unit", ""),
                        row.get("object_name", ""),
                        row.get("subsystem", ""),
                        record_text,
                        sort_order,
                        timestamp,
                        row_id,
                        section["id"],
                    ),
                )
                object_uuid = str(existing["assessment_object_uuid"] or "").strip()
                if object_uuid:
                    db.execute(
                        """
                        UPDATE assessment_objects
                        SET name_snapshot=?, source_section_code=?, source_row_id=?,
                            revision=revision+1, updated_at=?
                        WHERE project_id=? AND object_uuid=?
                          AND (
                              name_snapshot<>? OR source_section_code IS NOT ? OR source_row_id IS NOT ?
                          )
                        """,
                        (
                            row.get("object_name", ""),
                            code,
                            row_id,
                            timestamp,
                            project_id,
                            object_uuid,
                            row.get("object_name", ""),
                            code,
                            row_id,
                        ),
                    )
            submitted_ids.add(row_id)
            metric = row.get("metric_result") or {}
            db.execute(
                """
                INSERT INTO metric_results
                    (row_id, d, a, k, ra, rk, object_score, unit_score, compliance)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(row_id) DO UPDATE SET
                    d=excluded.d,
                    a=excluded.a,
                    k=excluded.k,
                    ra=excluded.ra,
                    rk=excluded.rk,
                    object_score=excluded.object_score,
                    unit_score=excluded.unit_score,
                    compliance=excluded.compliance
                """,
                (
                    row_id,
                    metric.get("d"),
                    metric.get("a"),
                    metric.get("k"),
                    metric.get("ra"),
                    metric.get("rk"),
                    metric.get("object_score"),
                    metric.get("unit_score"),
                    metric.get("compliance"),
                ),
            )
            active_reference_tokens = _active_reference_tokens(record_text)
            inserted_reference_tokens: set[str] = set()
            db.execute("DELETE FROM cross_references WHERE source_row_id = ?", (row_id,))
            for reference in row.get("cross_references") or []:
                token = "" if reference.get("token") is None else str(reference.get("token")).strip()
                if token not in active_reference_tokens or token in inserted_reference_tokens:
                    continue
                inserted_reference_tokens.add(token)
                db.execute(
                    """
                    INSERT INTO cross_references
                        (source_row_id, target_image_id, token, display_text)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        row_id,
                        reference.get("target_image_id"),
                        token,
                        reference.get("display_text", ""),
                    ),
                )

        stale_row_ids = set(existing_by_id) - submitted_ids
        for stale_row_id in stale_row_ids:
            db.execute("DELETE FROM assessment_rows WHERE id=? AND section_id=?", (stale_row_id, section["id"]))

        return get_section(project_id, code, db)


def append_section_to_project(
    source_project_id: int,
    target_project_id: int,
    code: str,
) -> sqlite3.Row | None:
    if source_project_id == target_project_id:
        raise ValueError("不能导入到当前项目。")

    timestamp = utc_now()
    with connect() as db:
        source_section = get_section(source_project_id, code, db)
        target_section = get_section(target_project_id, code, db)
        if source_section is None or target_section is None:
            return None

        source_rows = list_effective_assessment_rows(source_section["id"], db)
        target_rows = list_assessment_rows(target_section["id"], db)
        if fixed_object_names_for_section(code) and source_rows and target_rows:
            raise ValueError(f"{code} 使用固定测评对象，目标章节已有数据时不能追加导入。")
        source_object_names = _unique_nonempty_values([row["object_name"] for row in source_rows])
        target_object_names = set(_unique_nonempty_values([row["object_name"] for row in target_rows]))
        duplicate_names = [name for name in source_object_names if name in target_object_names]
        if duplicate_names:
            raise ValueError(f"目标章节已存在同名测评对象：{', '.join(duplicate_names)}")

        source_images = list_evidence_images(source_project_id, code, db)
        next_image_order = next_image_sort_order(target_project_id, code, db)
        image_id_map: dict[int, int] = {}
        image_display_text_map: dict[int, str] = {}
        for offset, source_image in enumerate(source_images):
            target_sort_order = next_image_order + offset
            target_relative_path = _copy_evidence_file_for_project(
                source_image["file_path"],
                target_project_id,
                code,
            )
            cursor = db.execute(
                """
                INSERT INTO evidence_images
                    (
                        evidence_uuid,
                        project_id,
                        section_code,
                        file_path,
                        original_name,
                        caption,
                        alt_text,
                        sort_order,
                        pixel_width,
                        pixel_height,
                        dpi_x,
                        dpi_y,
                        display_width_in,
                        display_height_in,
                        created_at,
                        updated_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_evidence_uuid(),
                    target_project_id,
                    code,
                    target_relative_path,
                    source_image["original_name"],
                    source_image["caption"],
                    source_image["alt_text"],
                    target_sort_order,
                    source_image["pixel_width"],
                    source_image["pixel_height"],
                    source_image["dpi_x"],
                    source_image["dpi_y"],
                    source_image["display_width_in"],
                    source_image["display_height_in"],
                    timestamp,
                    timestamp,
                ),
            )
            new_image_id = int(cursor.lastrowid)
            image_id_map[int(source_image["id"])] = new_image_id
            image_display_text_map[new_image_id] = f"图{code}-{target_sort_order}"

        existing_subsystems = _unique_nonempty_values(
            [row["name"] for row in list_section_subsystems(target_project_id, code, db)]
            + [row["subsystem"] for row in target_rows]
        )
        imported_subsystems = _unique_nonempty_values([row["subsystem"] for row in source_rows])
        for subsystem_name in imported_subsystems:
            if subsystem_name in existing_subsystems:
                continue
            existing_subsystems.append(subsystem_name)
            db.execute(
                """
                INSERT INTO section_subsystems
                    (project_id, section_code, name, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (target_project_id, code, subsystem_name, len(existing_subsystems), timestamp, timestamp),
            )

        references_by_row: dict[int, list[sqlite3.Row]] = {}
        for reference in list_cross_references(source_section["id"], db):
            references_by_row.setdefault(int(reference["source_row_id"]), []).append(reference)

        next_row_order = _next_assessment_row_sort_order(target_section["id"], db)
        rows_for_scores: list[dict[str, Any]] = []
        for offset, source_row in enumerate(source_rows):
            metric = {
                "d": source_row["d"],
                "a": source_row["a"],
                "k": source_row["k"],
                "ra": source_row["ra"],
                "rk": source_row["rk"],
                "object_score": source_row["object_score"],
                "unit_score": source_row["unit_score"],
                "compliance": source_row["compliance"],
            }
            rows_for_scores.append(
                {
                    "unit": source_row["unit"],
                    "object_name": source_row["object_name"],
                    "subsystem": source_row["subsystem"],
                    "record_text": _remap_record_text_image_tokens(source_row["record_text"], image_id_map),
                    "sort_order": next_row_order + offset,
                    "metric_result": metric,
                    "cross_references": [
                        _remap_cross_reference(reference, image_id_map, image_display_text_map)
                        for reference in references_by_row.get(int(source_row["id"]), [])
                    ],
                }
            )

        rows_for_scores = prepare_section_rows(code, rows_for_scores)
        for row in rows_for_scores:
            cursor = db.execute(
                """
                INSERT INTO assessment_rows
                    (section_id, unit, object_name, subsystem, record_text, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_section["id"],
                    row.get("unit", ""),
                    row.get("object_name", ""),
                    row.get("subsystem", ""),
                    row.get("record_text", ""),
                    int(row.get("sort_order") or next_row_order),
                    timestamp,
                    timestamp,
                ),
            )
            row_id = int(cursor.lastrowid)
            metric = row.get("metric_result") or {}
            db.execute(
                """
                INSERT INTO metric_results
                    (row_id, d, a, k, ra, rk, object_score, unit_score, compliance)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id,
                    metric.get("d"),
                    metric.get("a"),
                    metric.get("k"),
                    metric.get("ra"),
                    metric.get("rk"),
                    metric.get("object_score"),
                    metric.get("unit_score"),
                    metric.get("compliance"),
                ),
            )
            active_reference_tokens = _active_reference_tokens(str(row.get("record_text", "")))
            inserted_reference_tokens: set[str] = set()
            for reference in row.get("cross_references") or []:
                token = str(reference.get("token") or "").strip()
                if token not in active_reference_tokens or token in inserted_reference_tokens:
                    continue
                inserted_reference_tokens.add(token)
                db.execute(
                    """
                    INSERT INTO cross_references
                        (source_row_id, target_image_id, token, display_text)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        row_id,
                        reference.get("target_image_id"),
                        token,
                        reference.get("display_text", ""),
                    ),
                )

        _refresh_unit_scores_for_section(
            target_section["id"],
            [row["unit"] for row in source_rows],
            db,
        )
        db.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (timestamp, target_project_id))
        return get_section(target_project_id, code, db)


def _next_assessment_row_sort_order(section_id: int, db: sqlite3.Connection) -> int:
    row = db.execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_order FROM assessment_rows WHERE section_id = ?",
        (section_id,),
    ).fetchone()
    return int(row["next_order"])


def _refresh_unit_scores_for_section(
    section_id: int,
    affected_units: list[str],
    db: sqlite3.Connection,
) -> None:
    normalized_units = {str(unit or "").strip() for unit in affected_units}
    if not normalized_units:
        return

    rows = list_effective_assessment_rows(section_id, db)
    for row in rows:
        unit = str(row["unit"] or "").strip()
        if unit not in normalized_units:
            continue
        db.execute(
            "UPDATE metric_results SET unit_score = ? WHERE row_id = ?",
            (row["unit_score"], row["id"]),
        )


def _copy_evidence_file_for_project(source_file_path: str, target_project_id: int, section_code: str) -> str:
    source_path = settings.storage_path / source_file_path
    if not source_path.exists():
        raise ValueError(f"源证据图片文件不存在：{source_file_path}")

    safe_section = re.sub(r"[^A-Za-z0-9_-]+", "-", section_code)
    extension = source_path.suffix or ".png"
    relative_dir = Path("uploads") / str(target_project_id) / safe_section
    target_dir = settings.storage_path / relative_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    relative_path = relative_dir / f"{uuid.uuid4().hex}{extension}"
    shutil.copy2(source_path, settings.storage_path / relative_path)
    return relative_path.as_posix()


def _remap_record_text_image_tokens(record_text: str, image_id_map: dict[int, int]) -> str:
    output = record_text or ""
    for source_image_id, target_image_id in image_id_map.items():
        output = output.replace(f"[[FIG:{source_image_id}]]", f"[[FIG:{target_image_id}]]")
    return output


def _remap_cross_reference(
    reference: sqlite3.Row,
    image_id_map: dict[int, int],
    image_display_text_map: dict[int, str],
) -> dict[str, Any]:
    source_image_id = reference["target_image_id"]
    target_image_id = image_id_map.get(int(source_image_id)) if source_image_id is not None else None
    token = reference["token"]
    if target_image_id is not None:
        token = f"[[FIG:{target_image_id}]]"
    return {
        "target_image_id": target_image_id,
        "token": token,
        "display_text": image_display_text_map.get(target_image_id or -1, reference["display_text"]),
    }


def _active_reference_tokens(record_text: str) -> set[str]:
    return {match.group(0) for match in FIG_TOKEN_RE.finditer(record_text or "")}


def prepare_section_rows(
    code: str,
    rows: list[dict[str, Any]],
    *,
    strict: bool = True,
) -> list[dict[str, Any]]:
    if code in TECHNICAL_SECTION_CODES:
        return calculate_technical_rows(rows, strict=strict)
    rows = _prepare_fixed_management_rows(code, rows)
    output: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        metric = dict(row.get("metric_result") or {})
        metric["ra"] = None
        metric["rk"] = None
        row["metric_result"] = metric
        output.append(row)
    return calculate_management_rows(output, strict=strict)


def fixed_object_names_for_section(code: str) -> list[str]:
    section_profile = next(
        (section for section in load_template_profile()["sections"] if section["code"] == code),
        {},
    )
    return list(section_profile.get("fixed_object_names", []))


def _prepare_fixed_management_rows(code: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fixed_object_names = fixed_object_names_for_section(code)
    if not fixed_object_names or not rows:
        return rows

    unit_order = _unique_nonempty_values([str(row.get("unit", "")) for row in rows])
    prepared_rows: list[dict[str, Any]] = []
    for unit in unit_order:
        unit_rows = [dict(row) for row in rows if str(row.get("unit", "")).strip() == unit]
        if len(unit_rows) > len(fixed_object_names):
            raise ValueError(
                f"{code} 的测评单元“{unit}”只允许 {len(fixed_object_names)} 个固定测评对象。"
            )

        assigned_indexes: dict[str, int] = {}
        unused_indexes = list(range(len(unit_rows)))
        for object_name in fixed_object_names:
            matching_index = next(
                (
                    index
                    for index in unused_indexes
                    if str(unit_rows[index].get("object_name", "")).strip() == object_name
                ),
                None,
            )
            if matching_index is not None:
                assigned_indexes[object_name] = matching_index
                unused_indexes.remove(matching_index)

        for object_name in fixed_object_names:
            matching_index = assigned_indexes.get(object_name)
            if matching_index is None and unused_indexes:
                matching_index = unused_indexes.pop(0)
            if matching_index is None:
                row = {
                    "unit": unit,
                    "object_name": object_name,
                    "subsystem": "",
                    "record_text": "",
                    "metric_result": {},
                    "cross_references": [],
                }
            else:
                row = unit_rows[matching_index]
                row["object_name"] = object_name
            prepared_rows.append(row)

    return prepared_rows


def replace_validation_issues(project_id: int, issues: list[dict[str, Any]]) -> list[sqlite3.Row]:
    timestamp = utc_now()
    with connect() as db:
        if get_project_by_id(project_id, db) is None:
            raise ValueError("项目不存在")
        db.execute("DELETE FROM validation_issues WHERE project_id = ?", (project_id,))
        for issue in issues:
            db.execute(
                """
                INSERT INTO validation_issues
                    (project_id, severity, code, message, target_type, target_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    issue["severity"],
                    issue["code"],
                    issue["message"],
                    issue.get("target_type"),
                    issue.get("target_id"),
                    timestamp,
                ),
            )
        return list_validation_issues(project_id, db)


def list_validation_issues(project_id: int, db: sqlite3.Connection | None = None) -> list[sqlite3.Row]:
    query = """
        SELECT id, project_id, severity, code, message, target_type, target_id, created_at
        FROM validation_issues
        WHERE project_id = ?
        ORDER BY
            CASE severity
                WHEN 'error' THEN 1
                WHEN 'warning' THEN 2
                ELSE 3
            END,
            id
    """
    if db is not None:
        return db.execute(query, (project_id,)).fetchall()
    with connect() as connection:
        return connection.execute(query, (project_id,)).fetchall()


def create_render_job(project_id: int, mode: str = "final") -> sqlite3.Row:
    timestamp = utc_now()
    with connect() as db:
        if get_project_by_id(project_id, db) is None:
            raise ValueError("项目不存在")
        cursor = db.execute(
            """
            INSERT INTO render_jobs
                (project_id, status, mode, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (project_id, "queued", mode, timestamp),
        )
        return get_render_job(int(cursor.lastrowid), db)


def get_render_job(job_id: int, db: sqlite3.Connection | None = None) -> sqlite3.Row | None:
    query = """
        SELECT
            id,
            project_id,
            status,
            mode,
            created_at,
            started_at,
            finished_at,
            output_docx_path,
            output_pdf_path,
            page_count,
            log_path,
            error_message
        FROM render_jobs
        WHERE id = ?
    """
    if db is not None:
        return db.execute(query, (job_id,)).fetchone()
    with connect() as connection:
        return connection.execute(query, (job_id,)).fetchone()


def update_render_job(job_id: int, fields: dict[str, Any]) -> sqlite3.Row | None:
    allowed = {
        "status",
        "started_at",
        "finished_at",
        "output_docx_path",
        "output_pdf_path",
        "page_count",
        "log_path",
        "error_message",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return get_render_job(job_id)

    assignments = ", ".join(f"{key} = ?" for key in updates)
    values = list(updates.values()) + [job_id]
    with connect() as db:
        existing = get_render_job(job_id, db)
        if existing is None:
            return None
        db.execute(f"UPDATE render_jobs SET {assignments} WHERE id = ?", values)
        return get_render_job(job_id, db)
