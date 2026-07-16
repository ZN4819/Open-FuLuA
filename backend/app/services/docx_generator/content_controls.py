from __future__ import annotations

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.table import _Cell


def wrap_cell_paragraph_with_dropdown(
    cell: _Cell,
    tag: str,
    value: str,
    options: list[str],
    *,
    lock_control: bool = False,
) -> None:
    paragraph = cell.paragraphs[0]
    parent = cell._tc
    parent.remove(paragraph._p)

    sdt = OxmlElement("w:sdt")
    sdt_pr = OxmlElement("w:sdtPr")

    alias = OxmlElement("w:alias")
    alias.set(qn("w:val"), tag)
    sdt_pr.append(alias)

    tag_element = OxmlElement("w:tag")
    tag_element.set(qn("w:val"), tag)
    sdt_pr.append(tag_element)

    if lock_control:
        lock = OxmlElement("w:lock")
        lock.set(qn("w:val"), "sdtLocked")
        sdt_pr.append(lock)

    dropdown = OxmlElement("w:dropDownList")
    for option in options:
        item = OxmlElement("w:listItem")
        item.set(qn("w:displayText"), option)
        item.set(qn("w:value"), option)
        dropdown.append(item)
    sdt_pr.append(dropdown)

    sdt_content = OxmlElement("w:sdtContent")
    sdt_content.append(paragraph._p)
    sdt.append(sdt_pr)
    sdt.append(sdt_content)
    parent.append(sdt)


def wrap_cell_paragraph_with_plain_text(
    cell: _Cell,
    tag: str,
    *,
    multiline: bool = False,
    lock_control: bool = True,
) -> None:
    paragraph = cell.paragraphs[0]
    parent = cell._tc
    parent.remove(paragraph._p)
    sdt = OxmlElement("w:sdt")
    sdt_pr = OxmlElement("w:sdtPr")
    alias = OxmlElement("w:alias")
    alias.set(qn("w:val"), tag)
    sdt_pr.append(alias)
    tag_element = OxmlElement("w:tag")
    tag_element.set(qn("w:val"), tag)
    sdt_pr.append(tag_element)
    if lock_control:
        lock = OxmlElement("w:lock")
        lock.set(qn("w:val"), "sdtLocked")
        sdt_pr.append(lock)
    text = OxmlElement("w:text")
    if multiline:
        text.set(qn("w:multiLine"), "1")
    sdt_pr.append(text)
    content = OxmlElement("w:sdtContent")
    content.append(paragraph._p)
    sdt.append(sdt_pr)
    sdt.append(content)
    parent.append(sdt)
