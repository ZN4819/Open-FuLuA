import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import database  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.docx_analyzer import analyze_docx  # noqa: E402
from app.services.docx_generator import generate_project_docx  # noqa: E402


NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
}


class DocxGeneratorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.original_storage_path = settings.storage_path
        object.__setattr__(settings, "storage_path", self.root / "storage")
        os.environ["FULUA_DATABASE_PATH"] = str(self.root / "test.db")
        database.init_db()

    def tearDown(self) -> None:
        os.environ.pop("FULUA_DATABASE_PATH", None)
        object.__setattr__(settings, "storage_path", self.original_storage_path)

    def test_editable_docx_contains_sections_tables_fields_controls_and_image(self) -> None:
        project = self._make_project_with_content()

        path = generate_project_docx(project["id"], "editable")
        analysis = analyze_docx(path)

        self.assertEqual(analysis.sections, 8)
        self.assertEqual(analysis.tables, 8)
        self.assertGreaterEqual(analysis.dropdown_controls, 4)
        self.assertGreaterEqual(analysis.seq_fields, 9)
        self.assertEqual(analysis.ref_fields, 1)
        self.assertEqual(analysis.images, 1)
        self.assertEqual(analysis.missing_ref_targets, [])
        self.assertIn("4x8", analysis.table_shapes)
        self.assertTrue(_all_tables_have_grid(path))
        paragraph_texts = _nonempty_paragraph_texts(path)
        self.assertEqual(paragraph_texts[:3], ["附录A测评结果记录", "物理和环境安全", "表A-1物理和环境安全测评结果记录"])
        self.assertNotIn("A-1 物理和环境安全", paragraph_texts[:3])
        self.assertTrue(_first_table_uses_template_header(path))
        self.assertEqual(
            _first_table_border_sizes(path),
            {"top": "18", "left": "18", "bottom": "18", "right": "18", "insideH": "4", "insideV": "4"},
        )
        self.assertEqual(
            _first_table_unit_column_format(path),
            {
                "text": "身份鉴别",
                "fills": ["E7E6E6", "E7E6E6"],
                "alignment": "center",
                "vertical_alignment": "center",
                "bold": "true",
                "bold_cs": "true",
            },
        )
        self.assertEqual(
            _unit_score_header_formulas(path),
            {"technical": "Si,j=1≤k≤ni,jSi,j,kni,j", "management": "Si,j"},
        )
        self.assertEqual(
            _first_table_unit_score_column(path),
            {
                "text": "0.5000",
                "first_vmerge": "restart",
                "second_vmerge": "continue",
                "second_text": "",
            },
        )
        self.assertEqual(
            _first_figure_caption_format(path),
            {
                "paragraph": {
                    "alignment": "center",
                    "spacing_after": "200",
                    "line": "276",
                },
                "runs": [
                    {
                        "ascii": "Cambria",
                        "hAnsi": "Cambria",
                        "eastAsia": "宋体",
                        "cs": "Times New Roman",
                        "bold": "false",
                        "bold_cs": "false",
                        "caps": "true",
                        "spacing": "10",
                        "kern": "0",
                        "size": "18",
                        "size_cs": "18",
                    }
                ],
                "instruction": "SEQ 图A-1- \\* ARABIC",
            },
        )
        self.assertIn("A1.row1.D", _dropdown_tags(path))
        self.assertIn("A5.row1.compliance", _dropdown_tags(path))

    def test_evidence_images_are_scaled_to_page_and_kept_with_captions(self) -> None:
        project = self._make_project_with_content()
        self._create_evidence_image(
            project["id"],
            "A-1",
            filename="portrait.png",
            caption="纵向截图",
            size=(300, 900),
            dpi=(72, 72),
            display_width_in=0.4,
            display_height_in=1.2,
        )

        path = generate_project_docx(project["id"], "editable")
        layouts = _figure_layouts(path)

        self.assertEqual(len(layouts), 2)
        self.assertAlmostEqual(layouts[0]["width_in"], 9.69, places=2)
        self.assertGreater(layouts[0]["height_in"], 4.8)
        self.assertLess(layouts[0]["height_in"], 4.9)
        self.assertGreater(layouts[1]["height_in"], 5.0)
        self.assertLess(layouts[1]["width_in"], 2.0)
        for layout in layouts:
            self.assertTrue(layout["page_break_before"])
            self.assertTrue(layout["keep_next"])
            self.assertTrue(layout["keep_lines"])
            self.assertTrue(layout["caption_keep_lines"])
            self.assertFalse(layout["caption_page_break_before"])
            self.assertIn("图A-1-", layout["caption_text"])

    def test_final_docx_flattens_content_controls_but_keeps_references(self) -> None:
        project = self._make_project_with_content()

        path = generate_project_docx(project["id"], "final")
        analysis = analyze_docx(path)

        self.assertEqual(analysis.sections, 8)
        self.assertEqual(analysis.tables, 8)
        self.assertEqual(analysis.dropdown_controls, 0)
        self.assertEqual(analysis.ref_fields, 1)
        self.assertEqual(analysis.missing_ref_targets, [])

    def _make_project_with_content(self):
        project = database.create_project("DOCX 生成测试")
        image = self._create_evidence_image(project["id"], "A-1")
        database.replace_section_rows(
            project_id=project["id"],
            code="A-1",
            rows=[
                {
                    "unit": "身份鉴别",
                    "object_name": "服务器",
                    "record_text": f"查看登录策略，见 [[FIG:{image['id']}]]。",
                    "metric_result": {
                        "d": "√",
                        "a": "√",
                        "k": "/",
                        "object_score": "1.0000",
                        "unit_score": "1.0000",
                    },
                    "cross_references": [
                        {
                            "target_image_id": image["id"],
                            "token": f"[[FIG:{image['id']}]]",
                            "display_text": "图A-1-1",
                        }
                    ],
                },
                {
                    "unit": "身份鉴别",
                    "object_name": "备用服务器",
                    "record_text": "查看备用登录策略。",
                    "metric_result": {
                        "d": "√",
                        "a": "√",
                        "k": "/",
                        "object_score": "0.0000",
                        "unit_score": "9.9999",
                    },
                },
            ],
        )
        database.replace_section_rows(
            project_id=project["id"],
            code="A-5",
            rows=[
                {
                    "unit": "管理制度",
                    "object_name": "制度文件",
                    "record_text": "制度文件已建立并发布。",
                    "metric_result": {
                        "compliance": "符合",
                        "unit_score": "1.0000",
                    },
                }
            ],
        )
        return project

    def _create_evidence_image(
        self,
        project_id: int,
        section_code: str,
        filename: str = "sample.png",
        caption: str = "登录策略截图",
        size: tuple[int, int] = (600, 300),
        dpi: tuple[int, int] = (150, 150),
        display_width_in: float = 4,
        display_height_in: float = 2,
    ):
        relative_path = Path("uploads") / str(project_id) / section_code / filename
        absolute_path = settings.storage_path / relative_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", size, color=(255, 255, 255))
        image.save(absolute_path, dpi=dpi)
        return database.create_evidence_image(
            project_id,
            section_code,
            {
                "file_path": relative_path.as_posix(),
                "original_name": filename,
                "caption": caption,
                "alt_text": caption,
                "pixel_width": size[0],
                "pixel_height": size[1],
                "dpi_x": dpi[0],
                "dpi_y": dpi[1],
                "display_width_in": display_width_in,
                "display_height_in": display_height_in,
            },
        )


def _all_tables_have_grid(path: Path) -> bool:
    with zipfile.ZipFile(path) as package:
        document = ET.fromstring(package.read("word/document.xml"))
    for table in document.findall(".//w:tbl", NS):
        grid = table.find("w:tblGrid", NS)
        if grid is None or not grid.findall("w:gridCol", NS):
            return False
    return True


def _nonempty_paragraph_texts(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as package:
        document = ET.fromstring(package.read("word/document.xml"))
    texts: list[str] = []
    for paragraph in document.findall(".//w:p", NS):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", NS)).strip()
        if text:
            texts.append(text)
    return texts


def _first_table_uses_template_header(path: Path) -> bool:
    with zipfile.ZipFile(path) as package:
        document = ET.fromstring(package.read("word/document.xml"))
    table = document.find(".//w:tbl", NS)
    if table is None:
        return False
    rows = table.findall("w:tr", NS)
    if len(rows) < 2:
        return False
    first_row = [_cell_text(cell) for cell in rows[0].findall("w:tc", NS)]
    second_row = [_cell_text(cell) for cell in rows[1].findall("w:tc", NS)]
    first_spans = [cell.find("w:tcPr/w:gridSpan", NS) for cell in rows[0].findall("w:tc", NS)]
    return (
        first_row == ["测评单元", "测评对象", "结果记录", "量化指标", "测评单元得分"]
        and second_row[3:7] == ["密码使用有效性D", "密码算法/技术合规性A", "密钥管理安全K", "测评对象评分Si,j,k"]
        and first_spans[3] is not None
        and first_spans[3].get(f"{{{NS['w']}}}val") == "4"
    )


def _cell_text(cell: ET.Element) -> str:
    return "".join(node.text or "" for node in cell.findall(".//w:t", NS)).strip()


def _first_table_border_sizes(path: Path) -> dict[str, str | None]:
    with zipfile.ZipFile(path) as package:
        document = ET.fromstring(package.read("word/document.xml"))
    borders = document.find(".//w:tbl/w:tblPr/w:tblBorders", NS)
    if borders is None:
        return {}
    sizes: dict[str, str | None] = {}
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        node = borders.find(f"w:{side}", NS)
        sizes[side] = node.get(f"{{{NS['w']}}}sz") if node is not None else None
    return sizes


def _first_table_unit_column_format(path: Path) -> dict[str, str | list[str] | None]:
    with zipfile.ZipFile(path) as package:
        document = ET.fromstring(package.read("word/document.xml"))
    table = document.find(".//w:tbl", NS)
    if table is None:
        return {}
    rows = table.findall("w:tr", NS)
    body_rows = rows[2:4]
    cells = [row.findall("w:tc", NS)[0] for row in body_rows]
    first_cell = cells[0]
    paragraph = first_cell.find("w:p", NS)
    paragraph_properties = paragraph.find("w:pPr", NS) if paragraph is not None else None
    cell_properties = first_cell.find("w:tcPr", NS)
    alignment = paragraph_properties.find("w:jc", NS) if paragraph_properties is not None else None
    vertical_alignment = cell_properties.find("w:vAlign", NS) if cell_properties is not None else None
    run_properties = first_cell.find("w:p/w:r/w:rPr", NS)
    bold = run_properties.find("w:b", NS) if run_properties is not None else None
    bold_cs = run_properties.find("w:bCs", NS) if run_properties is not None else None
    fills = []
    for cell in cells:
        shading = cell.find("w:tcPr/w:shd", NS)
        fills.append(shading.get(f"{{{NS['w']}}}fill") if shading is not None else None)
    return {
        "text": _cell_text(first_cell),
        "fills": fills,
        "alignment": alignment.get(f"{{{NS['w']}}}val") if alignment is not None else None,
        "vertical_alignment": vertical_alignment.get(f"{{{NS['w']}}}val") if vertical_alignment is not None else None,
        "bold": _word_boolean_state(bold),
        "bold_cs": _word_boolean_state(bold_cs),
    }


def _first_table_unit_score_column(path: Path) -> dict[str, str | None]:
    with zipfile.ZipFile(path) as package:
        document = ET.fromstring(package.read("word/document.xml"))
    table = document.find(".//w:tbl", NS)
    if table is None:
        return {}
    rows = table.findall("w:tr", NS)
    body_rows = rows[2:4]
    first_score_cell = body_rows[0].findall("w:tc", NS)[7]
    second_score_cell = body_rows[1].findall("w:tc", NS)[7]
    first_vmerge = first_score_cell.find("w:tcPr/w:vMerge", NS)
    second_vmerge = second_score_cell.find("w:tcPr/w:vMerge", NS)
    return {
        "text": _cell_text(first_score_cell),
        "first_vmerge": _w_attr(first_vmerge, "val") or "continue",
        "second_vmerge": _w_attr(second_vmerge, "val") or "continue",
        "second_text": _cell_text(second_score_cell),
    }

def _unit_score_header_formulas(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as package:
        document = ET.fromstring(package.read("word/document.xml"))
    tables = document.findall(".//w:tbl", NS)
    technical_header = tables[0].findall("w:tr", NS)[0].findall("w:tc", NS)[4]
    management_header = tables[4].findall("w:tr", NS)[0].findall("w:tc", NS)[4]
    return {
        "technical": _math_text(technical_header),
        "management": _math_text(management_header),
    }


def _math_text(cell: ET.Element) -> str:
    return "".join(node.text or "" for node in cell.findall(".//m:t", NS))


def _word_boolean_state(element: ET.Element | None) -> str:
    if element is None:
        return "false"
    value = element.get(f"{{{NS['w']}}}val")
    return "false" if value in {"0", "false", "False"} else "true"


def _first_figure_caption_format(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path) as package:
        document = ET.fromstring(package.read("word/document.xml"))
    paragraphs = document.findall(".//w:p", NS)
    for index, paragraph in enumerate(paragraphs):
        if paragraph.find(".//wp:inline", NS) is None:
            continue
        caption = paragraphs[index + 1]
        paragraph_properties = caption.find("w:pPr", NS)
        spacing = paragraph_properties.find("w:spacing", NS) if paragraph_properties is not None else None
        alignment = paragraph_properties.find("w:jc", NS) if paragraph_properties is not None else None
        run_formats = {
            tuple(_run_format(run).items())
            for run in caption.findall("w:r", NS)
            if run.find(".//w:t", NS) is not None
            or run.find(".//w:instrText", NS) is not None
            or run.find("w:fldChar", NS) is not None
        }
        return {
            "paragraph": {
                "alignment": _w_attr(alignment, "val"),
                "spacing_after": _w_attr(spacing, "after"),
                "line": _w_attr(spacing, "line"),
            },
            "runs": [dict(items) for items in sorted(run_formats)],
            "instruction": " ".join(_field_instruction(caption).split()),
        }
    return {}


def _run_format(run: ET.Element) -> dict[str, str | None]:
    run_properties = run.find("w:rPr", NS)
    fonts = run_properties.find("w:rFonts", NS) if run_properties is not None else None
    return {
        "ascii": _w_attr(fonts, "ascii"),
        "hAnsi": _w_attr(fonts, "hAnsi"),
        "eastAsia": _w_attr(fonts, "eastAsia"),
        "cs": _w_attr(fonts, "cs"),
        "bold": _word_boolean_state(run_properties.find("w:b", NS) if run_properties is not None else None),
        "bold_cs": _word_boolean_state(run_properties.find("w:bCs", NS) if run_properties is not None else None),
        "caps": _word_boolean_state(run_properties.find("w:caps", NS) if run_properties is not None else None),
        "spacing": _w_attr(run_properties.find("w:spacing", NS) if run_properties is not None else None, "val"),
        "kern": _w_attr(run_properties.find("w:kern", NS) if run_properties is not None else None, "val"),
        "size": _w_attr(run_properties.find("w:sz", NS) if run_properties is not None else None, "val"),
        "size_cs": _w_attr(run_properties.find("w:szCs", NS) if run_properties is not None else None, "val"),
    }


def _field_instruction(paragraph: ET.Element) -> str:
    return "".join(node.text or "" for node in paragraph.findall(".//w:instrText", NS))


def _w_attr(element: ET.Element | None, local_name: str) -> str | None:
    if element is None:
        return None
    return element.get(f"{{{NS['w']}}}{local_name}")


def _dropdown_tags(path: Path) -> set[str]:
    tags: set[str] = set()
    with zipfile.ZipFile(path) as package:
        document = ET.fromstring(package.read("word/document.xml"))
    for tag in document.findall(".//w:sdtPr/w:tag", NS):
        value = tag.get(f"{{{NS['w']}}}val")
        if value:
            tags.add(value)
    return tags


def _figure_layouts(path: Path) -> list[dict[str, float | bool | str]]:
    with zipfile.ZipFile(path) as package:
        document = ET.fromstring(package.read("word/document.xml"))
    paragraphs = document.findall(".//w:p", NS)
    layouts: list[dict[str, float | bool | str]] = []
    for index, paragraph in enumerate(paragraphs):
        inline = paragraph.find(".//wp:inline", NS)
        if inline is None:
            continue
        extent = inline.find("wp:extent", NS)
        paragraph_properties = paragraph.find("w:pPr", NS)
        caption = paragraphs[index + 1] if index + 1 < len(paragraphs) else None
        caption_properties = caption.find("w:pPr", NS) if caption is not None else None
        layouts.append(
            {
                "width_in": int(extent.get("cx")) / 914400 if extent is not None else 0,
                "height_in": int(extent.get("cy")) / 914400 if extent is not None else 0,
                "page_break_before": _has_paragraph_property(paragraph_properties, "pageBreakBefore"),
                "keep_next": _has_paragraph_property(paragraph_properties, "keepNext"),
                "keep_lines": _has_paragraph_property(paragraph_properties, "keepLines"),
                "caption_keep_lines": _has_paragraph_property(caption_properties, "keepLines"),
                "caption_page_break_before": _has_paragraph_property(caption_properties, "pageBreakBefore"),
                "caption_text": _paragraph_text(caption) if caption is not None else "",
            }
        )
    return layouts


def _has_paragraph_property(paragraph_properties: ET.Element | None, property_name: str) -> bool:
    return paragraph_properties is not None and paragraph_properties.find(f"w:{property_name}", NS) is not None


def _paragraph_text(paragraph: ET.Element) -> str:
    return "".join(node.text or "" for node in paragraph.findall(".//w:t", NS)).strip()


if __name__ == "__main__":
    unittest.main()
