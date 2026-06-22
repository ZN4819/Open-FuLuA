from __future__ import annotations

import posixpath
import re
import zipfile
from collections import defaultdict
from dataclasses import replace
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from ...config import settings
from ..evidence import inspect_image
from ..template_profile import load_template_profile
from .models import (
    DocxImportAssessmentRowModel,
    DocxImportCrossReferenceModel,
    DocxImportEvidenceImageModel,
    DocxImportIssueModel,
    DocxImportParsedProject,
    DocxImportParsedSectionModel,
)
from .package import read_docx_package
from .tables import parse_docx_core_tables


NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
W = f"{{{NS['w']}}}"
M = f"{{{NS['m']}}}"
A = f"{{{NS['a']}}}"
R = f"{{{NS['r']}}}"

FIGURE_LABEL_RE = re.compile(r"图\s*A\s*-\s*([1-8])\s*-\s*(\d+)")
TABLE_CAPTION_RE = re.compile(r"表\s*A\s*-\s*([1-8])")
SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}


def parse_docx_images_and_references(path: str | Path, import_dir: str | Path) -> DocxImportParsedProject:
    """解析证据图片、题注和结果记录中的图号引用。"""
    parsed = parse_docx_core_tables(path)
    package = read_docx_package(path)
    profile = load_template_profile()
    issues = list(parsed.issues)
    output_dir = Path(import_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    images = _extract_images(package.path, package.document, package.relationships, output_dir, profile, issues)
    images_by_section: dict[str, list[DocxImportEvidenceImageModel]] = defaultdict(list)
    images_by_key: dict[str, DocxImportEvidenceImageModel] = {}
    for image in images:
        images_by_section[image.section_code].append(image)
        images_by_key[_label_key(image.figure_label)] = image

    parsed_sections: list[DocxImportParsedSectionModel] = []
    total_references = 0
    for section in parsed.sections:
        rows: list[DocxImportAssessmentRowModel] = []
        section_references = 0
        for row in section.rows:
            replaced_row = _replace_row_references(row, images_by_key, issues)
            section_references += len(replaced_row.cross_references)
            rows.append(replaced_row)
        section_images = sorted(images_by_section.get(section.code, []), key=lambda item: (item.sort_order, item.figure_label))
        total_references += section_references
        parsed_sections.append(
            replace(
                section,
                rows=rows,
                images=section_images,
                image_count=len(section_images),
                reference_count=section_references,
            )
        )

    summary = dict(parsed.summary)
    summary["images"] = len(images)
    summary["references"] = total_references
    summary["errors"] = sum(1 for issue in issues if issue.severity == "error")
    summary["warnings"] = sum(1 for issue in issues if issue.severity == "warning")
    summary["info"] = sum(1 for issue in issues if issue.severity == "info")

    return DocxImportParsedProject(
        suggested_project_name=parsed.suggested_project_name,
        sections=parsed_sections,
        issues=issues,
        summary=summary,
    )


def _extract_images(
    docx_path: Path,
    document: ET.Element,
    relationships: dict[str, str],
    output_dir: Path,
    profile: dict,
    issues: list[DocxImportIssueModel],
) -> list[DocxImportEvidenceImageModel]:
    body = document.find("w:body", NS)
    if body is None:
        return []

    children = list(body)
    current_section_code = "A-1"
    images: list[DocxImportEvidenceImageModel] = []
    used_labels: set[str] = set()
    section_counts: dict[str, int] = defaultdict(int)

    with zipfile.ZipFile(docx_path) as package:
        for index, child in enumerate(children):
            if child.tag != W + "p":
                continue

            paragraph_text = _clean_inline_text(_visible_text(child))
            current_section_code = _update_current_section(current_section_code, paragraph_text, profile)
            rel_ids = _drawing_relationship_ids(child)
            if not rel_ids:
                continue

            caption_text = _find_following_caption(children, index + 1)
            caption_match = FIGURE_LABEL_RE.search(caption_text or "")
            caption_section_code = f"A-{caption_match.group(1)}" if caption_match else current_section_code
            caption_order = int(caption_match.group(2)) if caption_match else None
            caption = _caption_suffix(caption_text, caption_match)

            for offset, rel_id in enumerate(rel_ids, start=1):
                target = relationships.get(rel_id)
                source_media_path = _resolve_media_path(target or "")
                extension = Path(source_media_path).suffix.lower()
                if extension not in SUPPORTED_IMAGE_EXTENSIONS:
                    issues.append(
                        DocxImportIssueModel(
                            severity="warning",
                            code="IMPORT_IMAGE_UNSUPPORTED_FORMAT",
                            message=f"图片关系 {rel_id} 指向的格式不支持：{source_media_path or target or '未知'}。",
                            section_code=caption_section_code,
                            target=f"relationship:{rel_id}",
                        )
                    )
                    continue

                section_code = caption_section_code
                if caption_order is not None and offset == 1:
                    sort_order = caption_order
                else:
                    section_counts[section_code] += 1
                    sort_order = section_counts[section_code]
                section_counts[section_code] = max(section_counts[section_code], sort_order)

                figure_label = f"图{section_code}-{sort_order}"
                if caption_match:
                    figure_label = f"图A-{caption_match.group(1)}-{caption_match.group(2)}"
                if not caption_match:
                    issues.append(
                        DocxImportIssueModel(
                            severity="warning",
                            code="IMPORT_IMAGE_CAPTION_MISSING",
                            message=f"{section_code} 第 {sort_order} 张图片未识别到图题注。",
                            section_code=section_code,
                            target=f"relationship:{rel_id}",
                        )
                    )

                label_key = _label_key(figure_label)
                if label_key in used_labels:
                    issues.append(
                        DocxImportIssueModel(
                            severity="warning",
                            code="IMPORT_IMAGE_CAPTION_DUPLICATE",
                            message=f"图题注重复：{figure_label}。",
                            section_code=section_code,
                            target=f"relationship:{rel_id}",
                        )
                    )
                used_labels.add(label_key)

                copied_path = _copy_media(package, source_media_path, output_dir, label_key, extension, issues, section_code, rel_id)
                if copied_path is None:
                    continue
                metadata = _inspect_copied_image(copied_path, issues, section_code, rel_id)
                if metadata is None:
                    continue

                images.append(
                    DocxImportEvidenceImageModel(
                        section_code=section_code,
                        figure_label=figure_label,
                        caption=caption,
                        sort_order=sort_order,
                        file_path=_display_file_path(copied_path),
                        original_name=Path(source_media_path).name,
                        source_media_path=source_media_path,
                        relationship_id=rel_id,
                        pixel_width=_int_or_none(metadata.get("pixel_width")),
                        pixel_height=_int_or_none(metadata.get("pixel_height")),
                        dpi_x=_float_or_none(metadata.get("dpi_x")),
                        dpi_y=_float_or_none(metadata.get("dpi_y")),
                        display_width_in=_float_or_none(metadata.get("display_width_in")),
                        display_height_in=_float_or_none(metadata.get("display_height_in")),
                    )
                )
    return images


def _drawing_relationship_ids(paragraph: ET.Element) -> list[str]:
    rel_ids: list[str] = []
    for blip in paragraph.findall(".//a:blip", NS):
        rel_id = blip.get(R + "embed")
        if rel_id:
            rel_ids.append(rel_id)
    return rel_ids


def _find_following_caption(children: list[ET.Element], start_index: int) -> str:
    for child in children[start_index:start_index + 4]:
        if child.tag != W + "p":
            continue
        text = _clean_inline_text(_visible_text(child))
        if not text:
            continue
        if FIGURE_LABEL_RE.search(text):
            return text
        if _drawing_relationship_ids(child):
            return ""
    return ""


def _replace_row_references(
    row: DocxImportAssessmentRowModel,
    images_by_key: dict[str, DocxImportEvidenceImageModel],
    issues: list[DocxImportIssueModel],
) -> DocxImportAssessmentRowModel:
    cross_references: list[DocxImportCrossReferenceModel] = []

    def replace_match(match: re.Match[str]) -> str:
        label = f"图A-{match.group(1)}-{match.group(2)}"
        key = _label_key(label)
        image = images_by_key.get(key)
        if image is None:
            issues.append(
                DocxImportIssueModel(
                    severity="warning",
                    code="IMPORT_REFERENCE_TARGET_MISSING",
                    message=f"{row.section_code} 第 {row.sort_order} 行引用的{label}未找到对应图片。",
                    section_code=row.section_code,
                    target=f"row:{row.sort_order}:figure:{key}",
                )
            )
            return match.group(0)
        token = f"[[FIG:import:{key}]]"
        cross_references.append(
            DocxImportCrossReferenceModel(
                token=token,
                display_text=label,
                target_figure_label=image.figure_label,
                target_image_key=key,
            )
        )
        return token

    record_text = FIGURE_LABEL_RE.sub(replace_match, row.record_text or "")
    return replace(row, record_text=record_text, cross_references=cross_references)


def _visible_text(element: ET.Element) -> str:
    parts: list[str] = []
    for child in element.iter():
        if child.tag in {W + "t", M + "t"}:
            parts.append(child.text or "")
        elif child.tag == W + "tab":
            parts.append("\t")
        elif child.tag in {W + "br", W + "cr"}:
            parts.append("\n")
    return "".join(parts)


def _clean_inline_text(text: str) -> str:
    return " ".join((text or "").replace("\u00a0", " ").replace("\u3000", " ").split())


def _update_current_section(current_section_code: str, text: str, profile: dict) -> str:
    table_match = TABLE_CAPTION_RE.search(text or "")
    if table_match:
        return f"A-{table_match.group(1)}"
    for section in profile["sections"]:
        if text == section["title"] or text.endswith(section["title"]):
            return section["code"]
    return current_section_code


def _caption_suffix(caption_text: str, match: re.Match[str] | None) -> str:
    if not caption_text or match is None:
        return ""
    return caption_text[match.end():].strip()


def _resolve_media_path(target: str) -> str:
    if not target:
        return ""
    target_path = PurePosixPath(target)
    if target.startswith("/"):
        normalized = posixpath.normpath(target.lstrip("/"))
    elif target.startswith("word/"):
        normalized = posixpath.normpath(target)
    else:
        normalized = posixpath.normpath(str(PurePosixPath("word") / target_path))
    return normalized.replace("\\", "/")


def _copy_media(
    package: zipfile.ZipFile,
    source_media_path: str,
    output_dir: Path,
    label_key: str,
    extension: str,
    issues: list[DocxImportIssueModel],
    section_code: str,
    rel_id: str,
) -> Path | None:
    try:
        data = package.read(source_media_path)
    except KeyError:
        issues.append(
            DocxImportIssueModel(
                severity="warning",
                code="IMPORT_IMAGE_UNSUPPORTED_FORMAT",
                message=f"图片文件未在 DOCX 包中找到：{source_media_path}。",
                section_code=section_code,
                target=f"relationship:{rel_id}",
            )
        )
        return None

    image_dir = output_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    destination = image_dir / f"{label_key}{'.jpeg' if extension == '.jpg' else extension}"
    counter = 2
    while destination.exists():
        destination = image_dir / f"{label_key}-{counter}{'.jpeg' if extension == '.jpg' else extension}"
        counter += 1
    destination.write_bytes(data)
    return destination


def _inspect_copied_image(
    image_path: Path,
    issues: list[DocxImportIssueModel],
    section_code: str,
    rel_id: str,
) -> dict[str, object] | None:
    try:
        return inspect_image(image_path)
    except Exception:  # noqa: BLE001
        image_path.unlink(missing_ok=True)
        issues.append(
            DocxImportIssueModel(
                severity="warning",
                code="IMPORT_IMAGE_UNSUPPORTED_FORMAT",
                message=f"图片无法读取或文件已损坏：{image_path.name}。",
                section_code=section_code,
                target=f"relationship:{rel_id}",
            )
        )
        return None


def _display_file_path(path: Path) -> str:
    try:
        return path.relative_to(settings.storage_path).as_posix()
    except ValueError:
        return path.as_posix()


def _label_key(label: str) -> str:
    match = FIGURE_LABEL_RE.search(label or "")
    if not match:
        return ""
    return f"A-{match.group(1)}-{match.group(2)}"


def _int_or_none(value: object) -> int | None:
    return int(value) if isinstance(value, int) else None


def _float_or_none(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None