"""为桌面安装验收创建可迁移的真实 schema 3 数据副本。"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--project-name", required=True)
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    data_root = Path(arguments.data_root).expanduser().resolve()
    database_path = data_root / "data" / "app.db"
    if data_root.exists() and any(data_root.iterdir()):
        raise RuntimeError("schema 3 fixture 目标目录必须为空")

    os.environ["FULUA_DATA_DIR"] = str(data_root)
    os.environ["FULUA_DATABASE_PATH"] = str(database_path)
    from app import database

    database.init_db()
    project = database.create_project(str(arguments.project_name))
    storage = data_root / "storage"
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "schema3-retained.txt").write_text("schema3-retained", encoding="utf-8")

    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        for trigger in (
            "projects_identity_insert_guard",
            "projects_identity_update_guard",
            "projects_identity_immutable_guard",
        ):
            connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        connection.execute("DROP INDEX IF EXISTS idx_projects_project_uuid")
        connection.execute("DROP TABLE IF EXISTS project_upgrade_operations")
        connection.execute("DROP TABLE IF EXISTS app_metadata")
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
        migrated_project = connection.execute(
            "SELECT id, name FROM projects WHERE id = ?",
            (project["id"],),
        ).fetchone()
        if migrated_project != (project["id"], arguments.project_name):
            raise RuntimeError("schema 3 fixture 项目写入失败")
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
