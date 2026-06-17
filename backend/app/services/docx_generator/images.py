from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches

from ...config import settings
from .fields import BookmarkWriter, add_complex_field
from .styles import apply_run_font, set_paragraph_format


def build_figure_refs(section_code: str, images: list[Any]) -> dict[int, dict[str, str]]:
    refs: dict[int, dict[str, str]] = {}
    for index, image in enumerate(images, start=1):
        image_id = int(image["id"])
        refs[image_id] = {
            "bookmark": f"fig_{image_id}",
            "label": f"图{section_code}-{index}",
            "sequence": str(index),
        }
    return refs


def add_section_images(
    document: Document,
    section_code: str,
    images: list[Any],
    profile: dict[str, Any],
    bookmark_writer: BookmarkWriter,
    figure_refs: dict[int, dict[str, str]],
) -> None:
    if not images:
        return

    for image in images:
        image_id = int(image["id"])
        ref = figure_refs[image_id]
        image_path = settings.storage_path / image["file_path"]

        if not image_path.exists():
            paragraph = document.add_paragraph()
            set_paragraph_format(paragraph, profile, "caption")
            run = paragraph.add_run(f"[图片文件缺失：{image['original_name']}]")
            apply_run_font(run, profile, "caption")
            continue

        paragraph = document.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = paragraph.add_run()
        width = _display_width(image, profile)
        inline_shape = run.add_picture(str(image_path), width=Inches(width))
        _set_image_alt_text(inline_shape, image["alt_text"] or image["caption"] or image["original_name"])

        caption = document.add_paragraph()
        set_paragraph_format(caption, profile, "caption")
        caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
        bookmark_id = bookmark_writer.start(caption, ref["bookmark"])
        prefix_run = caption.add_run(f"图{section_code}-")
        apply_run_font(prefix_run, profile, "caption")
        add_complex_field(caption, f"SEQ AppendixFigure_{section_code.replace('-', '_')}", ref["sequence"])
        bookmark_writer.end(caption, bookmark_id)

        caption_text = image["caption"].strip()
        if caption_text:
            suffix_run = caption.add_run(f" {caption_text}")
            apply_run_font(suffix_run, profile, "caption")


def _display_width(image: Any, profile: dict[str, Any]) -> float:
    configured_max = float(profile["images"]["max_width_in"])
    display_width = image["display_width_in"]
    if isinstance(display_width, (int, float)) and display_width > 0:
        return min(float(display_width), configured_max)
    return min(configured_max, 6.0)


def _set_image_alt_text(inline_shape, alt_text: str) -> None:
    doc_pr = inline_shape._inline.docPr
    doc_pr.set("descr", alt_text)
    doc_pr.set("title", alt_text)
