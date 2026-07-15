from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


SCHEMA_VERSION = "5"
BACKEND_VERSION = "0.1.0"


@dataclass(frozen=True)
class RuntimePaths:
    data_root: Path
    database_path: Path
    storage_path: Path
    log_path: Path
    backup_path: Path
    migration_path: Path
    mode: str


def resolve_runtime_paths() -> RuntimePaths:
    repository_root = Path(__file__).resolve().parents[2]
    configured_data_root = os.getenv("FULUA_DATA_DIR")
    mode = "desktop" if configured_data_root else "development"
    data_root = Path(configured_data_root) if configured_data_root else repository_root

    database_path = Path(os.getenv("FULUA_DATABASE_PATH") or data_root / "data" / "app.db")
    storage_path = Path(os.getenv("FULUA_STORAGE_PATH") or data_root / "storage")
    if mode == "development":
        database_path = Path(os.getenv("FULUA_DATABASE_PATH") or repository_root / "backend" / "data" / "app.db")
        storage_path = Path(os.getenv("FULUA_STORAGE_PATH") or repository_root / "storage")

    return RuntimePaths(
        data_root=data_root,
        database_path=database_path,
        storage_path=storage_path,
        log_path=data_root / "logs",
        backup_path=data_root / "backups",
        migration_path=data_root / "migration",
        mode=mode,
    )


def ensure_runtime_directories(paths: RuntimePaths | None = None) -> RuntimePaths:
    resolved = paths or resolve_runtime_paths()
    for directory in (
        resolved.database_path.parent,
        resolved.storage_path,
        resolved.log_path,
        resolved.backup_path,
        resolved.migration_path,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return resolved
