"""从批准源模板按 OPC 白名单重建脱敏运行时母版。"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import re
import tempfile
import zipfile
from pathlib import Path

from lxml import etree
try:
    from ._safe_output import ensure_distinct_paths
except ImportError:  # 直接作为 CLI 脚本执行
    from _safe_output import ensure_distinct_paths

APPROVED_SOURCE_SHA256 = "b3957fd1da3bf19c31ac515fbdc6bf989fd7df033ca4d179c4b6e9567247fcf8"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC = "http://purl.org/dc/elements/1.1/"
NS = {"w": W, "w14": W14, "pr": PR, "ct": CT, "cp": CP, "dc": DC}

SDT_TYPE_NAMES = {
    "equation", "comboBox", "date", "docPartObj", "docPartList", "dropDownList",
    "picture", "richText", "text", "citation", "group", "bibliography",
}

ALLOWED_EXACT_PARTS = {
    "[Content_Types].xml", "_rels/.rels", "word/document.xml", "word/_rels/document.xml.rels",
    "word/footnotes.xml", "word/endnotes.xml", "word/theme/theme1.xml", "word/settings.xml",
    "word/numbering.xml", "word/webSettings.xml", "word/fontTable.xml", "word/styles.xml",
    "docProps/core.xml", "docProps/app.xml",
}
FORBIDDEN_REL_SUFFIXES = (
    "/comments", "/commentsExtended", "/commentsExtensible", "/commentsIds", "/person", "/people",
    "/oleObject", "/customXml", "/glossaryDocument", "/attachedTemplate", "/package",
    "/custom-properties",
    "/image",
)


def _keep_part(name: str) -> bool:
    return name in ALLOWED_EXACT_PARTS or bool(re.fullmatch(r"word/(header|footer)\d+\.xml", name))


def _xml(data: bytes) -> etree._Element:
    return etree.fromstring(data, parser=etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False))


def _serialize(root: etree._Element) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _clean_relationships(data: bytes) -> bytes:
    root = _xml(data)
    for rel in list(root):
        rel_type = rel.get("Type", "")
        if rel.get("TargetMode") == "External" or rel_type.endswith(FORBIDDEN_REL_SUFFIXES):
            root.remove(rel)
    return _serialize(root)


def _clean_content_types(data: bytes, kept: set[str]) -> bytes:
    root = _xml(data)
    for node in list(root):
        part_name = node.get("PartName")
        if part_name and part_name.lstrip("/") not in kept:
            root.remove(node)
        if node.get("Extension") in {"bin"}:
            root.remove(node)
    return _serialize(root)


def _clear_sdt_content(sdt: etree._Element) -> None:
    if sdt.xpath("./w:sdtPr/w:docPartObj", namespaces=NS):
        return
    properties = sdt.find(f"{{{W}}}sdtPr")
    if properties is not None:
        if properties.find(f"{{{W14}}}checkbox") is not None:
            # Word 复选框的可见状态由 sdtContent 中的 w:sym/w:t 承载。
            # 清空这些节点会保留可点击状态却让方框在页面上消失，因此按源模板
            # 原样保留复选框内容和选中状态；普通输入控件仍继续脱敏。
            return
        # 正文文字清空后 Word/LibreOffice 仍会渲染占位符属性，必须同步移除。
        for name in ("showingPlcHdr", "placeholder"):
            for node in properties.findall(f"{{{W}}}{name}"):
                properties.remove(node)
        for item in properties.xpath(".//w:listItem", namespaces=NS):
            display = item.get(f"{{{W}}}displayText", "")
            value = item.get(f"{{{W}}}value", "")
            if display == "选择一项。" or value == "选择一项。":
                # Word 拒绝 displayText/value 同时为空的下拉项。使用单个空格
                # 保留视觉空白首项，同时确保 DOCX 可由 Word 无修复提示打开。
                item.set(f"{{{W}}}displayText", " ")
                item.set(f"{{{W}}}value", " ")
    for text in sdt.xpath(".//w:t", namespaces=NS):
        text.text = ""
    for checked in sdt.xpath(".//w:checked", namespaces=NS):
        checked.set(f"{{{W}}}val", "0")


def _set_sdt_identity(properties: etree._Element, tag_value: str, alias_value: str) -> None:
    """按 CT_SdtPr 规定的子元素顺序写入 alias 和 tag。"""
    for name in ("tag", "alias", "dataBinding"):
        for old in properties.findall(f"{{{W}}}{name}"):
            properties.remove(old)

    alias = etree.Element(f"{{{W}}}alias")
    alias.set(f"{{{W}}}val", alias_value)
    alias_index = 1 if len(properties) and etree.QName(properties[0]).localname == "rPr" else 0
    properties.insert(alias_index, alias)

    tag = etree.Element(f"{{{W}}}tag")
    tag.set(f"{{{W}}}val", tag_value)
    type_index = len(properties)
    for child_index, child in enumerate(properties):
        child_name = etree.QName(child)
        if child_name.namespace != W or child_name.localname in SDT_TYPE_NAMES:
            type_index = child_index
            break
    properties.insert(type_index, tag)


def _iter_row_cells(row: etree._Element) -> list[etree._Element]:
    return row.xpath("./w:tc | ./w:sdt/w:sdtContent/w:tc", namespaces=NS)


def _scrub_story(root: etree._Element, body_paragraphs: list[etree._Element] | None = None) -> None:
    body_paragraphs = body_paragraphs or []
    sensitive_phrases = (
        "天津自贸试验区（中心商务区）新华路3678号宝风大厦28层2802",
        "李文宝",
        "商务经理",
        "业务部",
    )
    for paragraph in root.xpath("//w:p", namespaces=NS):
        paragraph_text = "".join(paragraph.xpath(".//w:t/text()", namespaces=NS))
        is_body_example_range = paragraph in body_paragraphs[48:154] and len(paragraph_text.strip()) > 12
        contains_sensitive = any(value in paragraph_text for value in sensitive_phrases)
        contains_pattern_sensitive = bool(
            re.search(r"\b1[3-9]\d{9}\b", paragraph_text)
            or re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", paragraph_text)
            or re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", paragraph_text)
        )
        if is_body_example_range or contains_sensitive or contains_pattern_sensitive or "示例" in paragraph_text or "{" in paragraph_text or "}" in paragraph_text or re.search(r"(?<![A-Za-z])X{1,20}(?![A-Za-z])", paragraph_text):
            for text in paragraph.xpath(".//w:t", namespaces=NS):
                text.text = ""
    for text in root.xpath("//w:t", namespaces=NS):
        value = text.text or ""
        for sensitive in sensitive_phrases:
            value = value.replace(sensitive, "")
        value = re.sub(r"\{[^{}]*\}", "", value)
        value = re.sub(r"(?<![A-Za-z])X{1,20}(?![A-Za-z])", "", value)
        value = re.sub(r"\b1[3-9]\d{9}\b", "", value)
        value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "", value)
        value = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "", value)
        text.text = value.replace("选择一项。", "")


def _clean_document(data: bytes) -> bytes:
    root = _xml(data)
    for node in root.xpath("//w:commentRangeStart | //w:commentRangeEnd | //w:commentReference | //w:object | //w:altChunk", namespaces=NS):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)
    for sdt_index, sdt in enumerate(root.xpath("//w:sdt", namespaces=NS), start=1):
        properties = sdt.find(f"{{{W}}}sdtPr")
        if properties is not None:
            _set_sdt_identity(
                properties,
                f"template.control.{sdt_index:04d}",
                f"运行时控件 {sdt_index:04d}",
            )
        _clear_sdt_content(sdt)

    body_paragraphs = root.xpath("/w:document/w:body/w:p", namespaces=NS)
    _scrub_story(root, body_paragraphs)

    # 基础模板 A-7 的第 4 列错误地让两个对象共享符合情况；运行时改为对象级输入。
    tables = root.xpath("/w:document/w:body/w:tbl", namespaces=NS)
    if len(tables) >= 45:
        for row in tables[44].xpath("./w:tr[position()>1]", namespaces=NS):
            cells = _iter_row_cells(row)
            if len(cells) >= 4:
                for merge in cells[3].xpath("./w:tcPr/w:vMerge", namespaces=NS):
                    merge.getparent().remove(merge)

    # 附录 A 数据行只保留测评单元标签，清除模板中的对象、记录、判定与评分示例。
    for table_index, table in enumerate(tables[38:46], start=39):
        header_rows = 2 if table_index <= 42 else 1
        for row in table.xpath("./w:tr", namespaces=NS)[header_rows:]:
            for cell in _iter_row_cells(row)[1:]:
                for text in cell.xpath(".//w:t", namespaces=NS):
                    text.text = ""

    # 为 55 张表建立稳定表锚点与块级起止锚点，避免后续依赖表格序号定位。
    bookmark_id = 9000
    def add_zero_bookmark(paragraph: etree._Element, name: str) -> None:
        nonlocal bookmark_id
        start = etree.Element(f"{{{W}}}bookmarkStart")
        start.set(f"{{{W}}}id", str(bookmark_id))
        start.set(f"{{{W}}}name", name)
        end = etree.Element(f"{{{W}}}bookmarkEnd")
        end.set(f"{{{W}}}id", str(bookmark_id))
        paragraph.insert(0, start)
        paragraph.insert(1, end)
        bookmark_id += 1

    for table_index, table in enumerate(tables, start=1):
        paragraphs = table.xpath(".//w:p", namespaces=NS)
        if not paragraphs:
            continue
        add_zero_bookmark(paragraphs[0], f"rt_table_{table_index:03d}")
        add_zero_bookmark(paragraphs[0], f"block_table_{table_index:03d}_start")
        add_zero_bookmark(paragraphs[-1], f"block_table_{table_index:03d}_end")

    def add_semantic_sdt(
        paragraph: etree._Element,
        tag_value: str,
        alias_value: str,
        position: int | None = None,
        display_text: str = "",
    ) -> etree._Element:
        sdt = etree.Element(f"{{{W}}}sdt")
        properties = etree.SubElement(sdt, f"{{{W}}}sdtPr")
        _set_sdt_identity(properties, tag_value, alias_value)
        content = etree.SubElement(sdt, f"{{{W}}}sdtContent")
        run = etree.SubElement(content, f"{{{W}}}r")
        reference_properties = paragraph.xpath(".//w:r/w:rPr", namespaces=NS)
        if reference_properties:
            run.append(copy.deepcopy(reference_properties[0]))
        text = etree.SubElement(run, f"{{{W}}}t")
        text.text = display_text
        if display_text != display_text.strip() or "  " in display_text:
            text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        if position is None:
            paragraph.append(sdt)
        else:
            paragraph.insert(position, sdt)
        return sdt

    def append_formatted_text(paragraph: etree._Element, value: str) -> None:
        run = etree.SubElement(paragraph, f"{{{W}}}r")
        reference_properties = paragraph.xpath(".//w:r/w:rPr", namespaces=NS)
        if reference_properties:
            run.append(copy.deepcopy(reference_properties[0]))
        etree.SubElement(run, f"{{{W}}}t").text = value

    def replace_paragraph_text(paragraph: etree._Element, value: str) -> None:
        texts = paragraph.xpath(".//w:t", namespaces=NS)
        if texts:
            texts[0].text = value
            for text in texts[1:]:
                text.text = ""
            return
        run = etree.SubElement(paragraph, f"{{{W}}}r")
        etree.SubElement(run, f"{{{W}}}t").text = value

    def table_cell_paragraph(table_number: int, row_number: int, cell_number: int) -> etree._Element:
        rows = tables[table_number - 1].xpath("./w:tr", namespaces=NS)
        cells = _iter_row_cells(rows[row_number])
        return cells[cell_number].xpath(".//w:p", namespaces=NS)[0]

    # 恢复“总体评价”的原始 18 段结构：首段概述、8 组层面描述与测评结果、末段结论。
    # 仅移除源模板中的客户化长示例，保留原句骨架并改用明确中文占位项。
    replace_paragraph_text(
        body_paragraphs[48],
        "本次信息系统商用密码应用安全性评估依据GB/T 39786—2021《信息安全技术 信息系统密码应用基本要求》"
        "的第三级别要求，选取的测评指标总数为41项，其中不适用项为【不适用项数量】项，特殊指标"
        "【特殊指标数量】项。测评结果为：符合项【符合项数量】项，部分符合项【部分符合项数量】项，"
        "不符合项【不符合项数量】项。其中，在部分符合和不符合项中：高风险项【高风险项数量】项，"
        "中风险项【中风险项数量】项，低风险项【低风险项数量】项。",
    )
    evaluation_layers = (
        "物理和环境安全",
        "网络和通信安全",
        "设备和计算安全",
        "应用和数据安全",
        "管理制度",
        "人员管理",
        "建设运行",
        "应急处置",
    )
    evaluation_result_text = (
        "测评结果：符合项【符合项数量】项，部分符合项【部分符合项数量】项，"
        "不符合项【不符合项数量】项，不适用项【不适用项数量】项。"
    )
    for layer_index, layer_name in enumerate(evaluation_layers):
        replace_paragraph_text(body_paragraphs[49 + layer_index * 2], f"在{layer_name}方面，【情况描述】。")
        replace_paragraph_text(body_paragraphs[50 + layer_index * 2], evaluation_result_text)
    replace_paragraph_text(
        body_paragraphs[65],
        "通过对【被测系统】的物理和环境安全、网络和通信安全、设备和计算安全、应用和数据安全、"
        "管理制度、人员管理、建设运行和应急处置等方面的测评，该系统【符合/基本符合/不符合】"
        "GB/T 39786—2021《信息安全技术 信息系统密码应用基本要求》的第三级别要求。",
    )

    replace_paragraph_text(body_paragraphs[0], "报告编号：")
    replace_paragraph_text(body_paragraphs[28], "本报告是")
    replace_paragraph_text(body_paragraphs[34], "")
    replace_paragraph_text(body_paragraphs[35], "")
    replace_paragraph_text(body_paragraphs[187], "现场测评阶段时间：")

    # 业务确认允许固化的密评机构资料，仅恢复到基本信息表的指定单元格。
    # 源模板其他位置以及客户数据仍受 _scrub_story 的脱敏规则约束。
    fixed_assessment_organization = {
        (29, 1): "中互金认证有限公司",
        (30, 1): "天津自贸试验区（中心商务区）新华路3678号宝风大厦28层2802",
        (30, 3): "300450",
        (31, 2): "李文宝",
        (31, 4): "商务经理",
        (32, 2): "业务部",
        (32, 4): "010-88720451",
        (33, 2): "15201294794",
        (33, 4): "liwb@secallab.com",
    }
    for (row_number, cell_number), value in fixed_assessment_organization.items():
        replace_paragraph_text(table_cell_paragraph(2, row_number, cell_number), value)

    # 评估结论页删除填写提示，仅保留原模板的正式“测评情况简介”描述。
    # 原文中的 XX 改为明确的中文占位项，便于后续报告装配替换且避免歧义。
    assessment_summary_cell = _iter_row_cells(tables[2].xpath("./w:tr", namespaces=NS)[2])[1]
    assessment_summary_paragraphs = assessment_summary_cell.xpath("./w:p", namespaces=NS)
    if len(assessment_summary_paragraphs) != 2:
        raise ValueError("ASSESSMENT_SUMMARY_TEMPLATE_STRUCTURE_INVALID")
    assessment_summary_cell.remove(assessment_summary_paragraphs[0])
    replace_paragraph_text(
        assessment_summary_paragraphs[1],
        "受【被测单位】委托，中互金认证有限公司于【开始日期】至【结束日期】对【被测单位】的"
        "【被测系统】进行了商用密码应用安全性评估，本次评估包含物理和环境、网络和通信、"
        "设备和计算、应用和数据等密码技术应用要求部分和管理制度、人员管理、建设运行、"
        "应急处置等密码应用管理要求部分的测评，评估已完成41项测评项的测评工作，其中"
        "符合项【符合项数量】项，部分符合项【部分符合项数量】项，不符合项【不符合项数量】项，"
        "不适用项【不适用项数量】项。风险分析发现被测系统存在【风险问题】。",
    )
    summary_paragraph = assessment_summary_paragraphs[1]
    paragraph_properties = summary_paragraph.find(f"{{{W}}}pPr")
    if paragraph_properties is None:
        paragraph_properties = etree.Element(f"{{{W}}}pPr")
        summary_paragraph.insert(0, paragraph_properties)
    indentation = paragraph_properties.find(f"{{{W}}}ind")
    if indentation is None:
        indentation = etree.Element(f"{{{W}}}ind")
        paragraph_mark_properties = paragraph_properties.find(f"{{{W}}}rPr")
        insert_at = paragraph_properties.index(paragraph_mark_properties) if paragraph_mark_properties is not None else len(paragraph_properties)
        paragraph_properties.insert(insert_at, indentation)
    for attribute in ("hanging", "hangingChars"):
        indentation.attrib.pop(f"{{{W}}}{attribute}", None)
    indentation.set(f"{{{W}}}firstLineChars", "200")
    indentation.set(f"{{{W}}}firstLine", "420")
    for run_properties in summary_paragraph.xpath("./w:pPr/w:rPr | .//w:r/w:rPr", namespaces=NS):
        for name in ("i", "iCs"):
            for old in run_properties.findall(f"{{{W}}}{name}"):
                run_properties.remove(old)
            disabled = etree.SubElement(run_properties, f"{{{W}}}{name}")
            disabled.set(f"{{{W}}}val", "0")

    semantic_slots = {
        "report.identity.number": (body_paragraphs[0], "报告编号", ""),
        "report.identity.date": (body_paragraphs[35], "报告日期", "年   月   日"),
        "report.organization.assessed_name": (table_cell_paragraph(1, 0, 1), "被测单位名称", ""),
        "report.organization.assessment_name": (table_cell_paragraph(1, 1, 1), "测评机构名称", ""),
        "report.system.name": (table_cell_paragraph(2, 8, 1), "被测系统名称", ""),
        "report.system.overview": (table_cell_paragraph(3, 1, 1), "系统概述", ""),
        "report.system.network_architecture": (body_paragraphs[204], "网络架构说明", ""),
        "report.assessment.period": (body_paragraphs[187], "现场测评时间", ""),
        "report.assessment.methods": (next((body_paragraphs[i + 1] for i, p in enumerate(body_paragraphs[:-1]) if "".join(p.xpath(".//w:t/text()", namespaces=NS)).strip() == "现场测评方法"), body_paragraphs[257]), "测评方法", ""),
        "report.result.overall_score": (table_cell_paragraph(3, 3, 3), "综合得分", ""),
        "report.result.conclusion": (table_cell_paragraph(3, 3, 1), "总体结论", ""),
    }
    for tag_value, (paragraph, alias_value, display_text) in semantic_slots.items():
        add_semantic_sdt(paragraph, tag_value, alias_value, display_text=display_text)

    add_semantic_sdt(
        body_paragraphs[28],
        "report.declaration.system_name",
        "声明页被测系统名称",
        display_text="被测信息系统名称",
    )
    append_formatted_text(body_paragraphs[28], "的商用密码应用安全性评估报告，报告模板为2023年版。")
    add_semantic_sdt(
        body_paragraphs[34],
        "report.declaration.assessment_name",
        "声明页测评机构名称",
        display_text="测评机构名称",
    )

    security_paragraph = table_cell_paragraph(2, 10, 1)
    security_texts = security_paragraph.xpath(".//w:t", namespaces=NS)
    anchor_text = next(text for text in security_texts if "已定级备案，第" in (text.text or ""))
    level_suffix = next(text for text in security_texts if (text.text or "").startswith("级（一至四）"))
    level_suffix.text = (level_suffix.text or "")[1:]
    anchor_run = anchor_text.getparent()
    suffix_run = level_suffix.getparent()
    for text in security_texts[security_texts.index(anchor_text) + 1 : security_texts.index(level_suffix)]:
        text.text = ""
    add_semantic_sdt(
        security_paragraph,
        "report.system.security_level",
        "安全保护等级",
        security_paragraph.index(anchor_run) + 1,
    )
    if security_paragraph.index(suffix_run) < security_paragraph.index(anchor_run):
        raise ValueError("SECURITY_LEVEL_SLOT_ORDER_INVALID")
    return _serialize(root)


def _clean_core(data: bytes) -> bytes:
    root = _xml(data)
    for node in list(root):
        root.remove(node)
    return _serialize(root)


def _clean_app(data: bytes) -> bytes:
    root = _xml(data)
    for node in root:
        local = etree.QName(node).localname
        if local == "Application":
            node.text = "Open-FuLuA"
        elif local == "AppVersion":
            node.text = "1.0"
        elif local in {"Company", "Manager", "HyperlinkBase", "Template", "LastPrinted"}:
            node.text = ""
        elif local == "TotalTime":
            node.text = "0"
    return _serialize(root)


def _clean_story_part(data: bytes) -> bytes:
    root = _xml(data)
    _scrub_story(root)
    return _serialize(root)


def build(source: Path, output: Path) -> None:
    ensure_distinct_paths(source, output)
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != APPROVED_SOURCE_SHA256:
        raise ValueError("SOURCE_FINGERPRINT_NOT_APPROVED")
    with zipfile.ZipFile(io.BytesIO(raw)) as package:
        source_parts = {info.filename: package.read(info) for info in package.infolist() if not info.is_dir()}
    for name, data in source_parts.items():
        if name.startswith("word/") and name.endswith(".xml"):
            root = _xml(data)
            if root.xpath("//w:ins | //w:del | //w:moveFrom | //w:moveTo", namespaces=NS):
                raise ValueError("SOURCE_HAS_UNRESOLVED_REVISIONS")
    kept = {name for name in source_parts if _keep_part(name)}
    transformed: dict[str, bytes] = {}
    for name in sorted(kept):
        data = source_parts[name]
        if name == "[Content_Types].xml":
            data = _clean_content_types(data, kept)
        elif name.endswith(".rels"):
            data = _clean_relationships(data)
        elif name == "word/document.xml":
            data = _clean_document(data)
        elif name == "docProps/core.xml":
            data = _clean_core(data)
        elif name == "docProps/app.xml":
            data = _clean_app(data)
        elif re.fullmatch(r"word/(header|footer)\d+\.xml", name) or name in {"word/footnotes.xml", "word/endnotes.xml"}:
            data = _clean_story_part(data)
        transformed[name] = data

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".docx", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as target:
            for name, data in transformed.items():
                info = zipfile.ZipInfo(name, date_time=(2025, 12, 8, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                target.writestr(info, data)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.source, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
