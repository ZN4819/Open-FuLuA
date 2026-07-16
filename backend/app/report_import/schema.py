"""R6 完整报告迁移 schema。"""

from __future__ import annotations

import sqlite3

from .contracts import REPORT_IMPORT_TABLES


_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS report_import_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mode TEXT NOT NULL CHECK(mode IN ('migration', 'roundtrip')),
        status TEXT NOT NULL
            CHECK(status IN ('uploaded', 'parsing', 'preview_ready', 'confirming', 'succeeded', 'failed')),
        job_revision INTEGER NOT NULL DEFAULT 1 CHECK(job_revision >= 1),
        original_name TEXT NOT NULL DEFAULT '',
        source_docx_path TEXT NOT NULL DEFAULT '',
        source_sha256 TEXT CHECK(source_sha256 IS NULL OR length(source_sha256) = 64),
        detected_edition TEXT,
        detected_revision TEXT,
        fingerprint_json TEXT NOT NULL DEFAULT '{}'
            CHECK(json_valid(fingerprint_json) AND json_type(fingerprint_json) = 'object'),
        parsed_json_path TEXT,
        summary_json TEXT NOT NULL DEFAULT '{}'
            CHECK(json_valid(summary_json) AND json_type(summary_json) = 'object'),
        appendix_a_source TEXT
            CHECK(appendix_a_source IS NULL OR appendix_a_source IN ('document', 'existing_project')),
        created_project_id INTEGER,
        error_message TEXT,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        FOREIGN KEY(created_project_id) REFERENCES projects(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_report_import_jobs_status
    ON report_import_jobs(status, created_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS report_import_issues (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        code TEXT NOT NULL CHECK(TRIM(code) <> ''),
        severity TEXT NOT NULL CHECK(severity IN ('info', 'warning', 'error')),
        association_id TEXT,
        authority_field_id TEXT,
        field_path TEXT NOT NULL DEFAULT '',
        source_locator TEXT NOT NULL DEFAULT '',
        original_text TEXT NOT NULL DEFAULT '',
        source_value_hash TEXT CHECK(source_value_hash IS NULL OR length(source_value_hash) = 64),
        candidate_value_json TEXT
            CHECK(candidate_value_json IS NULL OR json_valid(candidate_value_json)),
        confidence TEXT NOT NULL
            CHECK(confidence IN ('exact', 'high', 'ambiguous', 'unmapped')),
        status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'resolved', 'ignored')),
        needs_confirmation INTEGER NOT NULL DEFAULT 0 CHECK(needs_confirmation IN (0, 1)),
        blocks_confirmation INTEGER NOT NULL DEFAULT 0 CHECK(blocks_confirmation IN (0, 1)),
        blocks_final_export INTEGER NOT NULL DEFAULT 0 CHECK(blocks_final_export IN (0, 1)),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(job_id) REFERENCES report_import_jobs(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_report_import_issues_job_status
    ON report_import_issues(job_id, status, severity, id)
    """,
    """
    CREATE TABLE IF NOT EXISTS report_import_resolutions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        issue_id INTEGER NOT NULL,
        issue_revision INTEGER NOT NULL CHECK(issue_revision >= 1),
        association_id TEXT,
        authority_field_id TEXT,
        field_path TEXT NOT NULL DEFAULT '',
        action TEXT NOT NULL CHECK(action IN ('adopt_candidate', 'keep_original', 'skip')),
        resolved_value_json TEXT CHECK(resolved_value_json IS NULL OR json_valid(resolved_value_json)),
        resolved_by_user INTEGER NOT NULL DEFAULT 1 CHECK(resolved_by_user IN (0, 1)),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(job_id, issue_id),
        FOREIGN KEY(job_id) REFERENCES report_import_jobs(id) ON DELETE CASCADE,
        FOREIGN KEY(issue_id) REFERENCES report_import_issues(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS report_field_sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        report_import_job_id INTEGER,
        association_id TEXT,
        authority_field_id TEXT,
        field_path TEXT NOT NULL DEFAULT '',
        source_kind TEXT NOT NULL
            CHECK(source_kind IN ('imported', 'imported_manual_draft', 'defaulted', 'copied', 'unmapped')),
        source_locator TEXT NOT NULL DEFAULT '',
        source_value_hash TEXT CHECK(source_value_hash IS NULL OR length(source_value_hash) = 64),
        original_text TEXT NOT NULL DEFAULT '',
        confidence TEXT NOT NULL
            CHECK(confidence IN ('exact', 'high', 'ambiguous', 'unmapped')),
        mapping_status TEXT NOT NULL
            CHECK(mapping_status IN ('adopted', 'pending', 'skipped', 'comparison_only')),
        needs_confirmation INTEGER NOT NULL DEFAULT 0 CHECK(needs_confirmation IN (0, 1)),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(project_id, association_id, field_path, source_locator),
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY(report_import_job_id) REFERENCES report_import_jobs(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_report_field_sources_project
    ON report_field_sources(project_id, mapping_status, id)
    """,
)


def ensure_report_import_schema(db: sqlite3.Connection) -> None:
    for statement in _DDL:
        db.execute(statement)


def audit_report_import_schema(db: sqlite3.Connection) -> None:
    tables = {
        str(row[0])
        for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    missing = sorted(set(REPORT_IMPORT_TABLES) - tables)
    if missing:
        raise RuntimeError(f"REPORT_IMPORT_SCHEMA_TABLE_MISSING:{','.join(missing)}")

    required_columns = {
        "report_import_jobs": {
            "id", "mode", "status", "job_revision", "source_sha256",
            "fingerprint_json", "summary_json", "created_project_id",
        },
        "report_import_issues": {
            "id", "job_id", "revision", "association_id", "authority_field_id",
            "confidence", "needs_confirmation", "blocks_confirmation", "blocks_final_export",
        },
        "report_import_resolutions": {
            "id", "job_id", "issue_id", "issue_revision", "action", "resolved_value_json",
        },
        "report_field_sources": {
            "id", "project_id", "report_import_job_id", "association_id",
            "authority_field_id", "source_kind", "mapping_status", "needs_confirmation",
        },
    }
    for table, required in required_columns.items():
        columns = {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}
        missing_columns = sorted(required - columns)
        if missing_columns:
            raise RuntimeError(
                f"REPORT_IMPORT_SCHEMA_COLUMN_MISSING:{table}:{','.join(missing_columns)}"
            )
