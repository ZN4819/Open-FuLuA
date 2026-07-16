"""R7 受控 Word 回收数据库契约。"""

from __future__ import annotations

import sqlite3


ROUNDTRIP_STATUSES = (
    "uploaded",
    "validating",
    "invalid",
    "diff_ready",
    "conflicts_pending",
    "ready_to_commit",
    "committing",
    "succeeded",
    "failed",
    "stale",
)


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in db.execute(f"PRAGMA table_info({table})")}


def _add_column(db: sqlite3.Connection, table: str, name: str, declaration: str) -> None:
    if name not in _columns(db, table):
        db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


def ensure_report_roundtrip_schema(db: sqlite3.Connection) -> None:
    # R7 keeps the R4/R6 tables additive so existing desktop data can migrate
    # in place without weakening their historical CHECK constraints.
    for name, declaration in (
        ("roundtrip_capable", "INTEGER NOT NULL DEFAULT 0 CHECK(roundtrip_capable IN (0, 1))"),
        ("document_instance_id", "TEXT"),
        ("manifest_hash", "TEXT"),
        ("structure_contract_hash", "TEXT"),
        ("signing_key_id", "TEXT"),
    ):
        _add_column(db, "report_export_jobs", name, declaration)
    for name, declaration in (
        ("roundtrip_capable", "INTEGER NOT NULL DEFAULT 0 CHECK(roundtrip_capable IN (0, 1))"),
        ("baseline_relative_path", "TEXT"),
        ("baseline_hash", "TEXT"),
        ("structure_contract_hash", "TEXT"),
        ("document_instance_id", "TEXT"),
    ):
        _add_column(db, "report_export_snapshots", name, declaration)
    for name, declaration in (
        ("project_id", "INTEGER"),
        ("roundtrip_status", "TEXT"),
        ("document_instance_id", "TEXT"),
        ("source_snapshot_uuid", "TEXT"),
        ("base_project_revision", "INTEGER"),
        ("observed_project_revision", "INTEGER"),
        ("manifest_hash", "TEXT"),
        ("structure_contract_hash", "TEXT"),
        ("baseline_hash", "TEXT"),
        ("diff_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("diff_hash", "TEXT"),
        ("resolution_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("resolution_hash", "TEXT"),
        ("archived_relative_path", "TEXT"),
        ("archived_hash", "TEXT"),
        ("error_code", "TEXT"),
    ):
        _add_column(db, "report_import_jobs", name, declaration)

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS report_roundtrip_manifests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_instance_id TEXT NOT NULL UNIQUE CHECK(TRIM(document_instance_id) <> ''),
            project_id INTEGER NOT NULL,
            snapshot_uuid TEXT NOT NULL UNIQUE,
            export_job_uuid TEXT NOT NULL UNIQUE,
            manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json) AND json_type(manifest_json) = 'object'),
            baseline_json TEXT NOT NULL CHECK(json_valid(baseline_json) AND json_type(baseline_json) = 'object'),
            manifest_hash TEXT NOT NULL CHECK(LENGTH(manifest_hash) = 64),
            baseline_hash TEXT NOT NULL CHECK(LENGTH(baseline_hash) = 64),
            structure_contract_hash TEXT NOT NULL CHECK(LENGTH(structure_contract_hash) = 64),
            signing_key_id TEXT NOT NULL CHECK(TRIM(signing_key_id) <> ''),
            created_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE RESTRICT,
            FOREIGN KEY(snapshot_uuid) REFERENCES report_export_snapshots(snapshot_uuid) ON DELETE RESTRICT,
            FOREIGN KEY(export_job_uuid) REFERENCES report_export_jobs(job_uuid) ON DELETE RESTRICT
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS report_sync_conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            conflict_id TEXT NOT NULL,
            field_id TEXT NOT NULL,
            field_path TEXT NOT NULL,
            row_uuid TEXT,
            base_value_json TEXT,
            database_value_json TEXT,
            word_value_json TEXT,
            conflict_kind TEXT NOT NULL,
            resolution_action TEXT CHECK(resolution_action IS NULL OR resolution_action IN ('keep_database', 'apply_word')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(job_id, conflict_id),
            FOREIGN KEY(job_id) REFERENCES report_import_jobs(id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS report_import_audits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audit_uuid TEXT NOT NULL UNIQUE,
            job_id INTEGER NOT NULL UNIQUE,
            project_id INTEGER NOT NULL,
            document_instance_id TEXT NOT NULL,
            source_sha256 TEXT NOT NULL CHECK(LENGTH(source_sha256) = 64),
            manifest_hash TEXT NOT NULL CHECK(LENGTH(manifest_hash) = 64),
            diff_hash TEXT NOT NULL CHECK(LENGTH(diff_hash) = 64),
            resolution_hash TEXT NOT NULL CHECK(LENGTH(resolution_hash) = 64),
            before_hash TEXT NOT NULL CHECK(LENGTH(before_hash) = 64),
            after_hash TEXT NOT NULL CHECK(LENGTH(after_hash) = 64),
            changed_fields_json TEXT NOT NULL CHECK(json_valid(changed_fields_json) AND json_type(changed_fields_json) = 'array'),
            base_project_revision INTEGER NOT NULL,
            committed_project_revision INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(job_id) REFERENCES report_import_jobs(id) ON DELETE RESTRICT,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE RESTRICT
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS report_roundtrip_deletion_tombstones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tombstone_uuid TEXT NOT NULL UNIQUE,
            source_project_id INTEGER NOT NULL UNIQUE,
            project_uuid TEXT NOT NULL,
            manifest_hashes_json TEXT NOT NULL
                CHECK(json_valid(manifest_hashes_json) AND json_type(manifest_hashes_json) = 'array'),
            audit_hashes_json TEXT NOT NULL
                CHECK(json_valid(audit_hashes_json) AND json_type(audit_hashes_json) = 'array'),
            deleted_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS report_roundtrip_cleanup_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tombstone_uuid TEXT NOT NULL UNIQUE,
            source_project_id INTEGER NOT NULL UNIQUE,
            project_uuid TEXT NOT NULL,
            roundtrip_job_ids_json TEXT NOT NULL
                CHECK(json_valid(roundtrip_job_ids_json) AND json_type(roundtrip_job_ids_json) = 'array'),
            attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
            last_error_code TEXT,
            created_at TEXT NOT NULL,
            last_attempt_at TEXT
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_roundtrip_jobs_project_status "
        "ON report_import_jobs(project_id, roundtrip_status, created_at DESC)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_sync_conflicts_job "
        "ON report_sync_conflicts(job_id, resolution_action, id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_roundtrip_manifests_project "
        "ON report_roundtrip_manifests(project_id, created_at DESC)"
    )
    db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS report_roundtrip_manifests_immutable
        BEFORE UPDATE ON report_roundtrip_manifests
        BEGIN
            SELECT RAISE(ABORT, 'REPORT_ROUNDTRIP_MANIFEST_IMMUTABLE');
        END
        """
    )
    db.execute("DROP TRIGGER IF EXISTS report_roundtrip_manifests_no_delete")
    db.execute(
        """
        CREATE TRIGGER report_roundtrip_manifests_no_delete
        BEFORE DELETE ON report_roundtrip_manifests
        WHEN NOT EXISTS (
            SELECT 1 FROM report_roundtrip_deletion_tombstones t
            WHERE t.source_project_id = OLD.project_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'REPORT_ROUNDTRIP_MANIFEST_DELETE_FORBIDDEN');
        END
        """
    )
    db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS report_import_audits_immutable
        BEFORE UPDATE ON report_import_audits
        BEGIN
            SELECT RAISE(ABORT, 'REPORT_IMPORT_AUDIT_IMMUTABLE');
        END
        """
    )
    db.execute("DROP TRIGGER IF EXISTS report_import_audits_no_delete")
    db.execute(
        """
        CREATE TRIGGER report_import_audits_no_delete
        BEFORE DELETE ON report_import_audits
        WHEN NOT EXISTS (
            SELECT 1 FROM report_roundtrip_deletion_tombstones t
            WHERE t.source_project_id = OLD.project_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'REPORT_IMPORT_AUDIT_DELETE_FORBIDDEN');
        END
        """
    )
    db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS report_roundtrip_tombstones_immutable
        BEFORE UPDATE ON report_roundtrip_deletion_tombstones
        BEGIN
            SELECT RAISE(ABORT, 'REPORT_ROUNDTRIP_TOMBSTONE_IMMUTABLE');
        END
        """
    )
    db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS report_roundtrip_tombstones_no_delete
        BEFORE DELETE ON report_roundtrip_deletion_tombstones
        BEGIN
            SELECT RAISE(ABORT, 'REPORT_ROUNDTRIP_TOMBSTONE_DELETE_FORBIDDEN');
        END
        """
    )
    allowed_statuses = ",".join(f"'{item}'" for item in ROUNDTRIP_STATUSES)
    db.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS report_import_jobs_roundtrip_guard_insert
        BEFORE INSERT ON report_import_jobs
        WHEN NEW.mode = 'roundtrip' AND (
            NEW.roundtrip_status NOT IN ({allowed_statuses})
            OR NEW.project_id IS NULL
            OR NOT json_valid(NEW.diff_json)
            OR NOT json_valid(NEW.resolution_json)
        )
        BEGIN
            SELECT RAISE(ABORT, 'REPORT_ROUNDTRIP_JOB_INVALID');
        END
        """
    )
    db.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS report_import_jobs_roundtrip_guard_update
        BEFORE UPDATE OF roundtrip_status, diff_json, resolution_json ON report_import_jobs
        WHEN NEW.mode = 'roundtrip' AND (
            NEW.roundtrip_status NOT IN ({allowed_statuses})
            OR NOT json_valid(NEW.diff_json)
            OR NOT json_valid(NEW.resolution_json)
        )
        BEGIN
            SELECT RAISE(ABORT, 'REPORT_ROUNDTRIP_JOB_INVALID');
        END
        """
    )


def audit_report_roundtrip_schema(db: sqlite3.Connection) -> None:
    required = {
        "report_export_jobs": {"roundtrip_capable", "document_instance_id", "manifest_hash"},
        "report_export_snapshots": {"roundtrip_capable", "baseline_relative_path", "baseline_hash"},
        "report_import_jobs": {
            "project_id", "roundtrip_status", "source_snapshot_uuid", "base_project_revision",
            "diff_json", "diff_hash", "resolution_json", "resolution_hash",
        },
        "report_roundtrip_manifests": {
            "document_instance_id", "snapshot_uuid", "manifest_json", "baseline_json", "manifest_hash",
            "baseline_hash", "structure_contract_hash", "signing_key_id",
        },
        "report_sync_conflicts": {
            "job_id", "conflict_id", "field_id", "field_path", "conflict_kind", "resolution_action",
        },
        "report_import_audits": {
            "audit_uuid", "job_id", "before_hash", "after_hash", "changed_fields_json",
        },
        "report_roundtrip_deletion_tombstones": {
            "tombstone_uuid", "source_project_id", "project_uuid",
            "manifest_hashes_json", "audit_hashes_json", "deleted_at",
        },
        "report_roundtrip_cleanup_queue": {
            "tombstone_uuid", "source_project_id", "project_uuid",
            "roundtrip_job_ids_json", "attempts", "last_error_code",
            "created_at", "last_attempt_at",
        },
    }
    for table, expected in required.items():
        actual = _columns(db, table)
        if not expected <= actual:
            raise RuntimeError(
                f"REPORT_ROUNDTRIP_SCHEMA_AUDIT_FAILED:{table}:{','.join(sorted(expected - actual))}"
            )
    objects = {
        str(row["name"])
        for row in db.execute("SELECT name FROM sqlite_master WHERE type IN ('index', 'trigger')")
    }
    expected_objects = {
        "idx_roundtrip_jobs_project_status", "idx_sync_conflicts_job",
        "idx_roundtrip_manifests_project", "report_roundtrip_manifests_immutable",
        "report_import_audits_immutable",
        "report_roundtrip_manifests_no_delete", "report_import_audits_no_delete",
        "report_import_jobs_roundtrip_guard_insert", "report_import_jobs_roundtrip_guard_update",
        "report_roundtrip_tombstones_immutable", "report_roundtrip_tombstones_no_delete",
    }
    if not expected_objects <= objects:
        raise RuntimeError("REPORT_ROUNDTRIP_SCHEMA_OBJECT_MISSING")
    invalid_rows = db.execute(
        """
        SELECT COUNT(*) FROM assessment_rows
        WHERE row_uuid IS NULL OR TRIM(row_uuid) = ''
           OR LENGTH(row_uuid) != 36
        """
    ).fetchone()[0]
    if int(invalid_rows):
        raise RuntimeError("REPORT_ROUNDTRIP_ROW_UUID_AUDIT_FAILED")
