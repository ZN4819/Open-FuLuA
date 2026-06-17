from __future__ import annotations

import shutil
from pathlib import Path

from ..config import settings


def remove_project_runtime_files(project_id: int) -> None:
    for relative_path in (
        Path("uploads") / str(project_id),
        Path("exports") / str(project_id),
        Path("previews") / str(project_id),
        Path("projects") / str(project_id),
    ):
        _remove_storage_child(relative_path)


def _remove_storage_child(relative_path: Path) -> None:
    storage_root = settings.storage_path.resolve()
    target = (settings.storage_path / relative_path).resolve()
    if not target.exists() or not _is_relative_to(target, storage_root):
        return
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
