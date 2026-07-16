"""WordprocessingML 受控结构与主动内容安全检查。

本模块只解析已经通过 :func:`read_safe_opc` 限额检查的内存部件。它不负责
数据库映射，也不会根据文档内容猜测业务身份；所有可回收控件必须具有导出时
签发的 ``fla:r7:v1:*`` 标签。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Mapping

from lxml import etree

from .package import parse_xml_part


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
_NS = {"w": W}
_TAG_PATTERN = re.compile(r"^fla:r7:v1:(?P<kind>[brs]):(?P<token>[A-Za-z0-9_-]{8,48})$")
_FIELD_COMMANDS = frozenset({"TOC", "PAGE", "PAGEREF", "SEQ", "REF", "STYLEREF"})
_REVISION_NAMES = frozenset(
    {
        "ins",
        "del",
        "moveFrom",
        "moveTo",
        "moveFromRangeStart",
        "moveFromRangeEnd",
        "moveToRangeStart",
        "moveToRangeEnd",
        "customXmlInsRangeStart",
        "customXmlInsRangeEnd",
        "customXmlDelRangeStart",
        "customXmlDelRangeEnd",
        "customXmlMoveFromRangeStart",
        "customXmlMoveFromRangeEnd",
        "customXmlMoveToRangeStart",
        "customXmlMoveToRangeEnd",
        "cellIns",
        "cellDel",
        "cellMerge",
        "tblGridChange",
        "numberingChange",
    }
)


class StructureSecurityError(ValueError):
    """受控结构不满足封闭契约。"""

    def __init__(
        self,
        code: str,
        *,
        part: str | None = None,
        tag: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.part = part
        self.tag = tag


def _fail(code: str, *, part: str | None = None, tag: str | None = None) -> None:
    raise StructureSecurityError(code, part=part, tag=tag)


@dataclass(frozen=True)
class RevisionFinding:
    part: str
    element_name: str


@dataclass(frozen=True)
class SdtSlot:
    tag: str
    token: str
    value: str
    part: str


@dataclass(frozen=True)
class SdtRow:
    tag: str
    token: str
    block_token: str
    sort_order: int
    slot_tokens: tuple[str, ...]
    geometry_hash: str


@dataclass(frozen=True)
class SdtBlock:
    tag: str
    token: str
    row_tokens: tuple[str, ...]


@dataclass(frozen=True)
class RoundtripStructure:
    blocks: tuple[SdtBlock, ...]
    rows: tuple[SdtRow, ...]
    slots: tuple[SdtSlot, ...]


def _paragraph_readonly_text(paragraph: etree._Element, part: str) -> str:
    chunks: list[str] = []
    field_stack: list[dict[str, object]] = []

    def field_token(instruction_chunks: list[str]) -> str:
        instruction = " ".join("".join(instruction_chunks).split())
        return f"<FIELD:{instruction}>"

    def visit(element: etree._Element) -> None:
        if element.tag == f"{{{W}}}sdt":
            controlled = _controlled_tag(element, part)
            if controlled is not None and controlled[0] == "s":
                chunks.append(f"<SLOT:{controlled[1]}>")
                return
        local = etree.QName(element).localname
        if element.tag == f"{{{W}}}fldSimple":
            if not field_stack:
                chunks.append(field_token([str(element.get(f"{{{W}}}instr") or "")]))
            return
        if element.tag == f"{{{W}}}fldChar":
            kind = str(element.get(f"{{{W}}}fldCharType") or "")
            if kind == "begin":
                field_stack.append({"chunks": [], "emitted": False})
            elif kind == "separate" and field_stack:
                frame = field_stack[-1]
                if len(field_stack) == 1:
                    chunks.append(field_token(frame["chunks"]))  # type: ignore[arg-type]
                frame["emitted"] = True
            elif kind == "end" and field_stack:
                frame = field_stack.pop()
                if not frame["emitted"] and not field_stack:
                    chunks.append(field_token(frame["chunks"]))  # type: ignore[arg-type]
            return
        if element.tag == f"{{{W}}}instrText":
            if field_stack:
                instruction_chunks = field_stack[-1]["chunks"]
                assert isinstance(instruction_chunks, list)
                instruction_chunks.append(element.text or "")
            return
        if local in {"drawing", "pict", "object"}:
            if not field_stack:
                chunks.append("<MEDIA>")
            return
        if element.tag == f"{{{W}}}t":
            if not field_stack:
                chunks.append(element.text or "")
            return
        if element.tag == f"{{{W}}}tab":
            if not field_stack:
                chunks.append("\t")
            return
        if element.tag in {f"{{{W}}}br", f"{{{W}}}cr"}:
            if not field_stack:
                chunks.append("\n")
            return
        for child in element:
            visit(child)

    visit(paragraph)
    # Word rewrites equivalent manual breaks and tabs as spaces during SaveAs.
    # Bind the visible token sequence while ignoring that representation-only
    # normalization.
    return " ".join("".join(chunks).split())


def is_comment_part(name: str) -> bool:
    basename = name.rsplit("/", 1)[-1].casefold()
    return name.startswith("word/") and (
        basename.startswith("comments") or basename == "people.xml"
    )


def _readonly_story_paragraphs(
    root: etree._Element,
    part: str,
    paragraphs: list[etree._Element] | None = None,
) -> list[str]:
    """Canonicalize a story while collapsing each generated TOC to one token."""

    toc_controls: dict[str, str] = {}
    tree = root.getroottree()
    for control in root.xpath(".//w:sdt", namespaces=_NS):
        if not control.xpath(".//w:instrText | .//w:fldSimple", namespaces=_NS):
            continue
        toc_instructions = [
            instruction
            for instruction in _field_instructions(control, part)
            if instruction.split(" ", 1)[0].upper() == "TOC"
        ]
        if not toc_instructions:
            continue
        tag = str(control.xpath("string(w:sdtPr/w:tag/@w:val)", namespaces=_NS))
        toc_controls[tree.getpath(control)] = (
            f"<GENERATED_TOC:{tag}:{'|'.join(toc_instructions)}>"
        )

    values: list[str] = []
    emitted: set[str] = set()
    selected = paragraphs if paragraphs is not None else list(
        root.xpath(".//w:p", namespaces=_NS)
    )
    for paragraph in selected:
        toc_path = None
        for ancestor in paragraph.iterancestors(f"{{{W}}}sdt"):
            candidate = tree.getpath(ancestor)
            if candidate in toc_controls:
                toc_path = candidate
                break
        if toc_path is not None:
            if toc_path not in emitted:
                values.append(toc_controls[toc_path])
                emitted.add(toc_path)
            continue
        values.append(_paragraph_readonly_text(paragraph, part))
    return values


def readonly_document_hash(parts: Mapping[str, bytes]) -> str:
    """Hash non-writable visible content and logical table geometry.

    Run/style/rsid changes, field caches, media binaries and writable slot
    values are intentionally excluded.  Readonly text, story order, SDT
    boundaries and logical table topology remain bound.
    """
    stories: list[dict[str, object]] = []
    main = parse_xml_part(parts["word/document.xml"], "word/document.xml")
    note_references = {
        "word/footnotes.xml": list(
            main.xpath(".//w:footnoteReference/@w:id", namespaces=_NS)
        ),
        "word/endnotes.xml": list(
            main.xpath(".//w:endnoteReference/@w:id", namespaces=_NS)
        ),
    }
    for part, data in _word_xml_parts(parts):
        if is_comment_part(part):
            # Comments are non-authoritative review metadata.  They are still
            # scanned for revisions, active fields and dangerous relationships
            # by the package validator, but their text does not bind business
            # content and is reported as an ignored change.
            continue
        root = parse_xml_part(data, part)
        selected_paragraphs: list[etree._Element] | None = None
        if part in note_references:
            note_name = "footnote" if part.endswith("footnotes.xml") else "endnote"
            notes = {
                str(note.get(f"{{{W}}}id") or ""): note
                for note in root.xpath(f"./w:{note_name}", namespaces=_NS)
            }
            selected_paragraphs = []
            for note_id in note_references[part]:
                note = notes.get(str(note_id))
                if note is not None:
                    selected_paragraphs.extend(
                        note.xpath(".//w:p", namespaces=_NS)
                    )
        paragraphs = _readonly_story_paragraphs(
            root,
            part,
            selected_paragraphs,
        )
        readonly_tags: list[str] = []
        for sdt in root.xpath(".//w:sdt", namespaces=_NS):
            controlled = _controlled_tag(sdt, part)
            if controlled is not None and controlled[0] != "s":
                readonly_tags.append(controlled[2])
        geometries = [
            _row_geometry_hash(row)
            for row in root.xpath(".//w:tr", namespaces=_NS)
        ]
        stories.append(
            {
                "part": part,
                "paragraphs": paragraphs,
                "readonly_tags": readonly_tags,
                "table_count": len(root.xpath(".//w:tbl", namespaces=_NS)),
                "section_count": len(root.xpath(".//w:sectPr", namespaces=_NS)),
                "row_geometry": geometries,
            }
        )
    canonical = json.dumps(stories, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _word_xml_parts(parts: Mapping[str, bytes]) -> list[tuple[str, bytes]]:
    return sorted(
        (
            (name, data)
            for name, data in parts.items()
            if name.startswith("word/") and name.lower().endswith(".xml")
        ),
        key=lambda item: item[0],
    )


def find_unresolved_revisions(parts: Mapping[str, bytes]) -> tuple[RevisionFinding, ...]:
    """扫描所有 Word XML 部件，而非仅正文和页眉页脚。"""

    findings: list[RevisionFinding] = []
    for part, data in _word_xml_parts(parts):
        root = parse_xml_part(data, part)
        for element in root.iter():
            qname = etree.QName(element)
            if qname.namespace == W14 and qname.localname in {"conflictIns", "conflictDel"}:
                findings.append(RevisionFinding(part=part, element_name=qname.localname))
                continue
            if qname.namespace != W:
                continue
            localname = qname.localname
            if localname in _REVISION_NAMES or localname.endswith("PrChange"):
                findings.append(RevisionFinding(part=part, element_name=localname))
    return tuple(findings)


def _normalized_instruction(chunks: list[str]) -> str:
    return " ".join("".join(chunks).split())


def _field_instructions(root: etree._Element, part: str) -> tuple[str, ...]:
    instructions: list[str] = []

    for field in root.xpath(".//w:fldSimple", namespaces=_NS):
        instruction = field.get(f"{{{W}}}instr")
        if instruction is None:
            _fail("WORD_FIELD_INSTRUCTION_MISSING", part=part)
        normalized = _normalized_instruction([instruction])
        if normalized:
            instructions.append(normalized)

    # Word may split one instruction across any number of w:instrText nodes/runs.
    # A stack is needed because complex fields may be nested.
    stack: list[dict[str, object]] = []
    loose_chunks: list[str] = []
    for element in root.iter():
        if element.tag == f"{{{W}}}fldChar":
            kind = element.get(f"{{{W}}}fldCharType")
            if kind == "begin":
                stack.append({"chunks": [], "collecting": True})
            elif kind == "separate":
                if not stack:
                    _fail("WORD_FIELD_STRUCTURE_INVALID", part=part)
                stack[-1]["collecting"] = False
            elif kind == "end":
                if not stack:
                    _fail("WORD_FIELD_STRUCTURE_INVALID", part=part)
                field = stack.pop()
                normalized = _normalized_instruction(field["chunks"])  # type: ignore[arg-type]
                if normalized:
                    instructions.append(normalized)
            else:
                _fail("WORD_FIELD_STRUCTURE_INVALID", part=part)
        elif element.tag == f"{{{W}}}instrText":
            text = element.text or ""
            owner = next((item for item in reversed(stack) if item["collecting"]), None)
            if owner is None:
                loose_chunks.append(text)
            else:
                owner_chunks = owner["chunks"]
                assert isinstance(owner_chunks, list)
                owner_chunks.append(text)

    if stack:
        _fail("WORD_FIELD_STRUCTURE_INVALID", part=part)
    if loose_chunks and _normalized_instruction(loose_chunks):
        _fail("WORD_FIELD_STRUCTURE_INVALID", part=part)
    return tuple(instructions)


def validate_field_instructions(parts: Mapping[str, bytes]) -> tuple[str, ...]:
    """只允许报告需要的五类静态 Word 字段。"""

    found: list[str] = []
    for part, data in _word_xml_parts(parts):
        root = parse_xml_part(data, part)
        for instruction in _field_instructions(root, part):
            command = instruction.split(maxsplit=1)[0].upper()
            if command not in _FIELD_COMMANDS:
                _fail("WORD_FIELD_INSTRUCTION_FORBIDDEN", part=part)
            if command == "STYLEREF" and instruction.upper() != "STYLEREF 1 \\S":
                _fail("WORD_FIELD_INSTRUCTION_FORBIDDEN", part=part)
            found.append(instruction)
    return tuple(found)


def _controlled_tag(sdt: etree._Element, part: str) -> tuple[str, str, str] | None:
    tags = sdt.xpath("./w:sdtPr/w:tag", namespaces=_NS)
    values = [element.get(f"{{{W}}}val") for element in tags]
    values = [value for value in values if value is not None]
    controlled = [value for value in values if value.startswith("fla:r7:")]
    if not controlled:
        return None
    if len(tags) != 1 or len(controlled) != 1:
        _fail("SDT_CONTROLLED_TAG_AMBIGUOUS", part=part)
    tag = controlled[0]
    match = _TAG_PATTERN.fullmatch(tag)
    if match is None:
        _fail("SDT_CONTROLLED_TAG_INVALID", part=part, tag=tag)
    return match.group("kind"), match.group("token"), tag


def _single_content(sdt: etree._Element, part: str, tag: str) -> etree._Element:
    contents = sdt.xpath("./w:sdtContent", namespaces=_NS)
    if len(contents) != 1:
        _fail("SDT_CONTENT_INVALID", part=part, tag=tag)
    return contents[0]


def _slot_value(content: etree._Element) -> str:
    paragraphs = content.xpath(".//w:p", namespaces=_NS)
    if not paragraphs:
        paragraphs = [content]
    values: list[str] = []
    for paragraph in paragraphs:
        chunks: list[str] = []
        for element in paragraph.iter():
            if element.tag == f"{{{W}}}t":
                chunks.append(element.text or "")
            elif element.tag == f"{{{W}}}tab":
                chunks.append("\t")
            elif element.tag in {f"{{{W}}}br", f"{{{W}}}cr"}:
                chunks.append("\n")
        values.append("".join(chunks))
    return "\n".join(values)


def _row_cells(row: etree._Element) -> list[etree._Element]:
    cells: list[etree._Element] = []
    for child in row:
        if child.tag == f"{{{W}}}tc":
            cells.append(child)
        elif child.tag == f"{{{W}}}sdt":
            nested = child.xpath("./w:sdtContent/w:tc", namespaces=_NS)
            if len(nested) != 1:
                _fail("SDT_ROW_CELL_WRAPPER_INVALID", part="word/document.xml")
            cells.append(nested[0])
        elif child.tag not in {
            f"{{{W}}}trPr",
            f"{{{W}}}bookmarkStart",
            f"{{{W}}}bookmarkEnd",
        }:
            _fail("SDT_ROW_CHILD_INVALID", part="word/document.xml")
    return cells


def _row_geometry_hash(row: etree._Element) -> str:
    geometry: list[dict[str, object]] = []
    for cell in _row_cells(row):
        spans = cell.xpath("./w:tcPr/w:gridSpan", namespaces=_NS)
        if len(spans) > 1:
            _fail("SDT_ROW_GEOMETRY_INVALID", part="word/document.xml")
        raw_span = spans[0].get(f"{{{W}}}val") if spans else "1"
        try:
            span = int(raw_span or "1")
        except ValueError:
            _fail("SDT_ROW_GEOMETRY_INVALID", part="word/document.xml")
        if span < 1 or span > 1024:
            _fail("SDT_ROW_GEOMETRY_INVALID", part="word/document.xml")
        merges = cell.xpath("./w:tcPr/w:vMerge", namespaces=_NS)
        if len(merges) > 1:
            _fail("SDT_ROW_GEOMETRY_INVALID", part="word/document.xml")
        merge: str | None = None
        if merges:
            merge = merges[0].get(f"{{{W}}}val") or "continue"
            if merge not in {"restart", "continue"}:
                _fail("SDT_ROW_GEOMETRY_INVALID", part="word/document.xml")
        geometry.append({"grid_span": span, "vertical_merge": merge})
    if not geometry:
        _fail("SDT_ROW_GEOMETRY_INVALID", part="word/document.xml")
    canonical = json.dumps(geometry, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def extract_roundtrip_structure(parts: Mapping[str, bytes]) -> RoundtripStructure:
    """提取正文中的 block/row/slot 受控 SDT，并验证基本嵌套和唯一性。"""

    part = "word/document.xml"
    if part not in parts:
        _fail("WORD_DOCUMENT_MISSING", part=part)
    root = parse_xml_part(parts[part], part)

    controls: list[tuple[etree._Element, str, str, str]] = []
    seen_tags: set[str] = set()
    seen_tokens: dict[str, set[str]] = {"b": set(), "r": set(), "s": set()}
    by_element: dict[etree._Element, tuple[str, str, str]] = {}
    for sdt in root.xpath(".//w:sdt", namespaces=_NS):
        parsed = _controlled_tag(sdt, part)
        if parsed is None:
            continue
        kind, token, tag = parsed
        if tag in seen_tags or token in seen_tokens[kind]:
            _fail("SDT_CONTROLLED_TAG_DUPLICATE", part=part, tag=tag)
        seen_tags.add(tag)
        seen_tokens[kind].add(token)
        controls.append((sdt, kind, token, tag))
        by_element[sdt] = (kind, token, tag)

    slots: list[SdtSlot] = []
    for sdt, kind, token, tag in controls:
        if kind != "s":
            continue
        content = _single_content(sdt, part, tag)
        if content.xpath(".//w:tbl | .//w:tr", namespaces=_NS):
            _fail("SDT_SLOT_BLOCK_CONTENT_FORBIDDEN", part=part, tag=tag)
        for descendant in content.xpath(".//w:sdt", namespaces=_NS):
            if descendant in by_element:
                _fail("SDT_SLOT_NESTED_CONTROL_FORBIDDEN", part=part, tag=tag)
        slots.append(SdtSlot(tag=tag, token=token, value=_slot_value(content), part=part))

    row_records: list[tuple[SdtRow, etree._Element]] = []
    rows_by_block: dict[str, list[str]] = {}
    block_tables: dict[str, etree._Element] = {}
    for block_sdt, block_kind, block_token, block_tag in controls:
        if block_kind != "b":
            continue
        block_content = _single_content(block_sdt, part, block_tag)
        tables = [child for child in block_content if child.tag == f"{{{W}}}tbl"]
        if len(block_content) != 1 or len(tables) != 1:
            _fail("SDT_BLOCK_CONTENT_INVALID", part=part, tag=block_tag)
        block_tables[block_token] = tables[0]
    for sdt, kind, token, tag in controls:
        if kind != "r":
            continue
        content = _single_content(sdt, part, tag)
        row_children = [child for child in content if child.tag == f"{{{W}}}tr"]
        if len(content) != 1 or len(row_children) != 1:
            _fail("SDT_ROW_CONTENT_INVALID", part=part, tag=tag)
        if sdt.getparent() is None or sdt.getparent().tag != f"{{{W}}}tbl":
            _fail("SDT_ROW_PARENT_INVALID", part=part, tag=tag)
        block: tuple[str, str, str] | None = None
        for ancestor in sdt.iterancestors(f"{{{W}}}sdt"):
            candidate = by_element.get(ancestor)
            if candidate is not None:
                block = candidate
                break
        if block is None or block[0] != "b":
            _fail("SDT_ROW_BLOCK_MISSING", part=part, tag=tag)
        block_token = block[1]
        if sdt.getparent() is not block_tables.get(block_token):
            _fail("SDT_ROW_PARENT_INVALID", part=part, tag=tag)
        slot_tokens = tuple(
            by_element[descendant][1]
            for descendant in content.xpath(".//w:sdt", namespaces=_NS)
            if descendant in by_element and by_element[descendant][0] == "s"
        )
        order = len(rows_by_block.setdefault(block_token, [])) + 1
        rows_by_block[block_token].append(token)
        row_records.append(
            (
                SdtRow(
                    tag=tag,
                    token=token,
                    block_token=block_token,
                    sort_order=order,
                    slot_tokens=slot_tokens,
                    geometry_hash=_row_geometry_hash(row_children[0]),
                ),
                sdt,
            )
        )

    blocks: list[SdtBlock] = []
    for sdt, kind, token, tag in controls:
        if kind != "b":
            continue
        blocks.append(SdtBlock(tag=tag, token=token, row_tokens=tuple(rows_by_block.get(token, []))))

    # A controlled row must not accidentally resolve to a block other than the one whose
    # table directly contains it. The ancestor check above establishes that invariant;
    # this final check catches orphan rows if a malformed tree escaped ordering logic.
    known_blocks = {block.token for block in blocks}
    if any(row.block_token not in known_blocks for row, _ in row_records):
        _fail("SDT_ROW_BLOCK_MISSING", part=part)

    return RoundtripStructure(
        blocks=tuple(blocks),
        rows=tuple(row for row, _ in row_records),
        slots=tuple(slots),
    )
