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

from app.contracts import (
    FULL_REPORT_TEMPLATE_ASSET_SET_HASH,
    FULL_REPORT_TEMPLATE_EDITION,
    FULL_REPORT_TEMPLATE_PACKAGE_ID,
    FULL_REPORT_TEMPLATE_REVISION,
)
from app.runtime import BACKEND_VERSION, RuntimePaths
from app.report_core.contracts import REPORT_CORE_AUXILIARY_TABLES, REPORT_CORE_ENTITY_TABLES
from app.report_core.schema import audit_report_core_schema


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


def remove_sqlite_sidecars(database_path: Path) -> None:
    """已由 backup API 固化后，删除即将被替换数据库的旧 WAL/SHM。"""
    database_path.with_name(f"{database_path.name}-wal").unlink(missing_ok=True)
    database_path.with_name(f"{database_path.name}-shm").unlink(missing_ok=True)


def _database_check(database_path: Path) -> tuple[str, int, int, tuple[str, ...]]:
    db = sqlite3.connect(database_path)
    db.row_factory = sqlite3.Row
    try:
        integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0]).lower()
        if integrity != "ok":
            return integrity, 0, 0, ()
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "projects" not in tables or "evidence_images" not in tables:
            return "schema_invalid", 0, 0, ()
        schema_version = int(db.execute("PRAGMA user_version").fetchone()[0])
        if schema_version >= 4:
            project_columns = {
                row[1] for row in db.execute("PRAGMA table_info(projects)").fetchall()
            }
            required_columns = {
                "project_uuid",
                "project_type",
                "workflow_status",
                "template_package_id",
                "template_edition",
                "template_revision",
                "template_asset_set_hash",
                "source_project_uuid",
                "created_by_operation",
            }
            if not required_columns <= project_columns:
                return "schema_invalid", 0, 0, ()
            invalid_projects = int(
                db.execute(
                    """
                    SELECT COUNT(*) FROM projects
                    WHERE project_uuid IS NULL OR TRIM(project_uuid) = ''
                       OR project_type NOT IN ('appendix_a', 'full_report')
                       OR workflow_status NOT IN ('draft', 'ready_for_review', 'confirmed')
                       OR created_by_operation NOT IN (
                           'create', 'migration_import', 'roundtrip_import', 'upgrade_copy'
                       )
                       OR (project_type = 'appendix_a' AND (
                           template_package_id IS NOT NULL OR template_edition IS NOT NULL OR
                           template_revision IS NOT NULL OR template_asset_set_hash IS NOT NULL OR
                           source_project_uuid IS NOT NULL
                       ))
                        OR (project_type = 'full_report' AND (
                            template_package_id IS NULL OR
                            template_package_id <> ? OR
                            template_edition IS NULL OR
                            template_edition <> ? OR
                            template_revision IS NULL OR
                            template_revision <> ? OR
                            template_asset_set_hash IS NULL OR
                            template_asset_set_hash <> ?
                        ))
                        OR (created_by_operation = 'upgrade_copy' AND (
                            project_type <> 'full_report' OR
                            source_project_uuid IS NULL OR TRIM(source_project_uuid) = ''
                        ))
                        OR (created_by_operation <> 'upgrade_copy' AND source_project_uuid IS NOT NULL)
                    """
                    ,
                    (
                        FULL_REPORT_TEMPLATE_PACKAGE_ID,
                        FULL_REPORT_TEMPLATE_EDITION,
                        FULL_REPORT_TEMPLATE_REVISION,
                        FULL_REPORT_TEMPLATE_ASSET_SET_HASH,
                    ),
                ).fetchone()[0]
            )
            duplicate_uuids = int(
                db.execute(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT project_uuid FROM projects
                        GROUP BY project_uuid HAVING COUNT(*) > 1
                    )
                    """
                ).fetchone()[0]
            )
            try:
                for row in db.execute("SELECT project_uuid, source_project_uuid FROM projects"):
                    uuid.UUID(str(row[0]))
                    if row[1] is not None:
                        uuid.UUID(str(row[1]))
            except (ValueError, TypeError, AttributeError):
                return "schema_invalid", 0, 0, ()
            foreign_key_errors = db.execute("PRAGMA foreign_key_check").fetchone()
            if invalid_projects or duplicate_uuids or foreign_key_errors is not None:
                return "schema_invalid", 0, 0, ()
        if schema_version >= 5:
            try:
                audit_report_core_schema(db)
            except (RuntimeError, sqlite3.Error):
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


def sqlite_source_manifest(database_path: Path) -> dict[str, str]:
    return {item.name: _sha256(item) for item in (database_path, database_path.with_name(database_path.name + "-wal"), database_path.with_name(database_path.name + "-shm")) if item.is_file()}


def _target_has_user_data(paths: RuntimePaths) -> bool:
    if paths.database_path.exists():
        try:
            db = sqlite3.connect(paths.database_path)
            try:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
                allowed = {
                    "projects", "appendix_sections", "assessment_rows", "metric_results",
                    "evidence_images", "cross_references", "render_jobs", "validation_issues",
                    "docx_import_jobs", "record_templates", "record_template_slots",
                    "section_subsystems", "app_metadata", "project_upgrade_operations",
                    "sqlite_sequence", *REPORT_CORE_ENTITY_TABLES, *REPORT_CORE_AUXILIARY_TABLES,
                }
                if not tables <= allowed or "projects" not in tables:
                    return True
                for table in ("projects", "appendix_sections", "assessment_rows", "metric_results", "evidence_images", "cross_references", "render_jobs", "validation_issues", "docx_import_jobs", "section_subsystems"):
                    if table in tables and int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) > 0:
                        return True
                if "record_templates" in tables and int(db.execute("SELECT COUNT(*) FROM record_templates WHERE source_type != 'system' OR deleted_at IS NOT NULL").fetchone()[0]) > 0:
                    return True
                if "record_template_slots" in tables and int(db.execute("SELECT COUNT(*) FROM record_template_slots WHERE is_customized != 0").fetchone()[0]) > 0:
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
    target_db = _safe_resolve(paths.database_path)
    target_storage = _safe_resolve(paths.storage_path)
    if source_db == target_db or source_storage == target_storage or source_db.is_relative_to(target_storage) or target_db.is_relative_to(source_storage):
        reason = "源目录与桌面目标重叠，不能迁移"
        _write_state(paths, source_root=_safe_resolve(Path(source_root)), started_at=started_at, status="failed", reason=reason, projects=preflight.project_count, images=preflight.image_count, manifest={})
        return MigrationResult(False, reason=reason)
    source_files = file_manifest(source_storage)
    source_database_files = sqlite_source_manifest(source_db)
    source_database_hash = source_database_files.get(source_db.name, "")
    source_fingerprint = hashlib.sha256((str(source_db) + json.dumps(source_database_files, sort_keys=True) + json.dumps(source_files, sort_keys=True)).encode()).hexdigest()
    snapshots = paths.migration_path / "snapshots"
    published_snapshot = snapshots / source_fingerprint
    if published_snapshot.exists() and paths.database_path.exists() and paths.storage_path.exists():
        snapshot_valid, _, snapshot_projects, snapshot_images, _ = validate_database_and_evidence(published_snapshot / "data" / "app.db", published_snapshot / "storage")
        target_valid, _, target_projects, target_images, _ = validate_database_and_evidence(paths.database_path, paths.storage_path)
        if snapshot_valid and target_valid and snapshot_projects == target_projects and snapshot_images == target_images and file_manifest(published_snapshot / "storage") == file_manifest(paths.storage_path):
            return MigrationResult(False, already_installed=True, reason="相同源数据已迁移", snapshot_path=str(published_snapshot))
    if _target_has_user_data(paths):
        _write_state(
            paths, source_root=_safe_resolve(Path(source_root)), started_at=started_at,
            status="failed", reason="目标已有用户数据，请恢复、新建或选择其他目标",
            projects=preflight.project_count, images=preflight.image_count, manifest={},
        )
        return MigrationResult(False, reason="目标已有用户数据，请恢复、新建或选择其他目标")

    staging = paths.migration_path / f"staging-{uuid.uuid4()}"
    rollback = paths.migration_path / f"rollback-{uuid.uuid4()}"
    installed = False
    database_installed = False
    try:
        staging_data = staging / "data" / "app.db"
        staging_storage = staging / "storage"
        sqlite_backup(source_db, staging_data)
        shutil.copytree(source_storage, staging_storage)
        manifest = file_manifest(staging)
        # 复制过程结束后再次确认源没有变化，无法确认稳定性即 fail-closed。
        if source_files != file_manifest(source_storage) or source_database_files != sqlite_source_manifest(source_db):
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
            sqlite_backup(paths.database_path, rollback / "data" / "app.db")
        if paths.storage_path.exists():
            shutil.copytree(paths.storage_path, rollback / "storage")
        remove_sqlite_sidecars(paths.database_path)
        if paths.database_path.exists():
            paths.database_path.unlink()
        _remove_empty_storage_tree(paths.storage_path)
        os.replace(install / "data" / "app.db", paths.database_path)
        database_installed = True
        os.replace(install / "storage", paths.storage_path)
        installed = True
        shutil.rmtree(install, ignore_errors=True)
        shutil.rmtree(rollback, ignore_errors=True)
        valid, reason, projects, images, missing = validate_database_and_evidence(paths.database_path, paths.storage_path)
        if not valid:
            raise RuntimeError(reason if not missing else "安装后的证据图片校验失败")
        _write_state(paths, source_root=_safe_resolve(Path(source_root)), started_at=started_at, status="completed", reason="", projects=projects, images=images, manifest=manifest)
        return MigrationResult(True, snapshot_path=str(published_snapshot), restart_required=True)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        if installed or database_installed:
            remove_sqlite_sidecars(paths.database_path)
            paths.database_path.unlink(missing_ok=True)
            shutil.rmtree(paths.storage_path, ignore_errors=True)
            if (rollback / "data" / "app.db").exists():
                sqlite_backup(rollback / "data" / "app.db", paths.database_path)
            if (rollback / "storage").exists():
                shutil.copytree(rollback / "storage", paths.storage_path)
        shutil.rmtree(rollback, ignore_errors=True)
        _write_state(
            paths, source_root=_safe_resolve(Path(source_root)), started_at=started_at,
            status="failed", reason=str(exc), projects=preflight.project_count,
            images=preflight.image_count, manifest={},
        )
        return MigrationResult(False, reason=str(exc))
