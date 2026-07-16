"""Security boundary for R5 managed PNG/JPEG evidence files."""

from __future__ import annotations

import hashlib
import io
import os
import re
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError

from ..config import settings
from .report_domain.errors import ReportDomainError


MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_DIMENSION = 20_000
READ_CHUNK = 1024 * 1024

_EXTENSIONS = {".png": ("PNG", "image/png"), ".jpg": ("JPEG", "image/jpeg"), ".jpeg": ("JPEG", "image/jpeg")}
_MIME_TYPES = {"image/png", "image/jpeg", "image/jpg"}
_TOMBSTONE_PATTERN = re.compile(r"^\.r5-delete-[0-9a-f]{32}-(?P<original>[0-9a-f]{32}\.(?:png|jpeg))$")


@dataclass(frozen=True)
class ManagedFileTombstone:
    original: Path
    tombstone: Path


def _project_root(project_uuid: str) -> Path:
    return settings.storage_path / "report_evidence" / project_uuid


def _safe_project_root(project_uuid: str, *, create: bool) -> Path:
    storage = settings.storage_path.resolve()
    evidence_root = storage / "report_evidence"
    if create:
        evidence_root.mkdir(parents=True, exist_ok=True)
    if evidence_root.resolve() != evidence_root:
        raise ReportDomainError(
            "APPENDIX_B_FILE_PATH_INVALID",
            "证据图片根目录包含重解析点。",
            status_code=500,
            field="file_path",
        )
    expected = evidence_root / project_uuid
    if create:
        expected.mkdir(exist_ok=True)
    resolved = expected.resolve()
    # Reject a project directory (or report_evidence parent) redirected through
    # a symlink/junction, even when the target still happens to be under storage.
    # Otherwise project A could be redirected into project B or outside data root
    # before the first temporary file is opened.
    if resolved != expected:
        raise ReportDomainError(
            "APPENDIX_B_FILE_PATH_INVALID",
            "证据图片目录不属于当前项目或包含重解析点。",
            status_code=500,
            field="file_path",
        )
    return resolved


def _normalized_original_name(value: str | None, fallback: str) -> str:
    name = Path(value or "").name
    name = re.sub(r"[\x00-\x1f\x7f]+", "", name).strip()
    return (name or fallback)[:240]


def _read_bounded(stream: BinaryIO) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = stream.read(READ_CHUNK)
        if not chunk:
            break
        size += len(chunk)
        if size > MAX_IMAGE_BYTES:
            raise ReportDomainError(
                "APPENDIX_B_IMAGE_TOO_LARGE",
                "图片超过 20 MiB 限制。",
                status_code=400,
                field="file",
            )
        chunks.append(chunk)
    if not chunks:
        raise ReportDomainError(
            "APPENDIX_B_IMAGE_EMPTY", "图片文件为空。", status_code=400, field="file"
        )
    return b"".join(chunks)


def _signature_format(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    return None


def inspect_image_bytes(data: bytes) -> dict[str, Any]:
    signature = _signature_format(data)
    if signature is None:
        raise ReportDomainError(
            "APPENDIX_B_IMAGE_SIGNATURE_INVALID",
            "文件签名不是 PNG 或 JPEG。",
            status_code=400,
            field="file",
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                actual_format = str(image.format or "").upper()
                pixel_width, pixel_height = image.size
                dpi = image.info.get("dpi") or (None, None)
                pixels = int(pixel_width) * int(pixel_height)
                if (
                    pixels > MAX_IMAGE_PIXELS
                    or pixel_width > MAX_IMAGE_DIMENSION
                    or pixel_height > MAX_IMAGE_DIMENSION
                ):
                    raise ReportDomainError(
                        "APPENDIX_B_IMAGE_PIXEL_LIMIT",
                        "图片像素总量或边长超过安全限制。",
                        status_code=400,
                        field="file",
                    )
                image.verify()
            with Image.open(io.BytesIO(data)) as decoded:
                decoded.load()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ReportDomainError(
            "APPENDIX_B_IMAGE_CORRUPT",
            "图片无法完整解码或像素规模不安全。",
            status_code=400,
            field="file",
        ) from exc
    if actual_format != signature:
        raise ReportDomainError(
            "APPENDIX_B_IMAGE_FORMAT_MISMATCH",
            "图片文件签名与实际格式不一致。",
            status_code=400,
            field="file",
        )
    dpi_x = _dpi(dpi[0] if isinstance(dpi, (tuple, list)) and dpi else None)
    dpi_y = _dpi(dpi[1] if isinstance(dpi, (tuple, list)) and len(dpi) > 1 else None)
    effective_x = dpi_x or 144.0
    effective_y = dpi_y or effective_x
    natural_width = pixel_width / effective_x
    natural_height = pixel_height / effective_y
    display_width = min(natural_width, 6.5)
    scale = display_width / natural_width if natural_width else 1.0
    return {
        "format": actual_format,
        "mime_type": "image/png" if actual_format == "PNG" else "image/jpeg",
        "pixel_width": int(pixel_width),
        "pixel_height": int(pixel_height),
        "dpi_x": dpi_x,
        "dpi_y": dpi_y,
        "display_width_in": round(display_width, 2),
        "display_height_in": round(natural_height * scale, 2),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def save_upload(project_uuid: str, upload: UploadFile) -> dict[str, Any]:
    extension = Path(upload.filename or "").suffix.lower()
    declared_type = str(upload.content_type or "").lower()
    expected = _EXTENSIONS.get(extension)
    if expected is None or declared_type not in _MIME_TYPES:
        raise ReportDomainError(
            "APPENDIX_B_IMAGE_TYPE_INVALID",
            "仅允许上传 PNG 或 JPEG 图片。",
            status_code=400,
            field="file",
        )
    data = _read_bounded(upload.file)
    metadata = inspect_image_bytes(data)
    if metadata["format"] != expected[0] or metadata["mime_type"] != ("image/jpeg" if declared_type == "image/jpg" else declared_type):
        raise ReportDomainError(
            "APPENDIX_B_IMAGE_TYPE_MISMATCH",
            "图片扩展名、MIME 和实际格式不一致。",
            status_code=400,
            field="file",
        )
    normalized_extension = ".png" if metadata["format"] == "PNG" else ".jpeg"
    root = _safe_project_root(project_uuid, create=True)
    filename = f"{uuid.uuid4().hex}{normalized_extension}"
    destination = root / filename
    temporary = root / f".{filename}.tmp"
    try:
        with temporary.open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise
    relative = destination.resolve().relative_to(settings.storage_path.resolve()).as_posix()
    return {
        "file_path": relative,
        "original_name": _normalized_original_name(upload.filename, filename),
        **metadata,
    }


def resolve_managed_path(project_uuid: str, relative_path: str, *, must_exist: bool = True) -> Path:
    storage = settings.storage_path.resolve()
    expected_root = _safe_project_root(project_uuid, create=False)
    unresolved = storage / relative_path
    absolute = Path(os.path.abspath(unresolved))
    candidate = unresolved.resolve()
    if candidate != absolute:
        raise ReportDomainError(
            "APPENDIX_B_FILE_PATH_INVALID",
            "证据图片路径包含重解析点或不安全的路径跳转。",
            status_code=500,
            field="file_path",
        )
    try:
        candidate.relative_to(expected_root)
    except ValueError as exc:
        raise ReportDomainError(
            "APPENDIX_B_FILE_PATH_INVALID",
            "证据图片路径不属于当前项目。",
            status_code=500,
            field="file_path",
        ) from exc
    if must_exist and (not candidate.is_file() or candidate.is_symlink()):
        raise ReportDomainError(
            "APPENDIX_B_FILE_MISSING_OR_CORRUPT",
            "证据图片不存在或路径不安全。",
            status_code=500,
            field="file_path",
        )
    return candidate


def verify_stored_image(project_uuid: str, item: dict[str, Any]) -> None:
    path = resolve_managed_path(project_uuid, str(item.get("file_path") or ""))
    try:
        data = path.read_bytes()
        metadata = inspect_image_bytes(data)
    except (OSError, ReportDomainError) as exc:
        if isinstance(exc, ReportDomainError) and exc.code == "APPENDIX_B_FILE_PATH_INVALID":
            raise
        raise ReportDomainError(
            "APPENDIX_B_FILE_MISSING_OR_CORRUPT",
            "证据图片缺失、损坏或无法读取。",
            status_code=500,
            entity_uuid=str(item.get("item_uuid") or ""),
            field="file_path",
        ) from exc
    if metadata["sha256"] != item.get("sha256"):
        raise ReportDomainError(
            "APPENDIX_B_FILE_HASH_MISMATCH",
            "证据图片哈希与保存记录不一致。",
            status_code=500,
            entity_uuid=str(item.get("item_uuid") or ""),
            field="sha256",
        )
    if metadata["mime_type"] != item.get("mime_type"):
        raise ReportDomainError(
            "APPENDIX_B_FILE_FORMAT_MISMATCH",
            "证据图片实际格式与保存记录不一致。",
            status_code=500,
            entity_uuid=str(item.get("item_uuid") or ""),
            field="mime_type",
        )


def remove_managed_file(project_uuid: str, relative_path: str) -> None:
    path = resolve_managed_path(project_uuid, relative_path, must_exist=False)
    path.unlink(missing_ok=True)


def stage_managed_file_removal(
    project_uuid: str,
    relative_path: str,
    *,
    missing_ok: bool = False,
) -> ManagedFileTombstone | None:
    """Atomically hide a managed file until its database mutation commits."""

    path = resolve_managed_path(project_uuid, relative_path, must_exist=not missing_ok)
    if not path.exists():
        return None
    tombstone = path.with_name(f".r5-delete-{uuid.uuid4().hex}-{path.name}")
    os.replace(path, tombstone)
    return ManagedFileTombstone(original=path, tombstone=tombstone)


def restore_managed_tombstone(staged: ManagedFileTombstone | None) -> None:
    if staged is None or not staged.tombstone.exists():
        return
    if staged.original.exists():
        raise ReportDomainError(
            "APPENDIX_B_FILE_RESTORE_CONFLICT",
            "证据图片回滚时发现目标文件已存在。",
            status_code=500,
            field="file_path",
        )
    os.replace(staged.tombstone, staged.original)


def finalize_managed_tombstone(staged: ManagedFileTombstone | None) -> None:
    if staged is not None:
        staged.tombstone.unlink(missing_ok=True)


def discard_managed_file(project_uuid: str, relative_path: str) -> None:
    """Remove an unreferenced file without exposing it after a partial failure."""

    staged = stage_managed_file_removal(project_uuid, relative_path, missing_ok=True)
    finalize_managed_tombstone(staged)


def reconcile_managed_tombstones(
    project_uuid: str,
    referenced_relative_paths: set[str],
) -> None:
    """Recover interrupted file/SQLite compensation before reads or exports."""

    root = _safe_project_root(project_uuid, create=False)
    if not root.exists():
        return
    storage = settings.storage_path.resolve()
    try:
        for tombstone in root.glob(".r5-delete-*"):
            match = _TOMBSTONE_PATTERN.fullmatch(tombstone.name)
            if match is None or tombstone.is_symlink():
                raise ReportDomainError(
                    "APPENDIX_B_FILE_PATH_INVALID",
                    "证据图片目录包含无法识别的清理暂存文件。",
                    status_code=500,
                    field="file_path",
                )
            original = root / match.group("original")
            relative = original.relative_to(storage).as_posix()
            if relative in referenced_relative_paths and not original.exists():
                os.replace(tombstone, original)
            else:
                tombstone.unlink(missing_ok=True)
    except OSError as exc:
        raise ReportDomainError(
            "APPENDIX_B_FILE_RECONCILIATION_FAILED",
            "证据图片中断恢复或残留清理失败。",
            status_code=500,
            field="file_path",
        ) from exc


def _dpi(value: object) -> float | None:
    if not isinstance(value, (int, float)) or value <= 1 or value > 9600:
        return None
    return round(float(value), 2)
