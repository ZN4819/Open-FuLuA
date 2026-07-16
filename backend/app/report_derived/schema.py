from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone


DERIVED_UUID_NAMESPACE = uuid.UUID("22b8add8-d141-4e63-97d2-67f1ad8e4c32")


_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS report_generation_state (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        state_uuid TEXT NOT NULL UNIQUE,
        project_id INTEGER NOT NULL UNIQUE,
        project_revision INTEGER NOT NULL DEFAULT 1 CHECK(project_revision >= 1),
        feature_enabled INTEGER NOT NULL DEFAULT 1 CHECK(feature_enabled IN (0, 1)),
        current_run_uuid TEXT,
        current_input_hash TEXT,
        current_context_json TEXT CHECK(
            current_context_json IS NULL OR (
                json_valid(current_context_json) AND json_type(current_context_json) = 'object'
            )
        ),
        current_context_hash TEXT,
        source_groups_json TEXT NOT NULL DEFAULT '{}'
            CHECK(json_valid(source_groups_json) AND json_type(source_groups_json) = 'object'),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS report_generation_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_uuid TEXT NOT NULL UNIQUE,
        project_id INTEGER NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('current', 'needs_input', 'failed')),
        rule_set_id TEXT NOT NULL,
        rule_set_hash TEXT NOT NULL,
        input_hash TEXT NOT NULL,
        projection_json TEXT CHECK(
            projection_json IS NULL OR (
                json_valid(projection_json) AND json_type(projection_json) = 'object'
            )
        ),
        issues_json TEXT NOT NULL DEFAULT '[]'
            CHECK(json_valid(issues_json) AND json_type(issues_json) = 'array'),
        state_revision INTEGER NOT NULL CHECK(state_revision >= 1),
        started_at TEXT NOT NULL,
        finished_at TEXT NOT NULL,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS report_findings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        finding_uuid TEXT NOT NULL UNIQUE,
        project_id INTEGER NOT NULL,
        indicator_code TEXT NOT NULL,
        layer_code TEXT NOT NULL,
        final_indicator_result TEXT NOT NULL
            CHECK(final_indicator_result IN ('部分符合', '不符合')),
        source_hash TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(finding_uuid, project_id),
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS report_risks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        risk_uuid TEXT NOT NULL UNIQUE,
        project_id INTEGER NOT NULL,
        finding_uuid TEXT NOT NULL UNIQUE,
        risk_level TEXT CHECK(risk_level IS NULL OR risk_level IN ('high', 'medium', 'low')),
        analysis_baseline_json TEXT NOT NULL DEFAULT '{}'
            CHECK(json_valid(analysis_baseline_json) AND json_type(analysis_baseline_json) = 'object'),
        analysis_override_json TEXT CHECK(
            analysis_override_json IS NULL OR (
                json_valid(analysis_override_json) AND json_type(analysis_override_json) = 'object'
            )
        ),
        override_reason TEXT NOT NULL DEFAULT '',
        confirmation_status TEXT NOT NULL DEFAULT 'needs_input'
            CHECK(confirmation_status IN ('needs_input', 'unconfirmed', 'confirmed')),
        source_hash TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(risk_uuid, project_id),
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY(finding_uuid, project_id)
            REFERENCES report_findings(finding_uuid, project_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS report_risk_threat_relations (
        risk_uuid TEXT NOT NULL,
        project_id INTEGER NOT NULL,
        threat_catalog_id TEXT NOT NULL,
        sort_order INTEGER NOT NULL DEFAULT 0 CHECK(sort_order >= 0),
        PRIMARY KEY(risk_uuid, threat_catalog_id),
        FOREIGN KEY(risk_uuid, project_id)
            REFERENCES report_risks(risk_uuid, project_id) ON DELETE CASCADE,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS report_block_revisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        revision_uuid TEXT NOT NULL UNIQUE,
        block_uuid TEXT NOT NULL,
        project_id INTEGER NOT NULL,
        revision INTEGER NOT NULL CHECK(revision >= 1),
        baseline_json TEXT NOT NULL
            CHECK(json_valid(baseline_json) AND json_type(baseline_json) = 'object'),
        baseline_hash TEXT NOT NULL,
        source_snapshot_json TEXT NOT NULL
            CHECK(json_valid(source_snapshot_json) AND json_type(source_snapshot_json) = 'object'),
        source_hash TEXT NOT NULL,
        override_json TEXT CHECK(
            override_json IS NULL OR (
                json_valid(override_json) AND json_type(override_json) = 'object'
            )
        ),
        override_reason TEXT NOT NULL DEFAULT '',
        generation_status TEXT NOT NULL
            CHECK(generation_status IN ('not_generated', 'current', 'stale', 'failed')),
        confirmation_status TEXT NOT NULL
            CHECK(confirmation_status IN ('unconfirmed', 'confirmed', 'review_required')),
        rule_set_id TEXT NOT NULL,
        rule_id TEXT NOT NULL,
        generated_at TEXT NOT NULL,
        confirmed_at TEXT,
        confirmed_by TEXT,
        project_revision INTEGER NOT NULL CHECK(project_revision >= 1),
        is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0, 1)),
        UNIQUE(block_uuid, revision),
        FOREIGN KEY(block_uuid, project_id)
            REFERENCES report_blocks(block_uuid, project_id) ON DELETE CASCADE,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS report_consistency_checks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        check_uuid TEXT NOT NULL UNIQUE,
        project_id INTEGER NOT NULL,
        run_uuid TEXT,
        status TEXT NOT NULL CHECK(status IN ('valid', 'invalid', 'needs_input')),
        issues_json TEXT NOT NULL DEFAULT '[]'
            CHECK(json_valid(issues_json) AND json_type(issues_json) = 'array'),
        context_hash TEXT,
        state_revision INTEGER NOT NULL CHECK(state_revision >= 1),
        checked_at TEXT NOT NULL,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY(run_uuid) REFERENCES report_generation_runs(run_uuid) ON DELETE SET NULL
    )
    """,
)


_INDEX_DDL: tuple[str, ...] = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_report_blocks_uuid_project ON report_blocks(block_uuid, project_id)",
    "CREATE INDEX IF NOT EXISTS idx_generation_runs_project ON report_generation_runs(project_id, id DESC)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_findings_active_indicator ON report_findings(project_id, indicator_code) WHERE active = 1",
    "CREATE INDEX IF NOT EXISTS idx_findings_layer ON report_findings(project_id, layer_code, indicator_code)",
    "CREATE INDEX IF NOT EXISTS idx_findings_active ON report_findings(project_id, active)",
    "CREATE INDEX IF NOT EXISTS idx_risks_level ON report_risks(project_id, risk_level)",
    "CREATE INDEX IF NOT EXISTS idx_risks_confirmation ON report_risks(project_id, confirmation_status)",
    "CREATE INDEX IF NOT EXISTS idx_risk_threat_project ON report_risk_threat_relations(project_id, risk_uuid, sort_order)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_block_revision_current ON report_block_revisions(block_uuid) WHERE is_current = 1",
    "CREATE INDEX IF NOT EXISTS idx_block_revision_generation ON report_block_revisions(project_id, generation_status)",
    "CREATE INDEX IF NOT EXISTS idx_block_revision_confirmation ON report_block_revisions(project_id, confirmation_status)",
    "CREATE INDEX IF NOT EXISTS idx_block_revision_source_hash ON report_block_revisions(source_hash)",
    "CREATE INDEX IF NOT EXISTS idx_consistency_project ON report_consistency_checks(project_id, id DESC)",
)


_REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "report_generation_state": frozenset({"state_uuid", "project_id", "project_revision", "current_context_json"}),
    "report_generation_runs": frozenset({"run_uuid", "project_id", "status", "rule_set_id", "input_hash"}),
    "report_findings": frozenset({"finding_uuid", "project_id", "indicator_code", "active"}),
    "report_risks": frozenset({"risk_uuid", "project_id", "finding_uuid", "confirmation_status", "revision"}),
    "report_risk_threat_relations": frozenset({"risk_uuid", "project_id", "threat_catalog_id"}),
    "report_block_revisions": frozenset({"revision_uuid", "block_uuid", "project_id", "generation_status", "confirmation_status"}),
    "report_consistency_checks": frozenset({"check_uuid", "project_id", "status", "issues_json"}),
}

REPORT_DERIVED_TABLES = frozenset(_REQUIRED_COLUMNS)

_REQUIRED_INDEXES = frozenset(
    {
        "idx_report_blocks_uuid_project",
        "idx_generation_runs_project",
        "idx_findings_active_indicator",
        "idx_risks_confirmation",
        "idx_block_revision_current",
        "idx_consistency_project",
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_report_derived_schema(db: sqlite3.Connection) -> None:
    for statement in _DDL:
        db.execute(statement)
    for statement in _INDEX_DDL:
        db.execute(statement)


def audit_report_derived_schema(db: sqlite3.Connection) -> None:
    for table, required in _REQUIRED_COLUMNS.items():
        existing = {
            str(row["name"])
            for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if not required.issubset(existing):
            raise RuntimeError(f"REPORT_DERIVED_SCHEMA_AUDIT_FAILED:{table}")
    indexes = {
        str(row["name"])
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name IS NOT NULL"
        ).fetchall()
    }
    missing_indexes = _REQUIRED_INDEXES - indexes
    if missing_indexes:
        raise RuntimeError(
            f"REPORT_DERIVED_SCHEMA_AUDIT_FAILED:index:{','.join(sorted(missing_indexes))}"
        )
    foreign_key_error = db.execute("PRAGMA foreign_key_check").fetchone()
    if foreign_key_error is not None:
        raise RuntimeError("REPORT_DERIVED_FOREIGN_KEY_AUDIT_FAILED")


def initialize_report_derived_state(db: sqlite3.Connection, project_id: int) -> None:
    project = db.execute(
        "SELECT id, project_uuid, project_type FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()
    if project is None or project["project_type"] != "full_report":
        return
    timestamp = _utc_now()
    state_uuid = str(uuid.uuid5(DERIVED_UUID_NAMESPACE, f"{project['project_uuid']}:state"))
    db.execute(
        """
        INSERT INTO report_generation_state (
            state_uuid, project_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(project_id) DO NOTHING
        """,
        (state_uuid, project_id, timestamp, timestamp),
    )


def initialize_existing_report_derived_states(db: sqlite3.Connection) -> int:
    projects = db.execute(
        "SELECT id FROM projects WHERE project_type = 'full_report' ORDER BY id"
    ).fetchall()
    for project in projects:
        initialize_report_derived_state(db, int(project["id"]))
    return len(projects)
