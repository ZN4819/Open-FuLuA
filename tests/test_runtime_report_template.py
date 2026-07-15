from __future__ import annotations

import hashlib
import re
import sys
import unittest
import zipfile
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.report_templates import analyze_report_template
from scripts._safe_output import ensure_distinct_paths
from scripts.build_runtime_report_template import _disable_all_italics, _scrub_story

RUNTIME = ROOT / "templates" / "report" / "2023-2025.12.08" / "runtime_template.docx"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
V = "urn:schemas-microsoft-com:vml"
O = "urn:schemas-microsoft-com:office:office"
NS = {"w": W, "w14": W14, "r": R, "a": A, "m": M, "v": V, "o": O}


class RuntimeReportTemplateTests(unittest.TestCase):
    def test_cross_run_phone_email_and_ip_are_scrubbed(self) -> None:
        root = etree.fromstring(
            f'<w:document xmlns:w="{W}"><w:body>'
            '<w:p><w:r><w:t>13800</w:t></w:r><w:r><w:t>138000</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>alice</w:t></w:r><w:r><w:t>@example.com</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>10.0.</w:t></w:r><w:r><w:t>0.1</w:t></w:r></w:p>'
            '</w:body></w:document>'.encode("utf-8")
        )
        _scrub_story(root)
        self.assertEqual("".join(root.xpath("//w:t/text()", namespaces=NS)), "")

    def test_runtime_template_is_clean_and_structurally_complete(self) -> None:
        result = analyze_report_template(RUNTIME, source_role="synthetic_fixture")
        self.assertEqual(result.document.table_count, 55)
        self.assertEqual(result.document.section_count, 17)
        self.assertEqual(result.document.comment_count, 0)
        self.assertEqual(result.document.revision_count, 0)
        self.assertFalse(any(result.flags.model_dump().values()))
        from docx import Document
        reopened = Document(RUNTIME)
        self.assertEqual((len(reopened.tables), len(reopened.sections)), (55, 17))

    def test_runtime_template_has_no_active_italic_formatting(self) -> None:
        disabled_word_italics = 0
        with zipfile.ZipFile(RUNTIME) as package:
            for name in package.namelist():
                if not name.startswith("word/") or not name.endswith(".xml"):
                    continue
                root = etree.fromstring(package.read(name))
                word_italics = root.xpath(
                    f"//w:i[not(ancestor::*[namespace-uri()='{M}'])] | "
                    f"//w:iCs[not(ancestor::*[namespace-uri()='{M}'])]",
                    namespaces=NS,
                )
                disabled_word_italics += len(word_italics)
                self.assertTrue(
                    all(node.get(f"{{{W}}}val") == "0" for node in word_italics),
                    name,
                )
                drawing_italics = [
                    node
                    for node in root.xpath(
                        f"//*[@i][not(ancestor::*[namespace-uri()='{M}'])]",
                        namespaces=NS,
                    )
                    if etree.QName(node).namespace == A
                ]
                self.assertTrue(all(node.get("i") == "0" for node in drawing_italics), name)
                for styled in root.xpath(
                    f"//*[@style][not(ancestor::*[namespace-uri()='{M}'])]",
                    namespaces=NS,
                ):
                    self.assertNotRegex(styled.get("style", ""), r"(?i)font-style\s*:\s*italic")
        self.assertGreater(disabled_word_italics, 0)

    def test_math_formula_italic_formatting_is_preserved(self) -> None:
        source = (
            f'<m:oMath xmlns:m="{M}" xmlns:w="{W}">'
            '<m:r><w:rPr><w:i/><w:iCs/></w:rPr><m:t>i</m:t></m:r>'
            '<m:r><m:rPr><m:sty m:val="bi"/></m:rPr><m:t>S</m:t></m:r>'
            '<m:ctrlPr><w:rPr><w:i/><w:iCs/></w:rPr></m:ctrlPr>'
            '</m:oMath>'
        ).encode("utf-8")
        root = etree.fromstring(_disable_all_italics(source))
        self.assertEqual(
            root.xpath("//m:r/m:rPr/m:sty/@m:val", namespaces=NS),
            ["bi"],
        )
        self.assertEqual(len(root.xpath("//m:oMath//w:i", namespaces=NS)), 2)
        self.assertEqual(len(root.xpath("//m:oMath//w:iCs", namespaces=NS)), 2)

    def test_runtime_math_formula_formatting_matches_the_approved_source_baseline(self) -> None:
        formatting_hash = hashlib.sha256()
        formatting_node_count = 0
        with zipfile.ZipFile(RUNTIME) as package:
            for name in sorted(
                part
                for part in package.namelist()
                if part.startswith("word/") and part.endswith(".xml")
            ):
                root = etree.fromstring(package.read(name))
                for properties in root.xpath(
                    "//m:oMath//m:rPr | //m:oMath//m:ctrlPr | //m:oMath//w:rPr",
                    namespaces=NS,
                ):
                    formatting_hash.update(
                        etree.tostring(
                            properties,
                            method="c14n",
                            exclusive=True,
                            with_comments=False,
                        )
                    )
                    formatting_node_count += 1
        self.assertEqual(formatting_node_count, 269)
        self.assertEqual(
            formatting_hash.hexdigest(),
            "3915aaed1fac98c5e19fcee184af183ab8be1b736b35d06ec5b6a7db0d474e10",
        )

    def test_runtime_package_has_no_private_or_executable_parts(self) -> None:
        with zipfile.ZipFile(RUNTIME) as package:
            names = set(package.namelist())
            self.assertFalse(any(name.startswith(("customXml/", "word/glossary/", "word/embeddings/", "word/activeX/")) for name in names))
            self.assertEqual(sorted(name for name in names if name.startswith("word/media/")), ["word/media/image1.emf"])
            self.assertEqual(
                hashlib.sha256(package.read("word/media/image1.emf")).hexdigest(),
                "008976a91115718e266c4dffcf3985fe92d2ee00063eac1fc42be592100d2a86",
            )
            self.assertFalse(any("comments" in name.lower() or "people" in name.lower() for name in names))
            payload = b"\n".join(package.read(name) for name in names if name.endswith((".xml", ".rels"))).decode("utf-8", errors="ignore")
            story_text = ""
            for name in names:
                if name == "word/document.xml" or re.fullmatch(r"word/(header|footer)\d+\.xml", name) or name in {"word/footnotes.xml", "word/endnotes.xml"}:
                    story = etree.fromstring(package.read(name))
                    story_text += "".join(story.xpath("//w:t/text()", namespaces=NS))
        for forbidden in ("TargetMode=\"External\"",):
            self.assertNotIn(forbidden, payload)
        self.assertNotIn("示例", story_text)
        self.assertNotRegex(story_text, r"\{[^{}]*\}|(?<![A-Za-z])X{1,20}(?![A-Za-z])")
        self.assertNotIn("RaRk", story_text)
        self.assertIn("中互金认证有限公司", story_text)
        approved_fixed_values = {
            "天津自贸试验区（中心商务区）新华路3678号宝风大厦28层2802": 1,
            "300450": 1,
            "李文宝": 1,
            "商务经理": 1,
            "业务部": 1,
            "010-88720451": 1,
            "15201294794": 1,
            "liwb@secallab.com": 1,
        }
        for approved_fixed_value, expected_count in approved_fixed_values.items():
            self.assertEqual(story_text.count(approved_fixed_value), expected_count)
        with zipfile.ZipFile(RUNTIME) as package:
            document = etree.fromstring(package.read("word/document.xml"))
        self.assertFalse(document.xpath("//w:showingPlcHdr | //w:placeholder", namespaces=NS))
        self.assertFalse(
            document.xpath(
                "//w:listItem[@w:value='选择一项。' or @w:displayText='选择一项。']",
                namespaces=NS,
            )
        )
        for dropdown in document.xpath("//w:dropDownList", namespaces=NS):
            first = dropdown.find(f"{{{W}}}listItem")
            self.assertIsNotNone(first)
            self.assertEqual(first.get(f"{{{W}}}displayText", ""), " ")
            self.assertEqual(first.get(f"{{{W}}}value", ""), " ")

    def test_all_checkbox_controls_keep_a_visible_state_glyph(self) -> None:
        with zipfile.ZipFile(RUNTIME) as package:
            document = etree.fromstring(package.read("word/document.xml"))
        checkboxes = document.xpath("//w:sdt[w:sdtPr/w14:checkbox]", namespaces=NS)
        self.assertEqual(len(checkboxes), 149)
        for checkbox in checkboxes:
            self.assertTrue(
                checkbox.xpath("./w:sdtContent//w:sym | ./w:sdtContent//w:t[string-length(.) > 0]", namespaces=NS)
            )

    def test_sdt_identity_elements_follow_word_schema_order(self) -> None:
        with zipfile.ZipFile(RUNTIME) as package:
            document = etree.fromstring(package.read("word/document.xml"))
        for properties in document.xpath("//w:sdtPr[w:tag and w:alias]", namespaces=NS):
            children = [etree.QName(child).localname for child in properties]
            self.assertLess(children.index("alias"), children.index("tag"))
            type_positions = [
                index
                for index, child in enumerate(properties)
                if etree.QName(child).namespace != W
                or etree.QName(child).localname
                in ("comboBox", "date", "docPartObj", "docPartList", "dropDownList", "picture", "richText", "text")
            ]
            if type_positions:
                self.assertLess(children.index("tag"), min(type_positions))

    def test_headers_footers_and_page_fields_follow_the_approved_template(self) -> None:
        with zipfile.ZipFile(RUNTIME) as package:
            expected_headers = {
                "word/header1.xml": ("【报告编号】", ["report.header.report_number"]),
                "word/header4.xml": (
                    "【被测系统名称】商用密码应用安全性评估报告",
                    ["report.header.system_name.1"],
                ),
                "word/header5.xml": ("【测评机构名称】", ["report.header.assessment_name.1"]),
                "word/header6.xml": (
                    "【被测系统名称】商用密码应用安全性评估报告",
                    ["report.header.system_name.2"],
                ),
                "word/header7.xml": ("【测评机构名称】", ["report.header.assessment_name.2"]),
                "word/header8.xml": (
                    "【被测系统名称】商用密码应用安全性评估报告",
                    ["report.header.system_name.3"],
                ),
            }
            for part_name, (expected_text, expected_tags) in expected_headers.items():
                header = etree.fromstring(package.read(part_name))
                self.assertEqual("".join(header.xpath("//w:t/text()", namespaces=NS)), expected_text)
                self.assertEqual(header.xpath("//w:sdtPr/w:tag/@w:val", namespaces=NS), expected_tags)

            footer_page_fields = 0
            for part_name in sorted(
                name for name in package.namelist() if re.fullmatch(r"word/footer\d+\.xml", name)
            ):
                footer = etree.fromstring(package.read(part_name))
                footer_page_fields += sum(
                    "PAGE" in instruction.upper()
                    for instruction in footer.xpath("//w:instrText/text() | //w:fldSimple/@w:instr", namespaces=NS)
                )
                for field_begin in footer.xpath("//w:fldChar[@w:fldCharType='begin']", namespaces=NS):
                    self.assertEqual(field_begin.get(f"{{{W}}}dirty"), "true")
            self.assertGreaterEqual(footer_page_fields, 7)
            for part_name in ("word/footer6.xml", "word/footer7.xml"):
                footer = etree.fromstring(package.read(part_name))
                footer_text = "".join(footer.xpath("//w:t/text()", namespaces=NS))
                instructions = [
                    instruction.strip()
                    for instruction in footer.xpath("//w:instrText/text()", namespaces=NS)
                ]
                self.assertEqual(footer_text, "第1页/共1页")
                self.assertEqual(instructions, ["PAGE", "PAGEREF report_body_end \\h"])
                self.assertNotIn("61", footer_text)

            settings = etree.fromstring(package.read("word/settings.xml"))
            self.assertEqual(settings.xpath("//w:updateFields/@w:val", namespaces=NS), ["true"])
            document = etree.fromstring(package.read("word/document.xml"))
            self.assertEqual(
                document.xpath("//w:bookmarkStart[@w:name='report_body_end']/@w:name", namespaces=NS),
                ["report_body_end"],
            )
            self.assertTrue(document.xpath("//w:instrText[contains(., 'TOC ')]", namespaces=NS))
            all_instructions = []
            for part_name in package.namelist():
                if (
                    part_name == "word/document.xml"
                    or re.fullmatch(r"word/(header|footer)\d+\.xml", part_name)
                    or part_name in {"word/footnotes.xml", "word/endnotes.xml"}
                ):
                    story = etree.fromstring(package.read(part_name))
                    all_instructions.extend(
                        story.xpath("//w:instrText/text() | //w:fldSimple/@w:instr", namespaces=NS)
                    )
            self.assertFalse(any("NUMPAGES" in instruction.upper() for instruction in all_instructions))
            for field_begin in document.xpath("//w:fldChar[@w:fldCharType='begin']", namespaces=NS):
                self.assertEqual(field_begin.get(f"{{{W}}}dirty"), "true")

    def test_word_refresh_script_updates_all_stories_and_toc_page_numbers(self) -> None:
        script = (ROOT / "scripts" / "update_word_report_fields.ps1").read_text(encoding="utf-8")
        for required in (
            ".StoryRanges",
            ".TablesOfContents",
            ".UpdatePageNumbers()",
            ".Repaginate()",
            ".Fields.Update()",
        ):
            self.assertIn(required, script)

    def test_every_control_and_table_has_a_stable_identifier(self) -> None:
        with zipfile.ZipFile(RUNTIME) as package:
            document = etree.fromstring(package.read("word/document.xml"))
            story_roots = [document]
            for name in package.namelist():
                if re.fullmatch(r"word/(header|footer)\d+\.xml", name) or name in {
                    "word/footnotes.xml",
                    "word/endnotes.xml",
                }:
                    story_roots.append(etree.fromstring(package.read(name)))
        tags = document.xpath("//w:sdtPr/w:tag/@w:val", namespaces=NS)
        all_tags = [
            tag
            for story in story_roots
            for tag in story.xpath("//w:sdtPr/w:tag/@w:val", namespaces=NS)
        ]
        bookmarks = document.xpath("//w:bookmarkStart[starts-with(@w:name, 'rt_table_')]/@w:name", namespaces=NS)
        self.assertEqual(len(tags), 606)
        self.assertEqual(len(tags), len(set(tags)))
        self.assertEqual(len(all_tags), 612)
        self.assertEqual(len(all_tags), len(set(all_tags)))
        self.assertEqual(bookmarks, [f"rt_table_{index:03d}" for index in range(1, 56)])
        semantic = [tag for tag in all_tags if tag.startswith("report.")]
        self.assertEqual(len(semantic), 29)
        self.assertEqual(len(semantic), len(set(semantic)))
        self.assertNotIn("report.identity.version", semantic)
        self.assertIn("report.cover.system_name", semantic)
        self.assertIn("report.risk.high_risk_judgement", semantic)

        visible_text = "".join(document.xpath("//w:t/text()", namespaces=NS))
        self.assertNotIn("报告版本", visible_text)
        self.assertEqual(
            "".join(document.xpath("/w:document/w:body/w:p[4]//w:t/text()", namespaces=NS)),
            "【被测系统名称】",
        )
        self.assertEqual(
            document.xpath("/w:document/w:body/w:p[4]//w:sdtPr/w:tag/@w:val", namespaces=NS),
            ["report.cover.system_name"],
        )
        self.assertEqual(
            "".join(document.xpath("/w:document/w:body/w:p[5]//w:t/text()", namespaces=NS)),
            "商用密码应用安全性评估报告",
        )
        self.assertIn("本报告是被测信息系统名称的商用密码应用安全性评估报告，报告模板为2023年版。", visible_text)
        self.assertIn("测评机构名称", visible_text)
        self.assertIn("年   月   日", visible_text)
        self.assertNotIn("（简要描述测评范围和主要内容。建议不超过200字。）", visible_text)
        self.assertIn(
            "受【被测单位】委托，中互金认证有限公司于【开始日期】至【结束日期】",
            visible_text,
        )
        self.assertIn("评估已完成41项测评项的测评工作", visible_text)
        self.assertIn("风险分析发现被测系统存在【风险问题】。", visible_text)
        summary_paragraph = document.xpath(
            "/w:document/w:body/w:tbl[3]/w:tr[3]/w:tc[2]/w:p",
            namespaces=NS,
        )[0]
        indentation = summary_paragraph.find(f"{{{W}}}pPr/{{{W}}}ind")
        self.assertIsNotNone(indentation)
        self.assertEqual(indentation.get(f"{{{W}}}firstLineChars"), "200")
        self.assertEqual(indentation.get(f"{{{W}}}firstLine"), "420")
        for italic in summary_paragraph.xpath("./w:pPr/w:rPr/w:i | ./w:pPr/w:rPr/w:iCs | .//w:r/w:rPr/w:i | .//w:r/w:rPr/w:iCs", namespaces=NS):
            self.assertEqual(italic.get(f"{{{W}}}val"), "0")
        body_paragraphs = document.xpath("/w:document/w:body/w:p", namespaces=NS)
        self.assertIn("选取的测评指标总数为41项", "".join(body_paragraphs[48].xpath(".//w:t/text()", namespaces=NS)))
        layers = (
            "物理和环境安全",
            "网络和通信安全",
            "设备和计算安全",
            "应用和数据安全",
            "管理制度",
            "人员管理",
            "建设运行",
            "应急处置",
        )
        expected_result = (
            "测评结果：符合项【符合项数量】项，部分符合项【部分符合项数量】项，"
            "不符合项【不符合项数量】项，不适用项【不适用项数量】项。"
        )
        for layer_index, layer_name in enumerate(layers):
            layer_paragraph = body_paragraphs[49 + layer_index * 2]
            result_paragraph = body_paragraphs[50 + layer_index * 2]
            self.assertEqual("".join(layer_paragraph.xpath(".//w:t/text()", namespaces=NS)), f"在{layer_name}方面，【情况描述】。")
            self.assertTrue(layer_paragraph.xpath("./w:pPr/w:numPr", namespaces=NS))
            self.assertEqual("".join(result_paragraph.xpath(".//w:t/text()", namespaces=NS)), expected_result)
        self.assertIn(
            "通过对【被测系统】的物理和环境安全、网络和通信安全、设备和计算安全、应用和数据安全",
            "".join(body_paragraphs[65].xpath(".//w:t/text()", namespaces=NS)),
        )
        self.assertEqual(
            "".join(body_paragraphs[67].xpath(".//w:t/text()", namespaces=NS)),
            "本次信息系统商用密码应用安全性评估依据GB/T 39786—2021《信息安全技术 信息系统密码应用基本要求》"
            "的第三级别要求，发现被测信息系统存在以下安全问题。建议被测信息系统根据实际情况和以下给出的建议进行整改。",
        )
        issue_layers = {
            68: ((70, 73), (74, 76)),
            76: ((78, 82), (83, 85)),
            85: ((87, 92), (93, 98)),
            98: ((100, 107), (108, 114)),
            114: ((116, 122), (123, 129)),
            129: ((131, 135), (136, 140)),
            140: ((142, 145), (146, 149)),
            149: ((151, 152), (153, 154)),
        }
        for heading_index, (problem_range, recommendation_range) in issue_layers.items():
            self.assertTrue(body_paragraphs[heading_index].xpath("./w:pPr/w:numPr", namespaces=NS))
            self.assertEqual("".join(body_paragraphs[heading_index + 1].xpath(".//w:t/text()", namespaces=NS)), "问题描述：")
            for paragraph in body_paragraphs[problem_range[0]:problem_range[1]]:
                self.assertEqual("".join(paragraph.xpath(".//w:t/text()", namespaces=NS)), "")
                self.assertFalse(paragraph.xpath("./w:pPr/w:numPr", namespaces=NS))
            recommendation_label_index = problem_range[1]
            self.assertEqual("".join(body_paragraphs[recommendation_label_index].xpath(".//w:t/text()", namespaces=NS)), "改进建议：")
            for paragraph in body_paragraphs[recommendation_range[0]:recommendation_range[1]]:
                self.assertEqual("".join(paragraph.xpath(".//w:t/text()", namespaces=NS)), "")
                self.assertFalse(paragraph.xpath("./w:pPr/w:numPr", namespaces=NS))
        for paragraph in body_paragraphs[67:154]:
            italics = paragraph.xpath(
                "./w:pPr/w:rPr/w:i | ./w:pPr/w:rPr/w:iCs | .//w:r/w:rPr/w:i | .//w:r/w:rPr/w:iCs",
                namespaces=NS,
            )
            self.assertTrue(italics)
            self.assertTrue(all(italic.get(f"{{{W}}}val") == "0" for italic in italics))
        self.assertIn(
            "中互金认证有限公司受【被测单位】委托，于【测评开始日期】至【测评结束日期】",
            "".join(body_paragraphs[158].xpath(".//w:t/text()", namespaces=NS)),
        )
        expected_reference_standards = (
            "GB/T 43206—2023《信息安全技术 信息系统密码应用测评要求》",
            "GB/T 43207—2023《信息安全技术 信息系统密码应用设计指南》",
            "GM/T 0116—2021《信息系统密码应用测评过程指南》",
            "《信息系统密码应用高风险判定指引》",
            "《商用密码应用安全性评估量化评估规则》",
        )
        self.assertEqual(
            tuple("".join(body_paragraphs[index].xpath(".//w:t/text()", namespaces=NS)) for index in range(163, 168)),
            expected_reference_standards,
        )
        for index in (168, 169):
            self.assertEqual("".join(body_paragraphs[index].xpath(".//w:t/text()", namespaces=NS)), "")
            self.assertFalse(body_paragraphs[index].xpath("./w:pPr/w:numPr", namespaces=NS))
        self.assertTrue(
            body_paragraphs[168].xpath(
                ".//w:bookmarkStart[@w:name='report_additional_reference_standards']",
                namespaces=NS,
            )
        )
        workflow_picture = body_paragraphs[174].xpath(".//w:pict/v:shape/v:imagedata[@r:id='rId25']", namespaces=NS)
        self.assertEqual(len(workflow_picture), 1)
        self.assertFalse(body_paragraphs[174].xpath(".//w:object | .//o:OLEObject", namespaces=NS))
        time_slots = {
            178: ("测评准备阶段时间：", "report.assessment.preparation_period"),
            183: ("方案编制阶段时间：", "report.assessment.plan_period"),
            187: ("现场测评阶段时间：", "report.assessment.period"),
            191: ("分析与报告编制阶段时间：", "report.assessment.report_period"),
        }
        for paragraph_index, (label, tag) in time_slots.items():
            paragraph = body_paragraphs[paragraph_index]
            self.assertEqual("".join(paragraph.xpath(".//w:t/text()", namespaces=NS)), label + "【开始日期】至【结束日期】")
            self.assertEqual(paragraph.xpath(".//w:sdtPr/w:tag/@w:val", namespaces=NS), [tag])
        distribution_text = "".join(body_paragraphs[193].xpath(".//w:t/text()", namespaces=NS))
        self.assertEqual(
            distribution_text,
            "本报告一式【总份数】份，其中【密码管理部门份数】份提交密码管理部门，"
            "【委托单位份数】份提交委托单位，【密评机构留存份数】份由密评机构留存。",
        )
        self.assertEqual(
            body_paragraphs[193].xpath(".//w:sdtPr/w:tag/@w:val", namespaces=NS),
            [
                "report.distribution.total_copies",
                "report.distribution.regulator_copies",
                "report.distribution.client_copies",
                "report.distribution.assessment_copies",
            ],
        )
        risk_method_text = "".join(body_paragraphs[356].xpath(".//w:t/text()", namespaces=NS))
        self.assertTrue(risk_method_text.startswith("具体地，根据威胁类型和威胁发生频率"))
        risk_summary = body_paragraphs[357]
        risk_summary_text = "".join(risk_summary.xpath(".//w:t/text()", namespaces=NS))
        self.assertIn(
            "根据《商用密码应用安全性评估高风险判定指引》【高风险判定】。"
            "经风险分析，系统存在高风险【高风险项数量】项，中风险【中风险项数量】项，"
            "低风险【低风险项数量】项，具体见表61：",
            risk_summary_text,
        )
        self.assertNotIn("判定系统是否存在高风险", risk_summary_text)
        self.assertEqual(
            risk_summary.xpath(".//w:sdtPr/w:tag/@w:val", namespaces=NS),
            ["report.risk.high_risk_judgement"],
        )
        self.assertNotRegex(risk_summary_text, r"[{}]|(?<![A-Za-z])X{1,20}(?![A-Za-z])")
        self.assertEqual(
            risk_summary.xpath(".//w:instrText[contains(., 'REF _Ref54276704')]/text()", namespaces=NS),
            [" REF _Ref54276704 \\h  \\* MERGEFORMAT "],
        )
        conclusion = body_paragraphs[361]
        conclusion_text = "".join(conclusion.xpath(".//w:t/text()", namespaces=NS))
        self.assertEqual(
            conclusion_text,
            "通过对【被测单位】的【被测系统】的物理和环境安全、网络和通信安全、设备和计算安全、"
            "应用和数据安全、管理制度、人员管理、建设运行和应急处置等方面的测评，该系统综合得分为"
            "【综合得分】分，系统密码应用面临【风险等级】风险，【符合/基本符合/不符合】"
            "GB/T 39786—2021《信息安全技术 信息系统密码应用基本要求》的第三级别要求。",
        )
        self.assertNotRegex(conclusion_text, r"[{}]|(?<![A-Za-z])X{1,20}(?![A-Za-z])")
        self.assertEqual(conclusion.xpath(".//w:footnoteReference/@w:id", namespaces=NS), ["5"])
        for paragraph in (risk_summary, conclusion):
            italics = paragraph.xpath(
                "./w:pPr/w:rPr/w:i | ./w:pPr/w:rPr/w:iCs | .//w:r/w:rPr/w:i | .//w:r/w:rPr/w:iCs",
                namespaces=NS,
            )
            self.assertTrue(italics)
            self.assertTrue(all(italic.get(f"{{{W}}}val") == "0" for italic in italics))
        starts = document.xpath("//w:bookmarkStart[starts-with(@w:name, 'block_table_') and contains(@w:name, '_start')]/@w:name", namespaces=NS)
        ends = document.xpath("//w:bookmarkStart[starts-with(@w:name, 'block_table_') and contains(@w:name, '_end')]/@w:name", namespaces=NS)
        self.assertEqual(len(starts), 55)
        self.assertEqual(len(ends), 55)

    def test_word_native_acceptance_uses_exact_frozen_count(self) -> None:
        script = (ROOT / "scripts" / "test_word_report_template.ps1").read_text(encoding="utf-8")
        self.assertIn("$document.ContentControls.Count -ne 605", script)
        self.assertIn("OpenNoRepairDialog", script)
        self.assertIn("roundtrip_saved_and_reopened", script)
        self.assertIn("runtime_template_sha256", script)
        self.assertNotIn("$word.DisplayAlerts = 0", script)
        self.assertNotIn("expected_at_least", script)

    def test_semantic_controls_write_into_the_actual_placeholder(self) -> None:
        with zipfile.ZipFile(RUNTIME) as package:
            document = etree.fromstring(package.read("word/document.xml"))
        values = {
            "report.identity.date": "2026-07-15",
            "report.system.security_level": "三级",
        }
        for tag, value in values.items():
            controls = document.xpath(f"//w:sdt[w:sdtPr/w:tag[@w:val='{tag}']]", namespaces=NS)
            self.assertEqual(len(controls), 1)
            texts = controls[0].xpath(".//w:t", namespaces=NS)
            self.assertTrue(texts)
            texts[0].text = value
        date_control = document.xpath("//w:sdt[w:sdtPr/w:tag[@w:val='report.identity.date']]", namespaces=NS)[0]
        date_paragraph = date_control.xpath("ancestor::w:p[1]", namespaces=NS)[0]
        self.assertEqual("".join(date_paragraph.xpath(".//w:t/text()", namespaces=NS)), "2026-07-15")
        level_control = document.xpath("//w:sdt[w:sdtPr/w:tag[@w:val='report.system.security_level']]", namespaces=NS)[0]
        level_paragraph = level_control.xpath("ancestor::w:p[1]", namespaces=NS)[0]
        level_text = "".join(level_paragraph.xpath(".//w:t/text()", namespaces=NS))
        self.assertIn("已定级备案，第三级（一至四）", level_text)
        self.assertNotIn("第   级", level_text)

    def test_a7_conformance_column_is_not_merged_across_objects(self) -> None:
        with zipfile.ZipFile(RUNTIME) as package:
            document = etree.fromstring(package.read("word/document.xml"))
        table = document.xpath("/w:document/w:body/w:tbl", namespaces=NS)[44]
        for row in table.xpath("./w:tr[position()>1]", namespaces=NS):
            cells = row.xpath("./w:tc | ./w:sdt/w:sdtContent/w:tc", namespaces=NS)
            self.assertGreaterEqual(len(cells), 4)
            self.assertFalse(cells[3].xpath("./w:tcPr/w:vMerge", namespaces=NS))

    def test_source_and_output_paths_must_be_distinct(self) -> None:
        with self.assertRaisesRegex(ValueError, "OUTPUT_MUST_NOT_OVERWRITE_SOURCE"):
            ensure_distinct_paths(RUNTIME, RUNTIME)


if __name__ == "__main__":
    unittest.main()
