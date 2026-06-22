from __future__ import annotations

import shutil
from pathlib import Path

from ...config import settings


IMPORTS_DIR = "imports"


def import_job_dir(job_id: int) -> Path:
    return settings.storage_path / IMPORTS_DIR / str(job_id)


def ensure_import_job_dir(job_id: int) -> Path:
    path = import_job_dir(job_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def source_docx_path(job_id: int) -> Path:
    return import_job_dir(job_id) / "source.docx"


def parsed_json_path(job_id: int) -> Path:
    return import_job_dir(job_id) / "parsed.json"


def remove_import_job_dir(job_id: int) -> None:
    storage_root = settings.storage_path.resolve()
    target = import_job_dir(job_id).resolve()
    if not target.exists() or not _is_relative_to(target, storage_root):
        return
    shutil.rmtree(target)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
