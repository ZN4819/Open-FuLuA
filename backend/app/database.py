from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

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


def ensure_database_dir(path: Path | None = None) -> None:
    database_path = path or settings.database_path
    database_path.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    ensure_database_dir()
    connection = sqlite3.connect(settings.database_path)
    connection.row_factory = sqlite3.Row
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
