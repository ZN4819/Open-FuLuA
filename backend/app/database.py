from __future__ import annotations

import sqlite3
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import settings


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
    override = os.getenv("FULUA_DATABASE_PATH")
    if override:
        return Path(override)
    return settings.database_path


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
    finally:
        connection.close()


def init_db() -> None:
    with connect() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
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


def create_project(name: str) -> sqlite3.Row:
    timestamp = utc_now()
    with connect() as db:
        cursor = db.execute(
            "INSERT INTO projects (name, created_at, updated_at) VALUES (?, ?, ?)",
            (name, timestamp, timestamp),
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
        return get_project_by_id(project_id, db)


def get_project_by_id(project_id: int, db: sqlite3.Connection | None = None) -> sqlite3.Row | None:
    if db is not None:
        return db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    with connect() as connection:
        return connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()


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
            r.unit,
            r.object_name,
            r.record_text,
            r.sort_order,
            m.d,
            m.a,
            m.k,
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


def list_evidence_images(project_id: int, section_code: str, db: sqlite3.Connection | None = None) -> list[sqlite3.Row]:
    query = """
        SELECT
            id,
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
) -> sqlite3.Row | None:
    timestamp = utc_now()
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

        db.execute("DELETE FROM assessment_rows WHERE section_id = ?", (section["id"],))

        for index, row in enumerate(rows, start=1):
            sort_order = int(row.get("sort_order") or index)
            cursor = db.execute(
                """
                INSERT INTO assessment_rows
                    (section_id, unit, object_name, record_text, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    section["id"],
                    row.get("unit", ""),
                    row.get("object_name", ""),
                    row.get("record_text", ""),
                    sort_order,
                    timestamp,
                    timestamp,
                ),
            )
            row_id = int(cursor.lastrowid)
            metric = row.get("metric_result") or {}
            db.execute(
                """
                INSERT INTO metric_results
                    (row_id, d, a, k, object_score, unit_score, compliance)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id,
                    metric.get("d"),
                    metric.get("a"),
                    metric.get("k"),
                    metric.get("object_score"),
                    metric.get("unit_score"),
                    metric.get("compliance"),
                ),
            )
            for reference in row.get("cross_references") or []:
                db.execute(
                    """
                    INSERT INTO cross_references
                        (source_row_id, target_image_id, token, display_text)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        row_id,
                        reference.get("target_image_id"),
                        reference.get("token", ""),
                        reference.get("display_text", ""),
                    ),
                )

        return get_section(project_id, code, db)
