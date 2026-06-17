from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .. import database
from ..config import settings
from ..schemas import RenderJobRead
from .docx_generator import DocxGenerationError, generate_project_docx


PDF_PAGE_RE = re.compile(rb"/Type\s*/Page\b")
DEFAULT_RENDER_TIMEOUT_SECONDS = 120


class PreviewRenderError(RuntimeError):
    """预览渲染失败。"""


def create_preview_job(project_id: int, mode: str = "final") -> RenderJobRead:
    row = database.create_render_job(project_id, mode)
    return render_job_to_schema(row)


def get_preview_job(job_id: int) -> RenderJobRead | None:
    row = database.get_render_job(job_id)
    return render_job_to_schema(row) if row is not None else None


def process_preview_job(job_id: int, timeout_seconds: int = DEFAULT_RENDER_TIMEOUT_SECONDS) -> None:
    row = database.get_render_job(job_id)
    if row is None:
        return

    project_id = int(row["project_id"])
    preview_dir = settings.storage_path / "previews" / str(project_id) / str(job_id)
    preview_dir.mkdir(parents=True, exist_ok=True)
    log_path = preview_dir / "render.log"
    log_lines: list[str] = []

    database.update_render_job(
        job_id,
        {
            "status": "running",
            "started_at": database.utc_now(),
            "log_path": _relative_to_storage(log_path),
        },
    )

    try:
        docx_path = generate_project_docx(project_id, row["mode"])
        log_lines.append(f"DOCX 已生成：{docx_path}")
        pdf_path = _render_pdf(docx_path, preview_dir, log_lines, timeout_seconds)
        page_count = count_pdf_pages(pdf_path)
        log_lines.append(f"PDF 已生成：{pdf_path}")
        log_lines.append(f"页数：{page_count}")
        _write_log(log_path, log_lines)
        database.update_render_job(
            job_id,
            {
                "status": "succeeded",
                "finished_at": database.utc_now(),
                "output_docx_path": _relative_to_storage(docx_path),
                "output_pdf_path": _relative_to_storage(pdf_path),
                "page_count": page_count,
                "log_path": _relative_to_storage(log_path),
                "error_message": None,
            },
        )
    except subprocess.TimeoutExpired as exc:
        log_lines.append(f"预览渲染超时：{exc}")
        _write_log(log_path, log_lines)
        database.update_render_job(
            job_id,
            {
                "status": "timeout",
                "finished_at": database.utc_now(),
                "log_path": _relative_to_storage(log_path),
                "error_message": "预览渲染超时。",
            },
        )
    except (PreviewRenderError, DocxGenerationError, OSError) as exc:
        log_lines.append(f"预览渲染失败：{exc}")
        _write_log(log_path, log_lines)
        database.update_render_job(
            job_id,
            {
                "status": "failed",
                "finished_at": database.utc_now(),
                "log_path": _relative_to_storage(log_path),
                "error_message": str(exc),
            },
        )


def render_job_to_schema(row: Any) -> RenderJobRead:
    raw = dict(row)
    docx_path = raw.get("output_docx_path")
    pdf_path = raw.get("output_pdf_path")
    log_path = raw.get("log_path")
    return RenderJobRead(
        id=raw["id"],
        project_id=raw["project_id"],
        status=raw["status"],
        mode=raw["mode"],
        created_at=raw["created_at"],
        started_at=raw["started_at"],
        finished_at=raw["finished_at"],
        output_docx_path=docx_path,
        output_pdf_path=pdf_path,
        output_docx_url=_file_url(docx_path),
        output_pdf_url=_file_url(pdf_path),
        page_count=raw["page_count"],
        log_path=log_path,
        log_url=_file_url(log_path),
        error_message=raw["error_message"],
    )


def count_pdf_pages(path: Path) -> int:
    data = path.read_bytes()
    return len(PDF_PAGE_RE.findall(data))


def _render_pdf(docx_path: Path, preview_dir: Path, log_lines: list[str], timeout_seconds: int) -> Path:
    soffice = _find_soffice()
    if soffice:
        log_lines.append(f"使用 LibreOffice 渲染器：{soffice}")
        return _render_with_libreoffice(soffice, docx_path, preview_dir, log_lines, timeout_seconds)

    word_pdf = _render_with_word_if_available(docx_path, preview_dir, log_lines)
    if word_pdf is not None:
        return word_pdf

    raise PreviewRenderError("未找到可用的 Word 或 LibreOffice 渲染器，无法生成 PDF 预览。")


def _find_soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        path = shutil.which(name)
        if path:
            return path
    for candidate in (
        Path("C:/Program Files/LibreOffice/program/soffice.exe"),
        Path("C:/Program Files (x86)/LibreOffice/program/soffice.exe"),
    ):
        if candidate.exists():
            return str(candidate)
    return None


def _render_with_libreoffice(
    soffice: str,
    docx_path: Path,
    preview_dir: Path,
    log_lines: list[str],
    timeout_seconds: int,
) -> Path:
    profile_dir = preview_dir / "lo_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    command = [
        soffice,
        "--headless",
        f"-env:UserInstallation={profile_dir.as_uri()}",
        "--convert-to",
        "pdf",
        "--outdir",
        str(preview_dir),
        str(docx_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds, check=False)
    if result.stdout:
        log_lines.append(result.stdout.strip())
    if result.stderr:
        log_lines.append(result.stderr.strip())
    if result.returncode != 0:
        raise PreviewRenderError(f"LibreOffice 导出失败，退出码 {result.returncode}。")

    pdf_path = preview_dir / f"{docx_path.stem}.pdf"
    if not pdf_path.exists():
        raise PreviewRenderError("LibreOffice 未生成 PDF 文件。")
    return pdf_path


def _render_with_word_if_available(docx_path: Path, preview_dir: Path, log_lines: list[str]) -> Path | None:
    try:
        import win32com.client  # type: ignore[import-not-found]
    except ImportError:
        log_lines.append("未安装 pywin32，跳过 Microsoft Word 自动化渲染。")
        return None

    pdf_path = preview_dir / f"{docx_path.stem}.pdf"
    word = None
    document = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        document = word.Documents.Open(str(docx_path))
        document.SaveAs(str(pdf_path), FileFormat=17)
    except Exception as exc:  # noqa: BLE001
        log_lines.append(f"Microsoft Word 自动化渲染失败：{exc}")
        return None
    finally:
        if document is not None:
            document.Close(False)
        if word is not None:
            word.Quit()

    return pdf_path if pdf_path.exists() else None


def _write_log(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _relative_to_storage(path: Path) -> str:
    return path.resolve().relative_to(settings.storage_path.resolve()).as_posix()


def _file_url(path: str | None) -> str | None:
    if not path:
        return None
    return f"/api/files/{path}"
