from __future__ import annotations

import re
from typing import Any

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.table import _Cell, Table

from .content_controls import wrap_cell_paragraph_with_dropdown
from .fields import add_complex_field
from .styles import (
    apply_run_font,
    configure_table_geometry,
    set_cell_margins,
    set_cell_text,
    set_paragraph_format,
    shade_cell,
)


FIG_TOKEN_RE = re.compile(r"\[\[FIG:(\d+)\]\]")


def add_assessment_table(
    document: Document,
    section: Any,
    rows: list[Any],
    profile: dict[str, Any],
    mode: str,
    figure_refs: dict[int, dict[str, str]],
) -> Table:
    section_profile = _section_profile(profile, section["code"])
    table_profile = profile["tables"][section_profile["table_type"]]
    columns = table_profile["columns"]
    output_rows = rows or [_empty_row(section_profile["table_type"])]

    table = document.add_table(rows=1, cols=len(columns))
    table.style = None

    header = table.rows[0]
    _mark_repeat_header(header)
    for index, column in enumerate(columns):
        cell = header.cells[index]
        set_cell_text(cell, column["label"], profile, "table_header", "center")
        shade_cell(cell, profile["colors"]["table_header_fill"])

    for row_index, row in enumerate(output_rows, start=1):
        table_row = table.add_row()
        for column_index, column in enumerate(columns):
            key = column["key"]
            cell = table_row.cells[column_index]
            _fill_body_cell(
                cell=cell,
                key=key,
                row=row,
                row_index=row_index,
                section_code=section["code"],
                profile=profile,
                mode=mode,
                figure_refs=figure_refs,
            )

    configure_table_geometry(table, [float(column["width_in"]) for column in columns], profile)
    _merge_repeated_unit_cells(table, output_rows)
    return table


def _fill_body_cell(
    cell: _Cell,
    key: str,
    row: Any,
    row_index: int,
    section_code: str,
    profile: dict[str, Any],
    mode: str,
    figure_refs: dict[int, dict[str, str]],
) -> None:
    if key == "record_text":
        _set_record_cell(cell, _value(row, key), profile, figure_refs)
        return

    if key in {"d", "a", "k"}:
        value = _value(row, key) or profile["content_controls"]["technical_metric"]["default"]
        _set_metric_cell(
            cell=cell,
            value=value,
            tag=f"{section_code.replace('-', '')}.row{row_index}.{key.upper()}",
            options=profile["content_controls"]["technical_metric"]["options"],
            profile=profile,
            mode=mode,
        )
        return

    if key == "compliance":
        value = _value(row, key) or profile["content_controls"]["management_compliance"]["default"]
        _set_metric_cell(
            cell=cell,
            value=value,
            tag=f"{section_code.replace('-', '')}.row{row_index}.compliance",
            options=profile["content_controls"]["management_compliance"]["options"],
            profile=profile,
            mode=mode,
        )
        return

    alignment = "center" if key in {"object_score", "unit_score"} else "left"
    set_cell_text(cell, _value(row, key), profile, "body", alignment)


def _set_metric_cell(
    cell: _Cell,
    value: str,
    tag: str,
    options: list[str],
    profile: dict[str, Any],
    mode: str,
) -> None:
    set_cell_text(cell, value, profile, "body", "center")
    if mode == "editable":
        wrap_cell_paragraph_with_dropdown(cell, tag, value, options)


def _set_record_cell(
    cell: _Cell,
    text: str,
    profile: dict[str, Any],
    figure_refs: dict[int, dict[str, str]],
) -> None:
    cell.text = ""
    paragraph = cell.paragraphs[0]
    set_paragraph_format(paragraph, profile, "body")
    paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    set_cell_margins(cell)

    cursor = 0
    for match in FIG_TOKEN_RE.finditer(text or ""):
        if match.start() > cursor:
            _append_text(paragraph, text[cursor:match.start()], profile)
        image_id = int(match.group(1))
        ref = figure_refs.get(image_id)
        if ref:
            add_complex_field(paragraph, f"REF {ref['bookmark']} \\h", ref["label"])
        else:
            _append_text(paragraph, match.group(0), profile)
        cursor = match.end()

    if cursor < len(text or ""):
        _append_text(paragraph, (text or "")[cursor:], profile)


def _append_text(paragraph, text: str, profile: dict[str, Any]) -> None:
    parts = text.splitlines()
    for index, part in enumerate(parts):
        if index:
            paragraph.add_run().add_break()
        run = paragraph.add_run(part)
        apply_run_font(run, profile, "body")


def _section_profile(profile: dict[str, Any], code: str) -> dict[str, Any]:
    for section in profile["sections"]:
        if section["code"] == code:
            return section
    raise ValueError(f"模板 profile 缺少章节：{code}")


def _empty_row(table_type: str) -> dict[str, str]:
    if table_type == "technical":
        return {"unit": "", "object_name": "", "record_text": "", "d": "/", "a": "/", "k": ""}
    return {"unit": "", "object_name": "", "record_text": "", "compliance": "不适用"}


def _value(row: Any, key: str) -> str:
    if isinstance(row, dict):
        value = row.get(key)
    elif hasattr(row, "keys") and key in row.keys():
        value = row[key]
    else:
        value = None
    return "" if value is None else str(value)


def _mark_repeat_header(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    tr_pr.append(header)


def _merge_repeated_unit_cells(table: Table, rows: list[Any]) -> None:
    start_index = 1
    previous_unit = None
    for offset, row in enumerate(rows + [None], start=1):
        unit = _value(row, "unit") if row is not None else None
        if offset == 1:
            previous_unit = unit
            start_index = 1
            continue
        if unit != previous_unit:
            end_index = offset - 1
            if previous_unit and end_index > start_index:
                table.cell(start_index, 0).merge(table.cell(end_index, 0))
            start_index = offset
            previous_unit = unit
