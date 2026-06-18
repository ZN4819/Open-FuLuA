from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

from fastapi import UploadFile
from PIL import Image

from .template_profile import load_template_profile
from ..config import settings


ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg"}
ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg"}


class EvidenceImageError(RuntimeError):
    """证据图片处理失败。"""


def save_upload_file(project_id: int, section_code: str, upload: UploadFile) -> dict[str, object]:
    extension = Path(upload.filename or "").suffix.lower()
    if extension == ".jpg":
        extension = ".jpeg"

    if extension not in ALLOWED_EXTENSIONS or upload.content_type not in ALLOWED_CONTENT_TYPES:
        raise EvidenceImageError("仅支持 PNG/JPEG 图片。")

    safe_section = re.sub(r"[^A-Za-z0-9_-]+", "-", section_code)
    relative_dir = Path("uploads") / str(project_id) / safe_section
    absolute_dir = settings.storage_path / relative_dir
    absolute_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid.uuid4().hex}{extension}"
    relative_path = relative_dir / filename
    absolute_path = settings.storage_path / relative_path

    with absolute_path.open("wb") as output_file:
        shutil.copyfileobj(upload.file, output_file)

    try:
        metadata = inspect_image(absolute_path)
    except Exception as exc:  # noqa: BLE001
        absolute_path.unlink(missing_ok=True)
        raise EvidenceImageError("图片无法读取或文件已损坏。") from exc

    return {
        "file_path": relative_path.as_posix(),
        "original_name": upload.filename or filename,
        **metadata,
    }


def inspect_image(path: Path) -> dict[str, object]:
    profile = load_template_profile()
    max_width = float(profile["images"]["max_width_in"])

    with Image.open(path) as image:
        pixel_width, pixel_height = image.size
        dpi = image.info.get("dpi") or (None, None)
        dpi_x = _normalize_dpi(dpi[0] if len(dpi) > 0 else None)
        dpi_y = _normalize_dpi(dpi[1] if len(dpi) > 1 else None)

    effective_dpi_x = dpi_x or 144.0
    effective_dpi_y = dpi_y or effective_dpi_x
    natural_width = pixel_width / effective_dpi_x
    natural_height = pixel_height / effective_dpi_y
    display_width = min(natural_width, max_width)
    scale = display_width / natural_width if natural_width else 1
    display_height = natural_height * scale

    return {
        "pixel_width": pixel_width,
        "pixel_height": pixel_height,
        "dpi_x": dpi_x,
        "dpi_y": dpi_y,
        "display_width_in": round(display_width, 2),
        "display_height_in": round(display_height, 2),
    }


def image_warnings(image: dict[str, object]) -> list[str]:
    profile = load_template_profile()
    threshold = float(profile["images"]["dpi_warning_threshold"])
    warnings: list[str] = []

    dpi_x = image.get("dpi_x")
    dpi_y = image.get("dpi_y")
    if (isinstance(dpi_x, (int, float)) and dpi_x < threshold) or (
        isinstance(dpi_y, (int, float)) and dpi_y < threshold
    ):
        warnings.append(f"DPI 低于 {int(threshold)}，导出后可能不清晰。")

    max_width = float(profile["images"]["max_width_in"])
    pixel_width = image.get("pixel_width")
    effective_dpi_x = dpi_x if isinstance(dpi_x, (int, float)) and dpi_x > 1 else 144.0
    if isinstance(pixel_width, (int, float)) and pixel_width / effective_dpi_x > max_width:
        warnings.append("图片原始宽度超过页面可用宽度，导出时将自动缩放。")

    return warnings


def remove_stored_file(relative_path: str) -> None:
    path = settings.storage_path / relative_path
    path.unlink(missing_ok=True)


def _normalize_dpi(value: object) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    if value <= 1:
        return None
    return round(float(value), 2)
