"""桌面运行时数据库和 storage 的一致性备份与恢复。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.runtime import RuntimePaths
from app.services.data_migration import file_manifest, remove_sqlite_sidecars, sqlite_backup, validate_database_and_evidence


_BACKUP_KINDS = {"daily", "pre_upgrade", "pre_migration", "pre_restore", "recovery_rollback"}
_RETENTION_LIMITS = {"daily": 7, "pre_upgrade": 3, "pre_migration": 3}
_operation_lock = threading.RLock()


@dataclass(frozen=True)
class BackupInfo:
    path: Path
    kind: str
    created_at: str
    database_path: Path
    storage_path: Path


@dataclass(frozen=True)
class RestoreResult:
    restored: bool
    restart_required: bool
    reason: str = ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest_manifest(manifest: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


def _copy_storage(source: Path, destination: Path) -> dict[str, str]:
    destination.mkdir(parents=True, exist_ok=True)
    source_root = source.resolve(strict=True)
    files: list[Path] = []
    stack = [source_root]
    while stack:
        item = stack.pop()
        if _is_reparse_point(item):
            raise ValueError("数据目录包含重解析点")
        item.resolve(strict=True).relative_to(source_root)
        if item.is_dir():
            stack.extend(item.iterdir())
        elif item.is_file():
            files.append(item)
    for source_file in sorted(files):
        target = destination / source_file.relative_to(source_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source_file, target)
        except OSError:
            shutil.copy2(source_file, target)
    return file_manifest(destination)


def _read_backup(path: Path) -> BackupInfo | None:
    metadata_path = path / "metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        kind = str(metadata["kind"])
        if kind not in _BACKUP_KINDS:
            return None
        return BackupInfo(path, kind, str(metadata["created_at"]), path / "data" / "app.db", path / "storage")
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _verify_staged_copy(source: BackupInfo, database_path: Path, storage_path: Path) -> None:
    try:
        metadata = json.loads((source.path / "metadata.json").read_text(encoding="utf-8"))
        expected_database = str(metadata["database"]["sha256"])
        expected_files = str(metadata["files"]["sha256"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("备份校验元数据无效") from exc
    if hashlib.sha256(database_path.read_bytes()).hexdigest() != expected_database:
        raise ValueError("备份数据库哈希不匹配")
    if _digest_manifest(file_manifest(storage_path)) != expected_files:
        raise ValueError("备份文件清单哈希不匹配")


def _write_recovery_rollback_metadata(rollback: Path, *, state: str, failed_stage: str = "") -> None:
    metadata_path = rollback / "metadata.json"
    previous: dict[str, object] = {}
    try:
        previous = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        pass
    database_path = rollback / "data" / "app.db"
    storage_manifest = file_manifest(rollback / "storage")
    metadata = {
        "kind": "recovery_rollback",
        "created_at": previous.get("created_at") or _utc_now(),
        "state": state,
        "failed_stage": failed_stage,
        "database": {"sha256": hashlib.sha256(database_path.read_bytes()).hexdigest()},
        "files": {"count": len(storage_manifest), "sha256": _digest_manifest(storage_manifest)},
        "verification": {"snapshot": "preserved", "replay": state},
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def _replay_rollback_database(paths: RuntimePaths, rollback: Path) -> None:
    remove_sqlite_sidecars(paths.database_path)
    if paths.database_path.exists():
        paths.database_path.unlink()
    paths.database_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(rollback / "data" / "app.db", paths.database_path)


def _replay_rollback_storage(paths: RuntimePaths, rollback: Path) -> None:
    if paths.storage_path.exists():
        shutil.rmtree(paths.storage_path)
    shutil.copytree(rollback / "storage", paths.storage_path, copy_function=shutil.copy2)


def _apply_retention(paths: RuntimePaths, kind: str) -> None:
    limit = _RETENTION_LIMITS.get(kind)
    if limit is None:
        return
    matching = sorted((item for item in list_backups(paths) if item.kind == kind), key=lambda item: item.created_at, reverse=True)
    for old in matching[limit:]:
        shutil.rmtree(old.path)


def _validate_backup_root(root: Path) -> Path:
    if not root.is_dir() or _is_reparse_point(root):
        raise ValueError("备份根目录为重解析点或不安全")
    resolved = root.resolve(strict=True)
    if resolved != root.absolute():
        raise ValueError("备份根目录解析结果不安全")
    return resolved


def create_backup(paths: RuntimePaths, kind: str) -> BackupInfo:
    if kind not in _BACKUP_KINDS:
        raise ValueError("不支持的备份类型")
    if not paths.database_path.is_file() or not paths.storage_path.is_dir():
        raise FileNotFoundError("当前运行时数据不完整，不能创建备份")
    with _operation_lock:
        paths.backup_path.mkdir(parents=True, exist_ok=True)
        _validate_backup_root(paths.backup_path)
        staging = paths.backup_path / f"staging-{uuid.uuid4()}"
        created_at = _utc_now()
        try:
            database_path = staging / "data" / "app.db"
            sqlite_backup(paths.database_path, database_path)
            storage_manifest = _copy_storage(paths.storage_path, staging / "storage")
            valid, reason, projects, images, missing = validate_database_and_evidence(database_path, staging / "storage")
            if not valid:
                raise RuntimeError(reason if not missing else "备份包含缺失的证据图片")
            metadata = {
                "kind": kind,
                "created_at": created_at,
                "database": {"sha256": hashlib.sha256(database_path.read_bytes()).hexdigest()},
                "files": {"count": len(storage_manifest), "sha256": _digest_manifest(storage_manifest)},
                "verification": {"integrity": "ok", "projects": projects, "images": images},
            }
            (staging / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            published = paths.backup_path / f"{kind}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex[:8]}"
            _validate_backup_root(paths.backup_path)
            os.replace(staging, published)
            safe_published = _validate_backup_tree(paths.backup_path, published)
            result = _read_backup(safe_published)
            assert result is not None
            _apply_retention(paths, kind)
            return result
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise


def list_backups(paths: RuntimePaths) -> list[BackupInfo]:
    if not paths.backup_path.is_dir() or _is_reparse_point(paths.backup_path):
        return []
    safe: list[BackupInfo] = []
    for item in paths.backup_path.iterdir():
        try:
            if not item.is_dir() or _is_reparse_point(item):
                continue
            resolved = _validate_backup_tree(paths.backup_path, item)
            info = _read_backup(resolved)
            if info is not None:
                safe.append(info)
        except (OSError, ValueError):
            continue
    return sorted(safe, key=lambda item: item.created_at, reverse=True)


def _is_reparse_point(path: Path) -> bool:
    try:
        stat = path.lstat()
    except OSError:
        return True
    return path.is_symlink() or bool(getattr(stat, "st_file_attributes", 0) & 0x400)


def _validate_backup_tree(root: Path, candidate: Path) -> Path:
    if _is_reparse_point(root) or _is_reparse_point(candidate):
        raise ValueError("备份包含重解析点")
    resolved_root = root.resolve(strict=True)
    resolved_candidate = candidate.resolve(strict=True)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("备份路径不安全") from exc
    stack = [resolved_candidate]
    while stack:
        current = stack.pop()
        if _is_reparse_point(current):
            raise ValueError("备份包含重解析点")
        try:
            current.resolve(strict=True).relative_to(resolved_candidate)
        except (OSError, ValueError) as exc:
            raise ValueError("备份内部路径不安全") from exc
        if current.is_dir():
            stack.extend(current.iterdir())
    metadata = resolved_candidate / "metadata.json"
    database = resolved_candidate / "data" / "app.db"
    storage = resolved_candidate / "storage"
    if not metadata.is_file() or not database.is_file() or not storage.is_dir():
        raise ValueError("备份结构不完整")
    return resolved_candidate


def resolve_backup_id(paths: RuntimePaths, backup_id: str) -> Path:
    if not backup_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in backup_id):
        raise ValueError("备份标识无效")
    root = paths.backup_path
    candidate = root / backup_id
    if not root.is_dir() or _is_reparse_point(root) or not candidate.is_dir() or _is_reparse_point(candidate):
        raise ValueError("备份不存在或路径不安全")
    try:
        resolved_candidate = _validate_backup_tree(root, candidate)
    except (OSError, ValueError) as exc:
        raise ValueError(str(exc) or "备份路径不安全") from exc
    if _read_backup(resolved_candidate) is None:
        raise ValueError("未知备份")
    return resolved_candidate


def restore_backup(paths: RuntimePaths, backup_path: Path | str, *, allow_damaged_live: bool = False) -> RestoreResult:
    try:
        safe_path = resolve_backup_id(paths, Path(backup_path).name)
    except ValueError as exc:
        return RestoreResult(False, False, str(exc))
    requested = _read_backup(safe_path)
    if requested is None:
        return RestoreResult(False, False, "备份不存在或元数据无效")
    with _operation_lock:
        rollback: Path | None = None
        staging = paths.backup_path / f"restore-staging-{uuid.uuid4()}"
        try:
            safe_path = _validate_backup_tree(paths.backup_path, safe_path)
            requested = _read_backup(safe_path)
            if requested is None:
                raise ValueError("备份元数据无效")
            # 正常恢复先做一致性 pre_restore；离线损坏恢复改为保留原始现场字节。
            if not allow_damaged_live:
                create_backup(paths, "pre_restore")
            # pre_restore 本身会遍历备份目录；真正复制前再次绑定并校验源树，避免校验后替换。
            safe_path = _validate_backup_tree(paths.backup_path, safe_path)
            requested = _read_backup(safe_path)
            if requested is None:
                raise ValueError("备份元数据无效")
            staged_database = staging / "data" / "app.db"
            staged_database.parent.mkdir(parents=True, exist_ok=True)
            # 备份目录中的数据库已经是离线、无 WAL 的一致性快照；按字节复制
            # 才能继续使用冻结的 SHA-256 验证。再次调用 SQLite backup API 可能
            # 重写页头，使逻辑等价的数据库产生不同文件哈希。
            shutil.copy2(requested.database_path, staged_database)
            _copy_storage(requested.storage_path, staging / "storage")
            _verify_staged_copy(requested, staging / "data" / "app.db", staging / "storage")
            valid, reason, _, _, missing = validate_database_and_evidence(staging / "data" / "app.db", staging / "storage")
            if not valid:
                raise RuntimeError(reason if not missing else "备份证据图片不完整")

            # 完整 live 必须先快照；离线重试时若 live 已被二次回放破坏，则保留既有
            # recovery_rollback 作为唯一可信源，不伪造不完整的新快照。
            can_snapshot_live = paths.database_path.is_file() and paths.storage_path.is_dir()
            if not allow_damaged_live or can_snapshot_live:
                rollback = paths.backup_path / f"recovery_rollback-{uuid.uuid4()}"
                (rollback / "data").mkdir(parents=True)
                if allow_damaged_live:
                    shutil.copy2(paths.database_path, rollback / "data" / "app.db")
                else:
                    sqlite_backup(paths.database_path, rollback / "data" / "app.db")
                _copy_storage(paths.storage_path, rollback / "storage")
                _write_recovery_rollback_metadata(rollback, state="captured")
            remove_sqlite_sidecars(paths.database_path)
            if paths.database_path.exists():
                paths.database_path.unlink()
            if paths.storage_path.exists():
                shutil.rmtree(paths.storage_path)
            paths.database_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging / "data" / "app.db", paths.database_path)
            os.replace(staging / "storage", paths.storage_path)
            valid, reason, _, _, missing = validate_database_and_evidence(paths.database_path, paths.storage_path)
            if not valid:
                raise RuntimeError(reason if not missing else "恢复后的证据图片校验失败")
            if rollback is not None:
                shutil.rmtree(rollback, ignore_errors=True)
            shutil.rmtree(staging, ignore_errors=True)
            return RestoreResult(True, True)
        except Exception as exc:
            replay_failures: list[str] = []
            if rollback is not None:
                try:
                    _replay_rollback_database(paths, rollback)
                except Exception:
                    replay_failures.append("database")
                try:
                    _replay_rollback_storage(paths, rollback)
                except Exception:
                    replay_failures.append("storage")
                if replay_failures:
                    try:
                        _write_recovery_rollback_metadata(rollback, state="replay_failed", failed_stage=replay_failures[0])
                    except Exception:
                        pass
                else:
                    shutil.rmtree(rollback, ignore_errors=True)
            shutil.rmtree(staging, ignore_errors=True)
            reason = str(exc)
            if replay_failures:
                reason = f"{reason}；回放失败阶段：{','.join(replay_failures)}，恢复现场已保留"
            return RestoreResult(False, False, reason)
