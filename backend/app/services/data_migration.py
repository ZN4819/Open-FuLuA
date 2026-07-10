"""旧 Web 数据的只读预检与可回滚复制迁移。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.runtime import BACKEND_VERSION, RuntimePaths


@dataclass(frozen=True)
class MigrationPreflight:
    source_root: str
    database_path: str | None
    storage_path: str | None
    database_integrity: str
    project_count: int
    image_count: int
    missing_files: tuple[str, ...]
    can_migrate: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["blocking_reasons"] = list(self.blocking_reasons)
        return result

    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        return (self.reason,) if self.reason else ()


@dataclass(frozen=True)
class MigrationResult:
    migrated: bool
    already_installed: bool = False
    reason: str = ""
    snapshot_path: str | None = None
    restart_required: bool = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_resolve(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _source_layout(source_root: Path) -> tuple[Path, Path] | tuple[None, None]:
    root = _safe_resolve(source_root)
    data_root_layout = (root / "data" / "app.db", root / "storage")
    repository_layout = (root / "backend" / "data" / "app.db", root / "storage")
    candidates = [
        item for item in (data_root_layout, repository_layout) if item[0].is_file() and item[1].is_dir()
    ]
    if len(candidates) != 1:
        return None, None
    return candidates[0]


def sqlite_backup(source_path: Path, destination_path: Path) -> None:
    """以 SQLite backup API 创建一致性副本，绝不复制 WAL 相关文件。"""
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if destination_path.exists():
        destination_path.unlink()
    source = sqlite3.connect(str(source_path))
    destination = sqlite3.connect(str(destination_path))
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def _database_check(database_path: Path) -> tuple[str, int, int, tuple[str, ...]]:
    db = sqlite3.connect(database_path)
    try:
        integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0]).lower()
        if integrity != "ok":
            return integrity, 0, 0, ()
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "projects" not in tables or "evidence_images" not in tables:
            return "schema_invalid", 0, 0, ()
        projects = int(db.execute("SELECT COUNT(*) FROM projects").fetchone()[0])
        rows = tuple(str(row[0]) for row in db.execute("SELECT file_path FROM evidence_images"))
        return "ok", projects, len(rows), rows
    finally:
        db.close()


def _referenced_files(storage_path: Path, references: tuple[str, ...]) -> tuple[str, ...]:
    resolved_storage = _safe_resolve(storage_path)
    missing: list[str] = []
    for reference in references:
        candidate = _safe_resolve(resolved_storage / reference)
        if not candidate.is_relative_to(resolved_storage) or not candidate.is_file():
            missing.append(reference)
    return tuple(sorted(missing))


def validate_database_and_evidence(database_path: Path, storage_path: Path) -> tuple[bool, str, int, int, tuple[str, ...]]:
    """校验数据库结构、完整性及所有证据文件引用；返回内容安全的摘要。"""
    try:
        integrity, projects, images, references = _database_check(database_path)
    except sqlite3.Error:
        return False, "数据库损坏或无法读取", 0, 0, ()
    if integrity != "ok":
        return False, "数据库完整性或结构校验失败", projects, images, ()
    missing = _referenced_files(storage_path, references)
    if missing:
        return False, "存在缺失的证据图片", projects, images, missing
    return True, "", projects, images, ()


def preflight_migration(source_root: Path | str) -> MigrationPreflight:
    root = _safe_resolve(Path(source_root))
    if not root.is_dir():
        return MigrationPreflight(str(root), None, None, "not_checked", 0, 0, (), False, "源目录不存在")
    database_path, storage_path = _source_layout(root)
    if database_path is None or storage_path is None:
        return MigrationPreflight(str(root), None, None, "not_checked", 0, 0, (), False, "源目录格式不存在或存在歧义")

    # 预检只在临时目录中创建一致性副本，源数据库和 storage 均保持只读。
    try:
        with tempfile.TemporaryDirectory(prefix="fulua-preflight-") as temporary:
            snapshot = Path(temporary) / "app.db"
            sqlite_backup(database_path, snapshot)
            valid, reason, projects, images, missing = validate_database_and_evidence(snapshot, storage_path)
            return MigrationPreflight(
                str(root), str(database_path), str(storage_path),
                "ok" if valid or reason == "存在缺失的证据图片" else "failed",
                projects, images, missing, valid, reason,
            )
    except (OSError, sqlite3.Error):
        return MigrationPreflight(str(root), str(database_path), str(storage_path), "failed", 0, 0, (), False, "数据库损坏或无法读取")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_manifest(root: Path) -> dict[str, str]:
    return {
        file.relative_to(root).as_posix(): _sha256(file)
        for file in sorted(root.rglob("*"))
        if file.is_file()
    }


def _target_has_user_data(paths: RuntimePaths) -> bool:
    if paths.database_path.exists():
        try:
            db = sqlite3.connect(paths.database_path)
            try:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
                if "projects" not in tables:
                    return paths.database_path.stat().st_size > 0
                if int(db.execute("SELECT COUNT(*) FROM projects").fetchone()[0]) > 0:
                    return True
            finally:
                db.close()
        except sqlite3.Error:
            return True
    return paths.storage_path.exists() and any(paths.storage_path.iterdir())


def _remove_empty_storage_tree(storage_path: Path) -> None:
    """仅移除已确认没有文件的目标目录；空子目录不是用户数据。"""
    if not storage_path.exists():
        return
    if any(item.is_file() for item in storage_path.rglob("*")):
        raise RuntimeError("目标 storage 包含文件，拒绝覆盖")
    shutil.rmtree(storage_path)


def _write_state(paths: RuntimePaths, *, source_root: Path, started_at: str, status: str, reason: str, projects: int, images: int, manifest: dict[str, str]) -> None:
    state = {
        "source_root": str(source_root),
        "started_at": started_at,
        "finished_at": _utc_now(),
        "tool_version": BACKEND_VERSION,
        "status": status,
        "reason": reason,
        "statistics": {"projects": projects, "images": images, "files": len(manifest)},
        "manifest_summary": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
    }
    paths.migration_path.mkdir(parents=True, exist_ok=True)
    state_path = paths.migration_path / "migration-state.json"
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, state_path)


def migrate_legacy_data(source_root: Path | str, paths: RuntimePaths) -> MigrationResult:
    """复制迁移；失败不修改源，且不会覆盖已有桌面用户数据。"""
    started_at = _utc_now()
    preflight = preflight_migration(source_root)
    if not preflight.can_migrate:
        _write_state(
            paths, source_root=_safe_resolve(Path(source_root)), started_at=started_at,
            status="failed", reason=preflight.reason, projects=preflight.project_count,
            images=preflight.image_count, manifest={"missing_files": str(len(preflight.missing_files))},
        )
        return MigrationResult(False, reason=preflight.reason)
    source_db = Path(preflight.database_path or "")
    source_storage = Path(preflight.storage_path or "")
    source_files = file_manifest(source_storage)
    source_database_hash = _sha256(source_db)
    source_fingerprint = hashlib.sha256((str(source_db) + source_database_hash + json.dumps(source_files, sort_keys=True)).encode()).hexdigest()
    snapshots = paths.migration_path / "snapshots"
    published_snapshot = snapshots / source_fingerprint
    if published_snapshot.exists() and paths.database_path.exists():
        return MigrationResult(False, already_installed=True, reason="相同源数据已迁移", snapshot_path=str(published_snapshot))
    if _target_has_user_data(paths):
        _write_state(
            paths, source_root=_safe_resolve(Path(source_root)), started_at=started_at,
            status="failed", reason="目标已有用户数据，请恢复、新建或选择其他目标",
            projects=preflight.project_count, images=preflight.image_count, manifest={},
        )
        return MigrationResult(False, reason="目标已有用户数据，请恢复、新建或选择其他目标")

    staging = paths.migration_path / f"staging-{uuid.uuid4()}"
    installed = False
    database_installed = False
    try:
        staging_data = staging / "data" / "app.db"
        staging_storage = staging / "storage"
        sqlite_backup(source_db, staging_data)
        shutil.copytree(source_storage, staging_storage)
        manifest = file_manifest(staging)
        # 复制过程结束后再次确认源没有变化，无法确认稳定性即 fail-closed。
        if source_files != file_manifest(source_storage) or source_database_hash != _sha256(source_db):
            raise RuntimeError("迁移期间源数据发生变化")
        valid, reason, projects, images, missing = validate_database_and_evidence(staging_data, staging_storage)
        if not valid:
            raise RuntimeError(reason if not missing else "存在缺失的证据图片")

        snapshots.mkdir(parents=True, exist_ok=True)
        if published_snapshot.exists():
            shutil.rmtree(staging)
        else:
            os.replace(staging, published_snapshot)

        install = paths.migration_path / f"installing-{uuid.uuid4()}"
        sqlite_backup(published_snapshot / "data" / "app.db", install / "data" / "app.db")
        shutil.copytree(published_snapshot / "storage", install / "storage")
        paths.database_path.parent.mkdir(parents=True, exist_ok=True)
        if paths.database_path.exists():
            paths.database_path.unlink()
        _remove_empty_storage_tree(paths.storage_path)
        os.replace(install / "data" / "app.db", paths.database_path)
        database_installed = True
        os.replace(install / "storage", paths.storage_path)
        installed = True
        shutil.rmtree(install, ignore_errors=True)
        valid, reason, projects, images, missing = validate_database_and_evidence(paths.database_path, paths.storage_path)
        if not valid:
            raise RuntimeError(reason if not missing else "安装后的证据图片校验失败")
        _write_state(paths, source_root=_safe_resolve(Path(source_root)), started_at=started_at, status="completed", reason="", projects=projects, images=images, manifest=manifest)
        return MigrationResult(True, snapshot_path=str(published_snapshot), restart_required=True)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        if installed or database_installed:
            paths.database_path.unlink(missing_ok=True)
            shutil.rmtree(paths.storage_path, ignore_errors=True)
        _write_state(
            paths, source_root=_safe_resolve(Path(source_root)), started_at=started_at,
            status="failed", reason=str(exc), projects=preflight.project_count,
            images=preflight.image_count, manifest={},
        )
        return MigrationResult(False, reason=str(exc))
