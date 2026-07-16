"""SQLite schema and idempotent initialization for R5 Appendix B evidence."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from .contracts import APPENDIX_B_CATEGORIES, APPENDIX_B_CATEGORY_CODES


_CATEGORY_VALUES = ", ".join(f"'{value}'" for value in APPENDIX_B_CATEGORY_CODES)

_DDL: tuple[str, ...] = (
    f"""
    CREATE TABLE IF NOT EXISTS report_evidence_categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_uuid TEXT NOT NULL UNIQUE CHECK(TRIM(category_uuid) <> ''),
        project_id INTEGER NOT NULL,
        category_code TEXT NOT NULL CHECK(category_code IN ({_CATEGORY_VALUES})),
        is_not_applicable INTEGER NOT NULL DEFAULT 0 CHECK(is_not_applicable IN (0, 1)),
        not_applicable_reason TEXT NOT NULL DEFAULT '',
        warning_acknowledged_at TEXT,
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(project_id, category_code),
        UNIQUE(category_uuid, project_id),
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS report_evidence_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_uuid TEXT NOT NULL UNIQUE CHECK(TRIM(item_uuid) <> ''),
        project_id INTEGER NOT NULL,
        category_code TEXT NOT NULL,
        parent_item_uuid TEXT,
        item_kind TEXT NOT NULL CHECK(item_kind IN ('record', 'image')),
        subtype TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        starts_on TEXT,
        ends_on TEXT,
        organization_uuid TEXT,
        location TEXT NOT NULL DEFAULT '',
        sort_order INTEGER NOT NULL DEFAULT 0 CHECK(sort_order >= 0),
        metadata_json TEXT NOT NULL DEFAULT '{}'
            CHECK(json_valid(metadata_json) AND json_type(metadata_json) = 'object'),
        file_path TEXT,
        original_name TEXT,
        mime_type TEXT,
        caption TEXT NOT NULL DEFAULT '',
        alt_text TEXT NOT NULL DEFAULT '',
        pixel_width INTEGER,
        pixel_height INTEGER,
        dpi_x REAL,
        dpi_y REAL,
        display_width_in REAL,
        display_height_in REAL,
        sha256 TEXT,
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK(ends_on IS NULL OR starts_on IS NULL OR ends_on >= starts_on),
        CHECK(
            (item_kind = 'record' AND file_path IS NULL AND sha256 IS NULL)
            OR
            (item_kind = 'image' AND parent_item_uuid IS NOT NULL
                AND file_path IS NOT NULL AND TRIM(file_path) <> ''
                AND original_name IS NOT NULL AND TRIM(original_name) <> ''
                AND mime_type IN ('image/png', 'image/jpeg')
                AND sha256 IS NOT NULL AND LENGTH(sha256) = 64
                AND pixel_width > 0 AND pixel_height > 0)
        ),
        UNIQUE(item_uuid, project_id),
        UNIQUE(item_uuid, project_id, category_code),
        FOREIGN KEY(project_id, category_code)
            REFERENCES report_evidence_categories(project_id, category_code)
            ON DELETE CASCADE,
        FOREIGN KEY(parent_item_uuid, project_id, category_code)
            REFERENCES report_evidence_items(item_uuid, project_id, category_code)
            ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY(organization_uuid, project_id)
            REFERENCES report_organizations(organization_uuid, project_id)
            ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS report_evidence_usages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usage_uuid TEXT NOT NULL UNIQUE CHECK(TRIM(usage_uuid) <> ''),
        project_id INTEGER NOT NULL,
        evidence_item_uuid TEXT NOT NULL,
        usage_kind TEXT NOT NULL CHECK(usage_kind IN (
            'member', 'covered_onsite', 'personnel_role', 'exam_proof', 'image_slot'
        )),
        related_member_uuid TEXT,
        related_item_uuid TEXT,
        slot_key TEXT NOT NULL DEFAULT '',
        sort_order INTEGER NOT NULL DEFAULT 0 CHECK(sort_order >= 0),
        created_at TEXT NOT NULL,
        CHECK(
            (usage_kind IN ('member', 'personnel_role', 'exam_proof')
                AND related_member_uuid IS NOT NULL AND related_item_uuid IS NULL)
            OR
            (usage_kind IN ('covered_onsite', 'image_slot')
                AND related_item_uuid IS NOT NULL AND related_member_uuid IS NULL)
        ),
        FOREIGN KEY(evidence_item_uuid, project_id)
            REFERENCES report_evidence_items(item_uuid, project_id) ON DELETE CASCADE,
        FOREIGN KEY(related_member_uuid, project_id)
            REFERENCES report_members(member_uuid, project_id)
            ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY(related_item_uuid, project_id)
            REFERENCES report_evidence_items(item_uuid, project_id)
            ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
    )
    """,
)

_INDEX_DDL: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_report_evidence_categories_project ON report_evidence_categories(project_id, category_code)",
    "CREATE INDEX IF NOT EXISTS idx_report_evidence_categories_na ON report_evidence_categories(project_id, is_not_applicable)",
    "CREATE INDEX IF NOT EXISTS idx_report_evidence_items_category ON report_evidence_items(project_id, category_code, sort_order, item_uuid)",
    "CREATE INDEX IF NOT EXISTS idx_report_evidence_items_kind ON report_evidence_items(project_id, item_kind)",
    "CREATE INDEX IF NOT EXISTS idx_report_evidence_items_sha256 ON report_evidence_items(sha256) WHERE sha256 IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_report_evidence_usages_kind ON report_evidence_usages(project_id, usage_kind)",
    "CREATE INDEX IF NOT EXISTS idx_report_evidence_usages_member ON report_evidence_usages(related_member_uuid) WHERE related_member_uuid IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_report_evidence_usages_related_item ON report_evidence_usages(related_item_uuid) WHERE related_item_uuid IS NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_report_evidence_usage_unique ON report_evidence_usages(project_id, evidence_item_uuid, usage_kind, COALESCE(related_member_uuid, ''), COALESCE(related_item_uuid, ''), slot_key)",
)

_TRIGGER_DDL: tuple[str, ...] = (
    """
    CREATE TRIGGER IF NOT EXISTS report_evidence_category_project_guard
    BEFORE INSERT ON report_evidence_categories
    BEGIN
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1 FROM projects WHERE id = NEW.project_id AND project_type = 'full_report'
        ) THEN RAISE(ABORT, 'REPORT_EVIDENCE_FULL_REPORT_REQUIRED') END;
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS report_evidence_parent_record_guard
    BEFORE INSERT ON report_evidence_items
    WHEN NEW.parent_item_uuid IS NOT NULL
    BEGIN
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1 FROM report_evidence_items
            WHERE item_uuid = NEW.parent_item_uuid
              AND project_id = NEW.project_id
              AND category_code = NEW.category_code
              AND item_kind = 'record'
        ) THEN RAISE(ABORT, 'REPORT_EVIDENCE_PARENT_RECORD_REQUIRED') END;
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS report_evidence_usage_semantic_guard
    BEFORE INSERT ON report_evidence_usages
    BEGIN
        SELECT CASE
            WHEN NEW.usage_kind = 'member' AND NOT EXISTS (
                SELECT 1 FROM report_evidence_items
                WHERE item_uuid = NEW.evidence_item_uuid AND project_id = NEW.project_id
                  AND item_kind = 'record' AND category_code IN ('travel_accommodation', 'onsite_process')
            ) THEN RAISE(ABORT, 'REPORT_EVIDENCE_MEMBER_USAGE_INVALID')
            WHEN NEW.usage_kind = 'covered_onsite' AND NOT (
                EXISTS (SELECT 1 FROM report_evidence_items WHERE item_uuid = NEW.evidence_item_uuid AND project_id = NEW.project_id AND item_kind = 'record' AND category_code = 'travel_accommodation')
                AND EXISTS (SELECT 1 FROM report_evidence_items WHERE item_uuid = NEW.related_item_uuid AND project_id = NEW.project_id AND item_kind = 'record' AND category_code = 'onsite_process')
            ) THEN RAISE(ABORT, 'REPORT_EVIDENCE_COVERAGE_USAGE_INVALID')
            WHEN NEW.usage_kind = 'personnel_role' AND NOT EXISTS (
                SELECT 1 FROM report_evidence_items WHERE item_uuid = NEW.evidence_item_uuid AND project_id = NEW.project_id AND item_kind = 'record' AND category_code = 'assessor_roster'
            ) THEN RAISE(ABORT, 'REPORT_EVIDENCE_PERSONNEL_USAGE_INVALID')
            WHEN NEW.usage_kind = 'exam_proof' AND NOT EXISTS (
                SELECT 1 FROM report_evidence_items WHERE item_uuid = NEW.evidence_item_uuid AND project_id = NEW.project_id AND item_kind = 'record' AND category_code = 'assessor_exam_proof'
            ) THEN RAISE(ABORT, 'REPORT_EVIDENCE_EXAM_USAGE_INVALID')
            WHEN NEW.usage_kind = 'image_slot' AND NOT (
                EXISTS (SELECT 1 FROM report_evidence_items WHERE item_uuid = NEW.evidence_item_uuid AND project_id = NEW.project_id AND item_kind = 'image')
                AND EXISTS (SELECT 1 FROM report_evidence_items WHERE item_uuid = NEW.related_item_uuid AND project_id = NEW.project_id AND item_kind = 'record')
            ) THEN RAISE(ABORT, 'REPORT_EVIDENCE_IMAGE_USAGE_INVALID')
        END;
    END
    """,
)

_REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "report_evidence_categories": frozenset(
        {"category_uuid", "project_id", "category_code", "is_not_applicable", "revision"}
    ),
    "report_evidence_items": frozenset(
        {"item_uuid", "project_id", "category_code", "parent_item_uuid", "item_kind", "metadata_json", "file_path", "sha256", "revision"}
    ),
    "report_evidence_usages": frozenset(
        {"usage_uuid", "project_id", "evidence_item_uuid", "usage_kind", "related_member_uuid", "related_item_uuid"}
    ),
}

REPORT_EVIDENCE_TABLES = frozenset(_REQUIRED_COLUMNS)

_REQUIRED_OBJECTS = frozenset(
    {
        "idx_report_evidence_categories_project",
        "idx_report_evidence_items_category",
        "idx_report_evidence_items_sha256",
        "idx_report_evidence_usages_kind",
        "idx_report_evidence_usage_unique",
        "report_evidence_category_project_guard",
        "report_evidence_parent_record_guard",
        "report_evidence_usage_semantic_guard",
    }
)


def ensure_report_evidence_schema(db: sqlite3.Connection) -> None:
    for statement in _DDL:
        db.execute(statement)
    for statement in _INDEX_DDL:
        db.execute(statement)
    for statement in _TRIGGER_DDL:
        db.execute(statement)


def initialize_report_evidence_categories(db: sqlite3.Connection, project_id: int) -> None:
    project = db.execute(
        "SELECT project_uuid, project_type FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if project is None or project["project_type"] != "full_report":
        return
    timestamp = datetime.now(timezone.utc).isoformat()
    for category in APPENDIX_B_CATEGORIES:
        category_code = str(category["category_code"])
        category_uuid = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"https://github.com/ZN4819/Open-FuLuA/report-evidence/{project['project_uuid']}/{category_code}",
            )
        )
        db.execute(
            """
            INSERT INTO report_evidence_categories (
                category_uuid, project_id, category_code, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(project_id, category_code) DO NOTHING
            """,
            (category_uuid, project_id, category_code, timestamp, timestamp),
        )
    count = int(
        db.execute(
            "SELECT COUNT(*) FROM report_evidence_categories WHERE project_id = ?",
            (project_id,),
        ).fetchone()[0]
    )
    if count != len(APPENDIX_B_CATEGORIES):
        raise RuntimeError("REPORT_EVIDENCE_CATEGORY_CARDINALITY_INVALID")


def initialize_existing_report_evidence_categories(db: sqlite3.Connection) -> None:
    for row in db.execute(
        "SELECT id FROM projects WHERE project_type = 'full_report' ORDER BY id"
    ).fetchall():
        initialize_report_evidence_categories(db, int(row["id"]))


def audit_report_evidence_schema(db: sqlite3.Connection) -> None:
    for table_name, required in _REQUIRED_COLUMNS.items():
        columns = {
            str(row["name"])
            for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if not required <= columns:
            raise RuntimeError(f"REPORT_EVIDENCE_SCHEMA_AUDIT_FAILED:{table_name}")
    objects = {
        str(row["name"])
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('index', 'trigger')"
        ).fetchall()
    }
    missing = _REQUIRED_OBJECTS - objects
    if missing:
        raise RuntimeError(
            f"REPORT_EVIDENCE_SCHEMA_AUDIT_FAILED:object:{','.join(sorted(missing))}"
        )
    invalid = db.execute(
        """
        SELECT p.project_uuid, COUNT(c.id) AS category_count
        FROM projects p
        LEFT JOIN report_evidence_categories c ON c.project_id = p.id
        WHERE p.project_type = 'full_report'
        GROUP BY p.id
        HAVING category_count <> ?
        """,
        (len(APPENDIX_B_CATEGORIES),),
    ).fetchone()
    if invalid is not None:
        raise RuntimeError("REPORT_EVIDENCE_CATEGORY_AUDIT_FAILED")
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise RuntimeError("REPORT_EVIDENCE_FOREIGN_KEY_AUDIT_FAILED")
