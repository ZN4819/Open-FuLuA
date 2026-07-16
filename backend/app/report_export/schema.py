"""SQLite schema for immutable complete-report export jobs and snapshots."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS report_export_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_uuid TEXT NOT NULL UNIQUE CHECK(TRIM(job_uuid) <> ''),
        project_id INTEGER NOT NULL,
        export_mode TEXT NOT NULL CHECK(export_mode IN ('draft', 'final')),
        export_version TEXT NOT NULL CHECK(TRIM(export_version) <> ''),
        status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
        project_revision INTEGER NOT NULL CHECK(project_revision >= 1),
        template_package_id TEXT NOT NULL,
        template_asset_set_hash TEXT NOT NULL,
        template_docx_hash TEXT NOT NULL,
        r2_context_hash TEXT,
        r3_context_hash TEXT,
        assembly_context_hash TEXT,
        snapshot_uuid TEXT,
        output_relative_path TEXT,
        docx_hash TEXT,
        page_count INTEGER CHECK(page_count IS NULL OR page_count >= 1),
        word_refresh_status TEXT NOT NULL DEFAULT 'not_started'
            CHECK(word_refresh_status IN ('not_started', 'skipped', 'succeeded', 'failed')),
        issues_json TEXT NOT NULL DEFAULT '[]'
            CHECK(json_valid(issues_json) AND json_type(issues_json) = 'array'),
        error_code TEXT,
        error_message TEXT,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        CHECK(docx_hash IS NULL OR LENGTH(docx_hash) = 64),
        CHECK(
            status != 'succeeded' OR (
                snapshot_uuid IS NOT NULL AND TRIM(snapshot_uuid) <> ''
                AND output_relative_path IS NOT NULL AND TRIM(output_relative_path) <> ''
                AND docx_hash IS NOT NULL AND LENGTH(docx_hash) = 64
                AND r2_context_hash IS NOT NULL AND LENGTH(r2_context_hash) = 64
                AND r3_context_hash IS NOT NULL AND LENGTH(r3_context_hash) = 64
                AND assembly_context_hash IS NOT NULL AND LENGTH(assembly_context_hash) = 64
            )
        ),
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS report_export_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_uuid TEXT NOT NULL UNIQUE CHECK(TRIM(snapshot_uuid) <> ''),
        project_id INTEGER NOT NULL,
        job_uuid TEXT NOT NULL UNIQUE,
        project_revision INTEGER NOT NULL CHECK(project_revision >= 1),
        export_mode TEXT NOT NULL CHECK(export_mode IN ('draft', 'final')),
        export_version TEXT NOT NULL CHECK(TRIM(export_version) <> ''),
        context_relative_path TEXT NOT NULL CHECK(TRIM(context_relative_path) <> ''),
        context_hash TEXT NOT NULL CHECK(LENGTH(context_hash) = 64),
        template_package_id TEXT NOT NULL,
        template_asset_set_hash TEXT NOT NULL,
        template_docx_hash TEXT NOT NULL,
        r2_context_hash TEXT NOT NULL CHECK(LENGTH(r2_context_hash) = 64),
        r3_schema_version TEXT NOT NULL,
        r3_rule_set_id TEXT NOT NULL,
        r3_rule_set_hash TEXT NOT NULL CHECK(LENGTH(r3_rule_set_hash) = 64),
        r3_context_hash TEXT NOT NULL CHECK(LENGTH(r3_context_hash) = 64),
        validation_summary_json TEXT NOT NULL DEFAULT '{}'
            CHECK(json_valid(validation_summary_json) AND json_type(validation_summary_json) = 'object'),
        warning_summary_json TEXT NOT NULL DEFAULT '{}'
            CHECK(json_valid(warning_summary_json) AND json_type(warning_summary_json) = 'object'),
        created_at TEXT NOT NULL,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY(job_uuid) REFERENCES report_export_jobs(job_uuid) ON DELETE CASCADE
    )
    """,
)

_INDEX_DDL: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_report_export_jobs_project_created ON report_export_jobs(project_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_report_export_jobs_project_status ON report_export_jobs(project_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_report_export_snapshots_project_created ON report_export_snapshots(project_id, created_at DESC)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_report_export_jobs_active_revision ON report_export_jobs(project_id, project_revision, export_mode) WHERE status IN ('queued', 'running')",
)

_TRIGGER_DDL: tuple[str, ...] = (
    """
    CREATE TRIGGER IF NOT EXISTS report_export_snapshots_immutable
    BEFORE UPDATE ON report_export_snapshots
    BEGIN
        SELECT RAISE(ABORT, 'REPORT_EXPORT_SNAPSHOT_IMMUTABLE');
    END
    """,
)

_REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "report_export_jobs": frozenset(
        {
            "job_uuid", "project_id", "export_mode", "export_version", "status",
            "project_revision", "template_docx_hash", "issues_json", "created_at",
        }
    ),
    "report_export_snapshots": frozenset(
        {
            "snapshot_uuid", "project_id", "job_uuid", "project_revision",
            "context_relative_path", "context_hash", "r2_context_hash", "r3_context_hash",
        }
    ),
}

REPORT_EXPORT_TABLES = frozenset(_REQUIRED_COLUMNS)

_REQUIRED_OBJECTS = frozenset(
    {
        "idx_report_export_jobs_project_created",
        "idx_report_export_jobs_project_status",
        "idx_report_export_snapshots_project_created",
        "idx_report_export_jobs_active_revision",
        "report_export_snapshots_immutable",
    }
)


def invalidate_pre_r4_projection_contexts(db: sqlite3.Connection) -> int:
    """Require one deterministic R3 regeneration after the schema-7 upgrade.

    Schema 6 contexts predate the authoritative Chapter 4 projection required
    by R4.  Existing block revisions and human confirmations stay intact; only
    the current run/context pointer and its consistency revision are invalidated.
    """

    run_uuids = [
        str(row["current_run_uuid"])
        for row in db.execute(
            "SELECT current_run_uuid FROM report_generation_state WHERE current_run_uuid IS NOT NULL"
        ).fetchall()
    ]
    if not run_uuids:
        return 0
    placeholders = ",".join("?" for _ in run_uuids)
    db.execute(
        f"UPDATE report_generation_runs SET status = 'needs_input' "
        f"WHERE status = 'current' AND run_uuid IN ({placeholders})",
        tuple(run_uuids),
    )
    timestamp = datetime.now(timezone.utc).isoformat()
    cursor = db.execute(
        """
        UPDATE report_generation_state
        SET current_run_uuid = NULL,
            current_input_hash = NULL,
            current_context_json = NULL,
            current_context_hash = NULL,
            project_revision = project_revision + 1,
            updated_at = ?
        WHERE current_run_uuid IS NOT NULL
        """,
        (timestamp,),
    )
    return int(cursor.rowcount)


def ensure_report_export_schema(db: sqlite3.Connection) -> None:
    for statement in _DDL:
        db.execute(statement)
    for statement in _INDEX_DDL:
        db.execute(statement)
    for statement in _TRIGGER_DDL:
        db.execute(statement)


def audit_report_export_schema(db: sqlite3.Connection) -> None:
    for table_name, required in _REQUIRED_COLUMNS.items():
        columns = {
            str(row["name"])
            for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if not required <= columns:
            raise RuntimeError(f"REPORT_EXPORT_SCHEMA_AUDIT_FAILED:{table_name}")
    objects = {
        str(row["name"])
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('index', 'trigger')"
        ).fetchall()
    }
    missing = _REQUIRED_OBJECTS - objects
    if missing:
        raise RuntimeError(
            f"REPORT_EXPORT_SCHEMA_AUDIT_FAILED:object:{','.join(sorted(missing))}"
        )
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise RuntimeError("REPORT_EXPORT_FOREIGN_KEY_AUDIT_FAILED")
