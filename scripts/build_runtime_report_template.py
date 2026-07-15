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
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
V = "urn:schemas-microsoft-com:vml"
O = "urn:schemas-microsoft-com:office:office"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC = "http://purl.org/dc/elements/1.1/"
NS = {"w": W, "w14": W14, "r": R, "v": V, "o": O, "pr": PR, "ct": CT, "cp": CP, "dc": DC}

APPROVED_WORKFLOW_IMAGE_PART = "word/media/image1.emf"
APPROVED_WORKFLOW_IMAGE_SHA256 = "008976a91115718e266c4dffcf3985fe92d2ee00063eac1fc42be592100d2a86"
APPROVED_WORKFLOW_IMAGE_RELATIONSHIP_ID = "rId25"

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
    return (
        name in ALLOWED_EXACT_PARTS
        or name == APPROVED_WORKFLOW_IMAGE_PART
        or bool(re.fullmatch(r"word/(header|footer)\d+\.xml", name))
    )


def _xml(data: bytes) -> etree._Element:
    return etree.fromstring(data, parser=etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False))


def _serialize(root: etree._Element) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _clean_relationships(data: bytes) -> bytes:
    root = _xml(data)
    for rel in list(root):
        rel_type = rel.get("Type", "")
        approved_workflow_image = (
            rel.get("Id") == APPROVED_WORKFLOW_IMAGE_RELATIONSHIP_ID
            and rel_type.endswith("/image")
            and rel.get("Target") == "media/image1.emf"
            and rel.get("TargetMode") != "External"
        )
        if rel.get("TargetMode") == "External" or (
            rel_type.endswith(FORBIDDEN_REL_SUFFIXES) and not approved_workflow_image
        ):
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
    # 源模板流程图是 Visio OLE 对象。仅保留其已批准、固定哈希的 EMF 静态预览，
    # 将 w:object 降级为普通 VML 图片；OLEObject 节点和 embeddings 二进制仍删除。
    for object_node in root.xpath(
        f"//w:object[.//v:imagedata[@r:id='{APPROVED_WORKFLOW_IMAGE_RELATIONSHIP_ID}']]",
        namespaces=NS,
    ):
        picture = etree.Element(f"{{{W}}}pict")
        for child in object_node:
            child_name = etree.QName(child)
            if child_name.namespace == V and child_name.localname in {"shapetype", "shape"}:
                clone = copy.deepcopy(child)
                if child_name.localname == "shape":
                    clone.attrib.pop(f"{{{O}}}ole", None)
                picture.append(clone)
        object_node.getparent().replace(object_node, picture)
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

    def replace_risk_summary_paragraph(paragraph: etree._Element) -> None:
        """写入高风险派生槽位，同时保留原模板的表 6-1 REF 字段。"""
        children = list(paragraph)
        begin_index = next(
            (
                index
                for index, child in enumerate(children)
                if child.xpath("./w:fldChar[@w:fldCharType='begin']", namespaces=NS)
            ),
            None,
        )
        end_index = next(
            (
                index
                for index, child in enumerate(children)
                if begin_index is not None
                and index >= begin_index
                and child.xpath("./w:fldChar[@w:fldCharType='end']", namespaces=NS)
            ),
            None,
        )
        if begin_index is None or end_index is None:
            raise ValueError("EXPECTED_PARAGRAPH_FIELD_MISSING")
        field_nodes = [copy.deepcopy(child) for child in children[begin_index : end_index + 1]]
        field_wrapper = etree.Element("field")
        for field_node in field_nodes:
            field_wrapper.append(field_node)
        separate_run = next(
            node
            for node in field_wrapper
            if node.xpath("./w:fldChar[@w:fldCharType='separate']", namespaces=NS)
        )
        separate_index = field_wrapper.index(separate_run)
        result_texts = field_wrapper.xpath(
            f"./w:r[position() > {separate_index + 1}]/w:t",
            namespaces=NS,
        )
        if len(result_texts) < 3:
            raise ValueError("EXPECTED_FIELD_RESULT_CACHE_MISSING")
        for text_node, display_part in zip(result_texts, ("表", "6", "1"), strict=False):
            text_node.text = display_part

        for child in list(paragraph):
            if etree.QName(child).namespace == W and etree.QName(child).localname == "pPr":
                continue
            paragraph.remove(child)
        append_formatted_text(paragraph, "根据《商用密码应用安全性评估高风险判定指引》")
        add_semantic_sdt(
            paragraph,
            "report.risk.high_risk_judgement",
            "高风险判定描述",
            display_text="【高风险判定】",
        )
        append_formatted_text(
            paragraph,
            "。经风险分析，系统存在高风险【高风险项数量】项，中风险【中风险项数量】项，"
            "低风险【低风险项数量】项，具体见",
        )
        for field_node in field_nodes:
            paragraph.append(field_node)
        append_formatted_text(paragraph, "：")

    def set_paragraph_nonitalic(paragraph: etree._Element) -> None:
        """显式关闭段落标记和所有文本运行的中西文斜体。"""
        properties = paragraph.find(f"{{{W}}}pPr")
        if properties is None:
            properties = etree.Element(f"{{{W}}}pPr")
            paragraph.insert(0, properties)
        paragraph_mark_properties = properties.find(f"{{{W}}}rPr")
        if paragraph_mark_properties is None:
            paragraph_mark_properties = etree.SubElement(properties, f"{{{W}}}rPr")
        run_properties = [paragraph_mark_properties]
        for run in paragraph.xpath(".//w:r", namespaces=NS):
            run_property = run.find(f"{{{W}}}rPr")
            if run_property is None:
                run_property = etree.Element(f"{{{W}}}rPr")
                run.insert(0, run_property)
            run_properties.append(run_property)
        for run_property in run_properties:
            for name in ("i", "iCs"):
                for old in run_property.findall(f"{{{W}}}{name}"):
                    run_property.remove(old)
                disabled = etree.SubElement(run_property, f"{{{W}}}{name}")
                disabled.set(f"{{{W}}}val", "0")

    def remove_paragraph_numbering(paragraph: etree._Element) -> None:
        properties = paragraph.find(f"{{{W}}}pPr")
        if properties is None:
            return
        for numbering in properties.findall(f"{{{W}}}numPr"):
            properties.remove(numbering)

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

    # “安全问题及改进建议”保留章节说明与八层面结构，但不把源模板示例写入空白报告。
    # 问题与建议将在后续装配阶段按实际发现动态生成并编号，因此清除示例条目的
    # 预置编号，避免空白母版出现只有“1）/2）/3）”的项目。源模板示例已提炼至
    # narrative_templates.json，作为须人工确认的写作参考，而不是报告默认内容。
    replace_paragraph_text(
        body_paragraphs[67],
        "本次信息系统商用密码应用安全性评估依据GB/T 39786—2021《信息安全技术 信息系统密码应用基本要求》"
        "的第三级别要求，发现被测信息系统存在以下安全问题。建议被测信息系统根据实际情况和以下给出的建议进行整改。",
    )
    security_issue_item_ranges = (
        (range(70, 73), range(74, 76)),
        (range(78, 82), range(83, 85)),
        (range(87, 92), range(93, 98)),
        (range(100, 107), range(108, 114)),
        (range(116, 122), range(123, 129)),
        (range(131, 135), range(136, 140)),
        (range(142, 145), range(146, 149)),
        (range(151, 152), range(153, 154)),
    )
    for problem_range, recommendation_range in security_issue_item_ranges:
        for paragraph_index in problem_range:
            replace_paragraph_text(body_paragraphs[paragraph_index], "")
            remove_paragraph_numbering(body_paragraphs[paragraph_index])
        for paragraph_index in recommendation_range:
            replace_paragraph_text(body_paragraphs[paragraph_index], "")
            remove_paragraph_numbering(body_paragraphs[paragraph_index])
    for paragraph in body_paragraphs[67:154]:
        set_paragraph_nonitalic(paragraph)

    # 第一章保留批准模板的正式描述；项目相关值使用明确中文占位项或语义槽位。
    replace_paragraph_text(
        body_paragraphs[158],
        "中互金认证有限公司受【被测单位】委托，于【测评开始日期】至【测评结束日期】，依据"
        "GB/T 39786—2021《信息安全技术 信息系统密码应用基本要求》的第三级别要求，对"
        "【被测单位】的【被测系统】从物理和环境安全、网络和通信安全、设备和计算安全、应用和数据安全、"
        "管理制度、人员管理、建设运行和应急处置等方面进行商用密码应用安全性评估，通过测评项目的实施，"
        "根据被测信息系统当前的安全状况，给出测评结果并提出改进建议，以确保被测信息系统达到"
        "GB/T 39786—2021《信息安全技术 信息系统密码应用基本要求》的要求，也为其信息资产安全和业务持续稳定运行提供保障。",
    )
    reference_standards = (
        "GB/T 43206—2023《信息安全技术 信息系统密码应用测评要求》",
        "GB/T 43207—2023《信息安全技术 信息系统密码应用设计指南》",
        "GM/T 0116—2021《信息系统密码应用测评过程指南》",
        "《信息系统密码应用高风险判定指引》",
        "《商用密码应用安全性评估量化评估规则》",
    )
    for paragraph_index, standard in enumerate(reference_standards, start=163):
        replace_paragraph_text(body_paragraphs[paragraph_index], standard)
    for paragraph_index in (168, 169):
        replace_paragraph_text(body_paragraphs[paragraph_index], "")
        remove_paragraph_numbering(body_paragraphs[paragraph_index])
    add_zero_bookmark(body_paragraphs[168], "report_additional_reference_standards")

    replace_paragraph_text(body_paragraphs[178], "测评准备阶段时间：")
    replace_paragraph_text(body_paragraphs[183], "方案编制阶段时间：")
    replace_paragraph_text(body_paragraphs[187], "现场测评阶段时间：")
    replace_paragraph_text(body_paragraphs[191], "分析与报告编制阶段时间：")

    replace_risk_summary_paragraph(body_paragraphs[357])
    set_paragraph_nonitalic(body_paragraphs[357])
    replace_paragraph_text(
        body_paragraphs[361],
        "通过对【被测单位】的【被测系统】的物理和环境安全、网络和通信安全、设备和计算安全、"
        "应用和数据安全、管理制度、人员管理、建设运行和应急处置等方面的测评，该系统综合得分为"
        "【综合得分】分，系统密码应用面临【风险等级】风险，【符合/基本符合/不符合】"
        "GB/T 39786—2021《信息安全技术 信息系统密码应用基本要求》的第三级别要求。",
    )
    set_paragraph_nonitalic(body_paragraphs[361])

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
    set_paragraph_nonitalic(summary_paragraph)

    semantic_slots = {
        "report.identity.number": (body_paragraphs[0], "报告编号", ""),
        "report.cover.system_name": (body_paragraphs[3], "首页被测系统名称", "【被测系统名称】"),
        "report.identity.date": (body_paragraphs[35], "报告日期", "年   月   日"),
        "report.organization.assessed_name": (table_cell_paragraph(1, 0, 1), "被测单位名称", ""),
        "report.organization.assessment_name": (table_cell_paragraph(1, 1, 1), "测评机构名称", ""),
        "report.system.name": (table_cell_paragraph(2, 8, 1), "被测系统名称", ""),
        "report.system.overview": (table_cell_paragraph(3, 1, 1), "系统概述", ""),
        "report.system.network_architecture": (body_paragraphs[204], "网络架构说明", ""),
        "report.assessment.preparation_period": (body_paragraphs[178], "测评准备阶段时间", "【开始日期】至【结束日期】"),
        "report.assessment.plan_period": (body_paragraphs[183], "方案编制阶段时间", "【开始日期】至【结束日期】"),
        "report.assessment.period": (body_paragraphs[187], "现场测评阶段时间", "【开始日期】至【结束日期】"),
        "report.assessment.report_period": (body_paragraphs[191], "分析与报告编制阶段时间", "【开始日期】至【结束日期】"),
        "report.assessment.methods": (next((body_paragraphs[i + 1] for i, p in enumerate(body_paragraphs[:-1]) if "".join(p.xpath(".//w:t/text()", namespaces=NS)).strip() == "现场测评方法"), body_paragraphs[257]), "测评方法", ""),
        "report.result.overall_score": (table_cell_paragraph(3, 3, 3), "综合得分", ""),
        "report.result.conclusion": (table_cell_paragraph(3, 3, 1), "总体结论", ""),
    }
    for tag_value, (paragraph, alias_value, display_text) in semantic_slots.items():
        add_semantic_sdt(paragraph, tag_value, alias_value, display_text=display_text)

    replace_paragraph_text(body_paragraphs[193], "本报告一式")
    add_semantic_sdt(body_paragraphs[193], "report.distribution.total_copies", "报告总份数", display_text="【总份数】")
    append_formatted_text(body_paragraphs[193], "份，其中")
    add_semantic_sdt(body_paragraphs[193], "report.distribution.regulator_copies", "密码管理部门份数", display_text="【密码管理部门份数】")
    append_formatted_text(body_paragraphs[193], "份提交密码管理部门，")
    add_semantic_sdt(body_paragraphs[193], "report.distribution.client_copies", "委托单位份数", display_text="【委托单位份数】")
    append_formatted_text(body_paragraphs[193], "份提交委托单位，")
    add_semantic_sdt(body_paragraphs[193], "report.distribution.assessment_copies", "密评机构留存份数", display_text="【密评机构留存份数】")
    append_formatted_text(body_paragraphs[193], "份由密评机构留存。")

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
    workflow_image = source_parts.get(APPROVED_WORKFLOW_IMAGE_PART)
    if workflow_image is None or hashlib.sha256(workflow_image).hexdigest() != APPROVED_WORKFLOW_IMAGE_SHA256:
        raise ValueError("WORKFLOW_IMAGE_FINGERPRINT_NOT_APPROVED")
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
