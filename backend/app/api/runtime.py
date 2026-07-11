"""仅供本机桌面侧车使用的数据迁移与恢复接口。"""

from __future__ import annotations

from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import threading
from typing import Iterator

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..runtime import SCHEMA_VERSION, resolve_runtime_paths
from ..services.backups import create_backup, list_backups, resolve_backup_id, restore_backup as restore_runtime_backup
from ..services.data_migration import migrate_legacy_data, preflight_migration


router = APIRouter(tags=["runtime"])


class MigrationRequest(BaseModel):
    source_root: str = Field(min_length=1, max_length=4096)


class RuntimeOperations:
    """迁移和恢复期间供应用中间件查询的排他写入门闩。"""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active = False
        self._writers = 0

    def writes_blocked(self) -> bool:
        with self._condition:
            return self._active

    def business_writes_active(self) -> int:
        with self._condition:
            return self._writers

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        with self._condition:
            if self._active:
                raise RuntimeError("当前正在进行数据迁移或恢复")
            self._active = True
            while self._writers:
                self._condition.wait()
        try:
            yield
        finally:
            with self._condition:
                self._active = False
                self._condition.notify_all()

    @contextmanager
    def business_write(self) -> Iterator[None]:
        with self._condition:
            if self._active:
                raise RuntimeError("正在进行数据迁移或恢复，请等待本地服务重新启动")
            self._writers += 1
        try:
            yield
        finally:
            with self._condition:
                self._writers -= 1
                self._condition.notify_all()


runtime_operations = RuntimeOperations()


def _source_root(payload: MigrationRequest | dict[str, str]) -> str:
    return payload.source_root if isinstance(payload, MigrationRequest) else payload["source_root"]


def _preflight_response(preflight) -> dict[str, object]:
    return {
        "source_root": preflight.source_root,
        "database_path": preflight.database_path,
        "storage_path": preflight.storage_path,
        "database_integrity": preflight.database_integrity,
        "project_count": preflight.project_count,
        "image_count": preflight.image_count,
        "missing_files": list(preflight.missing_files),
        "can_migrate": preflight.can_migrate,
        "blocking_reasons": [] if preflight.can_migrate else [preflight.reason or "无法确认旧数据完整性"],
    }


@router.post("/migration/preflight")
def migration_preflight(payload: MigrationRequest | dict[str, str]) -> dict[str, object]:
    return _preflight_response(preflight_migration(_source_root(payload)))


@router.post("/migration")
def migrate(payload: MigrationRequest) -> dict[str, object]:
    try:
        with runtime_operations.exclusive():
            paths = resolve_runtime_paths()
            create_backup(paths, "pre_migration")
            result = migrate_legacy_data(payload.source_root, paths)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not result.migrated:
        raise HTTPException(status_code=409, detail=result.reason or "旧数据未迁移")
    return {"migrated": True, "restart_required": True, "message": "旧数据已复制，正在重新启动本地服务"}


@router.get("/backups")
def backups() -> list[dict[str, str]]:
    return [
        {"id": item.path.name, "type": item.kind, "created_at": item.created_at}
        for item in list_backups(resolve_runtime_paths())
    ]


@router.post("/backups/{backup_id}/restore")
def restore_backup(backup_id: str) -> dict[str, object]:
    if not backup_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in backup_id):
        raise HTTPException(status_code=400, detail="备份标识无效")
    paths = resolve_runtime_paths()
    try:
        backup_path = resolve_backup_id(paths, backup_id)
        with runtime_operations.exclusive():
            result = restore_runtime_backup(paths, backup_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="备份标识无效或备份不存在") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not result.restored:
        raise HTTPException(status_code=409, detail=result.reason or "备份未恢复")
    return {"restored": True, "restart_required": True, "message": "恢复完成"}


@router.get("/status")
def runtime_status() -> dict[str, object]:
    paths = resolve_runtime_paths()
    return {
        "runtime_mode": paths.mode,
        "data_root": str(paths.data_root),
        "maintenance_active": runtime_operations.writes_blocked(),
        "business_writes_active": runtime_operations.business_writes_active(),
    }


@router.post("/upgrade/prepare")
def prepare_upgrade() -> dict[str, object]:
    try:
        with runtime_operations.exclusive():
            backup = create_backup(resolve_runtime_paths(), "pre_upgrade")
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ready": True, "backup_id": backup.path.name, "schema_version": SCHEMA_VERSION}


@router.get("/integrity")
def integrity_check() -> dict[str, str]:
    database_path = resolve_runtime_paths().database_path
    try:
        with closing(sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)) as connection:
            rows = connection.execute("PRAGMA integrity_check").fetchall()
        integrity = "ok" if rows == [("ok",)] else "corrupt"
    except (OSError, sqlite3.DatabaseError):
        integrity = "corrupt"
    return {"integrity": integrity, "schema_version": SCHEMA_VERSION}
