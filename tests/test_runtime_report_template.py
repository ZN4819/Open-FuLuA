from __future__ import annotations

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
from scripts.build_runtime_report_template import _scrub_story

RUNTIME = ROOT / "templates" / "report" / "2023-2025.12.08" / "runtime_template.docx"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}


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

    def test_runtime_package_has_no_private_or_executable_parts(self) -> None:
        with zipfile.ZipFile(RUNTIME) as package:
            names = set(package.namelist())
            self.assertFalse(any(name.startswith(("customXml/", "word/glossary/", "word/embeddings/", "word/media/", "word/activeX/")) for name in names))
            self.assertFalse(any("comments" in name.lower() or "people" in name.lower() for name in names))
            payload = b"\n".join(package.read(name) for name in names if name.endswith((".xml", ".rels"))).decode("utf-8", errors="ignore")
            story_text = ""
            for name in names:
                if name == "word/document.xml" or re.fullmatch(r"word/(header|footer)\d+\.xml", name) or name in {"word/footnotes.xml", "word/endnotes.xml"}:
                    story = etree.fromstring(package.read(name))
                    story_text += "".join(story.xpath("//w:t/text()", namespaces=NS))
        for forbidden in ("中互金认证有限公司", "liwb@", "15201294794", "TargetMode=\"External\""):
            self.assertNotIn(forbidden, payload)
        self.assertNotIn("示例", story_text)
        self.assertNotRegex(story_text, r"\{[^{}]*\}|(?<![A-Za-z])X{1,20}(?![A-Za-z])")
        self.assertNotIn("RaRk", story_text)
        self.assertNotIn("中互金认证有限公司", story_text)
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

    def test_every_control_and_table_has_a_stable_identifier(self) -> None:
        with zipfile.ZipFile(RUNTIME) as package:
            document = etree.fromstring(package.read("word/document.xml"))
        tags = document.xpath("//w:sdtPr/w:tag/@w:val", namespaces=NS)
        bookmarks = document.xpath("//w:bookmarkStart[starts-with(@w:name, 'rt_table_')]/@w:name", namespaces=NS)
        self.assertEqual(len(tags), 596)
        self.assertEqual(len(tags), len(set(tags)))
        self.assertEqual(bookmarks, [f"rt_table_{index:03d}" for index in range(1, 56)])
        semantic = document.xpath("//w:sdtPr/w:tag[starts-with(@w:val, 'report.')]/@w:val", namespaces=NS)
        self.assertEqual(len(semantic), 13)
        self.assertEqual(len(semantic), len(set(semantic)))
        starts = document.xpath("//w:bookmarkStart[starts-with(@w:name, 'block_table_') and contains(@w:name, '_start')]/@w:name", namespaces=NS)
        ends = document.xpath("//w:bookmarkStart[starts-with(@w:name, 'block_table_') and contains(@w:name, '_end')]/@w:name", namespaces=NS)
        self.assertEqual(len(starts), 55)
        self.assertEqual(len(ends), 55)

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
