from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.run import Run
from docx.text.paragraph import Paragraph


@dataclass
class BookmarkWriter:
    next_id: int = 1

    def start(self, paragraph: Paragraph, name: str) -> int:
        bookmark_id = self.next_id
        self.next_id += 1

        element = OxmlElement("w:bookmarkStart")
        element.set(qn("w:id"), str(bookmark_id))
        element.set(qn("w:name"), name)
        paragraph._p.append(element)
        return bookmark_id

    def end(self, paragraph: Paragraph, bookmark_id: int) -> None:
        element = OxmlElement("w:bookmarkEnd")
        element.set(qn("w:id"), str(bookmark_id))
        paragraph._p.append(element)


def add_complex_field(
    paragraph: Paragraph,
    instruction: str,
    display_text: str,
    run_formatter: Callable[[Run], None] | None = None,
) -> None:
    _append_field_char(paragraph, "begin", run_formatter)
    _append_instr_text(paragraph, instruction, run_formatter)
    _append_field_char(paragraph, "separate", run_formatter)
    display_run = paragraph.add_run(display_text)
    if run_formatter:
        run_formatter(display_run)
    _append_field_char(paragraph, "end", run_formatter)


def _append_field_char(
    paragraph: Paragraph,
    field_type: str,
    run_formatter: Callable[[Run], None] | None = None,
) -> None:
    run = OxmlElement("w:r")
    field_char = OxmlElement("w:fldChar")
    field_char.set(qn("w:fldCharType"), field_type)
    run.append(field_char)
    paragraph._p.append(run)
    if run_formatter:
        run_formatter(Run(run, paragraph))


def _append_instr_text(
    paragraph: Paragraph,
    instruction: str,
    run_formatter: Callable[[Run], None] | None = None,
) -> None:
    run = OxmlElement("w:r")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = instruction
    run.append(instr)
    paragraph._p.append(run)
    if run_formatter:
        run_formatter(Run(run, paragraph))
