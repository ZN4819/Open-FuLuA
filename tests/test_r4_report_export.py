from __future__ import annotations

import copy
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

from lxml import etree
from PIL import Image

from app import database
from app.main import app
from app.report_derived.rules import canonical_json, stable_hash
from app.report_export.context import build_assembly_context
from app.report_export import renderer as report_renderer
from app.report_export.renderer import render_report
from app.report_schemas import ConsistencyCheckWrite, GenerationRunWrite, ReportExportJobWrite
from app.services import report_exports, report_generation
from app.services.report_domain.errors import ReportDomainError
from app.services.report_domain.validation import validate_report
from app.services.report_templates.registry import report_template_registry
from app.report_export.word import WordRefreshError
from tests import test_r3_report_generation as r3_tests


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
NS = {"w": W, "m": M}


def _contains_private_factor(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).casefold() in {"ra", "rk"} or _contains_private_factor(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_private_factor(item) for item in value)
    return False


class R4ReportExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.r3 = r3_tests.R3ReportGenerationTests(
            methodName="test_complete_projection_score_and_context_exclude_private_factors"
        )
        self.r3.setUp()
        self.previous_storage = os.environ.get("FULUA_STORAGE_PATH")
        self.storage = Path(self.r3.temporary.name) / "storage"
        os.environ["FULUA_STORAGE_PATH"] = str(self.storage)

    def tearDown(self) -> None:
        if self.previous_storage is None:
            os.environ.pop("FULUA_STORAGE_PATH", None)
        else:
            os.environ["FULUA_STORAGE_PATH"] = self.previous_storage
        self.r3.tearDown()

    def _prepare_final_project(self, *, with_image: bool = False) -> tuple[object, int, dict]:
        project = self.r3._create_project()
        timestamp = database.utc_now()
        compiler_uuid = str(uuid.uuid4())
        second_member_uuid = str(uuid.uuid4())
        storage_object_uuid = str(uuid.uuid4())
        storage_indicator = next(
            item for item in self.r3.rules.indicators
            if item.section_code == "A-4" and item.name == "重要数据存储完整性"
        )
        image_id: int | None = None
        with database.connect() as db:
            for sort_order, (member_uuid, name) in enumerate(
                ((compiler_uuid, "张三"), (second_member_uuid, "李四")), start=1
            ):
                db.execute(
                    """
                    INSERT INTO report_members (
                        member_uuid, project_id, name, team_role, is_project_leader,
                        qualification_passed_at, active, sort_order, created_at, updated_at
                    ) VALUES (?, ?, ?, '组员', 0, '2025-01-01', 1, ?, ?, ?)
                    """,
                    (member_uuid, project["id"], name, sort_order, timestamp, timestamp),
                )
            db.execute(
                """
                UPDATE report_metadata
                SET report_number = 'BGSMSP01202600001', compiler_member_uuid = ?
                WHERE project_id = ?
                """,
                (compiler_uuid, project["id"]),
            )
            db.execute(
                """
                UPDATE report_phase_dates
                SET preparation_end = '2026-01-05', scheme_start = '2026-01-06',
                    scheme_end = '2026-01-10', fieldwork_start = '2026-01-11',
                    fieldwork_end = '2026-01-20', analysis_start = '2026-01-21',
                    analysis_end = '2026-01-31'
                WHERE project_id = ?
                """,
                (project["id"],),
            )
            db.execute(
                """
                UPDATE report_distribution
                SET regulator_copies = 1, client_copies = 1,
                    assessment_organization_copies = 1
                WHERE project_id = ?
                """,
                (project["id"],),
            )
            db.execute(
                """
                UPDATE system_profiles
                SET critical_infrastructure_status = 'not_recognized',
                    level_filing_status = 'not_filed',
                    level_assessment_status = 'not_assessed',
                    service_scope_json = ?, cloud_platform_json = ?,
                    crypto_plan_json = ?, operation_json = ?, interconnection_json = ?,
                    selected_algorithms_json = '["SM4"]', no_crypto_products = 1
                WHERE project_id = ?
                """,
                (
                    json.dumps({"kind": "local"}, ensure_ascii=False),
                    json.dumps({"dependency": "no"}, ensure_ascii=False),
                    json.dumps({"status": "none"}, ensure_ascii=False),
                    json.dumps(
                        {"status": "not_running", "construction_stage": "建设阶段"},
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {"application_catalog": ["业务子系统"], "other_algorithms": []},
                        ensure_ascii=False,
                    ),
                    project["id"],
                ),
            )
            db.execute(
                "UPDATE assessment_rows SET record_text = record_text || ' 使用SM4算法。' WHERE id = ?",
                (self.r3.rows["A-1.01"],),
            )
            db.execute(
                """
                INSERT INTO assessment_objects (
                    object_uuid, project_id, object_type, name_snapshot,
                    source_section_code, properties_json, created_at, updated_at
                ) VALUES (?, ?, 'application', '存储完整性对象', 'A-4', '{}', ?, ?)
                """,
                (storage_object_uuid, project["id"], timestamp, timestamp),
            )
            db.execute(
                """
                INSERT INTO assessment_object_subsystems (
                    binding_uuid, project_id, object_uuid, subsystem_name,
                    assessment_methods_json, remark, created_at, updated_at
                ) VALUES (?, ?, ?, '业务子系统', '["访谈","文档审查"]', '', ?, ?)
                """,
                (str(uuid.uuid4()), project["id"], storage_object_uuid, timestamp, timestamp),
            )
            db.execute(
                """
                UPDATE assessment_rows
                SET assessment_object_uuid = ?, object_name = '存储完整性对象'
                WHERE id = ?
                """,
                (storage_object_uuid, self.r3.rows[storage_indicator.code]),
            )
            if with_image:
                image_path = self.storage / "evidence" / "sample.png"
                image_path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (120, 80), (24, 120, 200)).save(image_path)
                cursor = db.execute(
                    """
                    INSERT INTO evidence_images (
                        evidence_uuid, project_id, section_code, file_path,
                        original_name, caption, alt_text, sort_order,
                        pixel_width, pixel_height, dpi_x, dpi_y,
                        display_width_in, display_height_in, created_at, updated_at
                    ) VALUES (?, ?, 'A-1', 'evidence/sample.png', 'sample.png',
                              '证据图', '证据图', 1, 120, 80, 96, 96, 1.25, 0.83, ?, ?)
                    """,
                    (str(uuid.uuid4()), project["id"], timestamp, timestamp),
                )
                image_id = int(cursor.lastrowid)
                db.execute(
                    "UPDATE assessment_rows SET record_text = record_text || ? WHERE id = ?",
                    (f" 参见[[FIG:{image_id}]]。", self.r3.rows["A-1.01"]),
                )
            db.execute(
                "UPDATE report_sections SET completion_status = 'complete' WHERE project_id = ?",
                (project["id"],),
            )
            db.execute(
                "UPDATE projects SET workflow_status = 'confirmed' WHERE id = ?",
                (project["id"],),
            )

        validation = validate_report(project["project_uuid"])
        self.assertEqual(validation["errors"], 0, validation["issues"])
        report_generation.create_generation_run(
            project["project_uuid"], GenerationRunWrite(expected_project_revision=1)
        )
        revision = self.r3._confirm_all_blocks(project["project_uuid"])
        check = report_generation.run_consistency_check(
            project["project_uuid"],
            ConsistencyCheckWrite(expected_project_revision=revision),
        )
        revision = int(check["project_revision"])
        context = build_assembly_context(
            project["project_uuid"],
            mode="final",
            version="V1.0",
            expected_project_revision=revision,
        )
        self.assertEqual(context["validation_summary"]["errors"], 0)
        if image_id is not None:
            context["test_image_id"] = image_id
        return project, revision, context

    def test_current_schema_keeps_immutable_export_storage(self) -> None:
        with database.connect() as db:
            self.assertEqual(int(db.execute("PRAGMA user_version").fetchone()[0]), 8)
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            triggers = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                ).fetchall()
            }
        self.assertTrue({"report_export_jobs", "report_export_snapshots"} <= tables)
        self.assertIn("report_export_snapshots_immutable", triggers)

    def test_schema_seven_invalidates_pre_r4_projection_context_for_regeneration(self) -> None:
        project, revision, _context = self._prepare_final_project()
        with database.connect() as db:
            current_run = db.execute(
                "SELECT current_run_uuid FROM report_generation_state WHERE project_id = ?",
                (project["id"],),
            ).fetchone()["current_run_uuid"]
            db.execute("PRAGMA user_version = 6")
        database.init_db()
        with database.connect() as db:
            state = db.execute(
                "SELECT * FROM report_generation_state WHERE project_id = ?",
                (project["id"],),
            ).fetchone()
            run_status = db.execute(
                "SELECT status FROM report_generation_runs WHERE run_uuid = ?",
                (current_run,),
            ).fetchone()["status"]
            confirmed_blocks = int(
                db.execute(
                    """
                    SELECT COUNT(*) FROM report_blocks
                    WHERE project_id = ? AND confirmation_status = 'confirmed'
                    """,
                    (project["id"],),
                ).fetchone()[0]
            )
        self.assertEqual(int(state["project_revision"]), revision + 1)
        self.assertIsNone(state["current_run_uuid"])
        self.assertIsNone(state["current_context_json"])
        self.assertEqual(run_status, "needs_input")
        self.assertEqual(confirmed_blocks, 33)

    def test_context_is_deterministic_same_revision_and_excludes_private_factors(self) -> None:
        project, revision, first = self._prepare_final_project()
        second = build_assembly_context(
            project["project_uuid"],
            mode="final",
            version="V1.0",
            expected_project_revision=revision,
        )
        self.assertEqual(first["assembly_context_hash"], second["assembly_context_hash"])
        self.assertEqual(first["r3_context_hash"], stable_hash(first["r3_context"]))
        self.assertEqual(first["r3_context"]["project_revision"], revision)
        self.assertFalse(_contains_private_factor(first))
        self.assertNotRegex(canonical_json(first), r"\b(?:Ra|Rk)\b")
        self.assertEqual(
            first["table_rows_by_table_id"]["report_table_011"][0]["name"],
            "业务子系统",
        )
        self.assertEqual(len(first["table_rows_by_table_id"]["report_table_024"]), 4)
        source = (Path.cwd() / "backend" / "app" / "report_export" / "context.py").read_text(encoding="utf-8")
        self.assertNotIn("database.connect", source)
        self.assertNotRegex(source, r"\bSELECT\b")

    def test_final_renderer_preserves_master_structure_and_corrected_display_contract(self) -> None:
        _project, _revision, context = self._prepare_final_project()
        corrected = copy.deepcopy(context)
        source = next(
            row for row in corrected["appendix_a_final_projection"]["rows"]
            if row["section_code"] == "A-1"
        )
        source.update(
            {
                "was_corrected": True,
                "final_object_score": "0.5000",
                "object_score": "0.5000",
                "unit_score": "0.5000",
                "object_result": "部分符合",
            }
        )
        output = self.storage / "r4-final.docx"
        result = render_report(corrected, output)
        self.assertEqual((result["section_count"], result["table_count"]), (17, 55))
        self.assertEqual(result["placeholder_count"], 0)
        self.assertTrue(result["template_allowlist_verified"])
        self.assertEqual(result["preserved_section_signatures"], 17)
        with zipfile.ZipFile(output) as package:
            root = etree.fromstring(package.read("word/document.xml"))
        visible = "".join(root.xpath("//w:t/text()", namespaces=NS))
        self.assertNotIn("客户复核版", visible)
        self.assertNotIn("报告版本", visible)
        self.assertNotIn("草稿—未完成复核", visible)
        self.assertNotRegex(visible, r"\b(?:Ra|Rk)\b")
        active_italic = (
            "//w:i[not(ancestor::m:oMath) and "
            "not(@w:val='0' or @w:val='false' or @w:val='off')] | "
            "//w:iCs[not(ancestor::m:oMath) and "
            "not(@w:val='0' or @w:val='false' or @w:val='off')]"
        )
        self.assertEqual(root.xpath(f"count({active_italic})", namespaces=NS), 0.0)
        table = root.xpath(
            "//w:bookmarkStart[@w:name='rt_table_039']/ancestor::w:tbl[1]",
            namespaces=NS,
        )[0]
        target_row = next(
            row for row in table.xpath("./w:tr", namespaces=NS)
            if source["object_name"] in "".join(row.xpath(".//w:t/text()", namespaces=NS))
        )
        cells = target_row.xpath("./w:tc | ./w:sdt/w:sdtContent/w:tc", namespaces=NS)
        cell_texts = ["".join(cell.xpath(".//w:t/text()", namespaces=NS)) for cell in cells]
        self.assertEqual(cell_texts[3:6], ["/", "/", "/"])
        self.assertEqual(cell_texts[6], "0.5000*")
        self.assertEqual(cell_texts[7], "0.5000")

    def test_evidence_is_embedded_with_caption_bookmark_and_ref(self) -> None:
        _project, _revision, context = self._prepare_final_project(with_image=True)
        image_id = int(context.pop("test_image_id"))
        output = self.storage / "r4-evidence.docx"
        render_report(context, output)
        with zipfile.ZipFile(output) as package:
            names = package.namelist()
            root = etree.fromstring(package.read("word/document.xml"))
        visible = "".join(root.xpath("//w:t/text()", namespaces=NS))
        instructions = " ".join(root.xpath("//w:instrText/text()", namespaces=NS))
        self.assertTrue(any(name.startswith("word/media/r4_") for name in names))
        self.assertIn("证据图", visible)
        self.assertIn(f"REF fig_{image_id}", instructions)
        self.assertNotIn(f"[[FIG:{image_id}]]", visible)
        self.assertEqual(
            root.xpath(f"count(//w:bookmarkStart[@w:name='fig_{image_id}'])", namespaces=NS),
            1.0,
        )

    def test_separate_client_is_conditionally_projected_to_cover_and_narrative(self) -> None:
        _project, _revision, context = self._prepare_final_project()
        without_client = self.storage / "r4-without-client.docx"
        render_report(context, without_client)
        with zipfile.ZipFile(without_client) as package:
            root = etree.fromstring(package.read("word/document.xml"))
        cover = root.xpath(
            "//w:bookmarkStart[@w:name='rt_table_001']/ancestor::w:tbl[1]",
            namespaces=NS,
        )[0]
        self.assertEqual(len(cover.xpath("./w:tr", namespaces=NS)), 3)
        self.assertNotIn("委托单位：", "".join(cover.xpath(".//w:t/text()", namespaces=NS)))

        separate = copy.deepcopy(context)
        separate["scalar_slot_values"].update(
            {"has_separate_client": True, "effective_client_name": "独立委托单位"}
        )
        with_client = self.storage / "r4-with-client.docx"
        render_report(separate, with_client)
        with zipfile.ZipFile(with_client) as package:
            root = etree.fromstring(package.read("word/document.xml"))
        cover = root.xpath(
            "//w:bookmarkStart[@w:name='rt_table_001']/ancestor::w:tbl[1]",
            namespaces=NS,
        )[0]
        self.assertEqual(len(cover.xpath("./w:tr", namespaces=NS)), 4)
        self.assertIn("委托单位：独立委托单位", "".join(cover.xpath(".//w:t/text()", namespaces=NS)))
        self.assertIn(
            "中互金认证有限公司受独立委托单位委托",
            "".join(root.xpath("//w:t/text()", namespaces=NS)),
        )

    def test_template_allowlist_rejects_changes_to_protected_parts(self) -> None:
        package = report_template_registry.load()
        with zipfile.ZipFile(io.BytesIO(package.runtime_template_bytes)) as source:
            original = {name: source.read(name) for name in source.namelist()}
        rendered = dict(original)
        rendered["word/styles.xml"] += b"\n"
        with self.assertRaises(ReportDomainError) as captured:
            report_renderer._assert_template_mutation_allowlist(original, rendered)
        self.assertEqual(
            captured.exception.code,
            "REPORT_TEMPLATE_MUTATION_ALLOWLIST_VIOLATION",
        )

    def test_export_job_persists_snapshot_and_allows_repeat_after_success(self) -> None:
        project, revision, context = self._prepare_final_project()
        payload = ReportExportJobWrite(
            mode="final", version="V1.0", expected_project_revision=revision
        )
        first = report_exports.create_export_job(project["project_uuid"], payload)
        with self.assertRaises(ReportDomainError) as duplicate:
            report_exports.create_export_job(project["project_uuid"], payload)
        self.assertEqual(duplicate.exception.code, "REPORT_EXPORT_ALREADY_RUNNING")

        def successful_word(input_path: Path, output_path: Path, *, status_path: Path):
            shutil.copy2(input_path, output_path)
            return {"status": "succeeded", "page_count": 82}

        with patch.object(report_exports, "refresh_with_word", side_effect=successful_word):
            report_exports.process_export_job(first["job_uuid"])
        completed = report_exports.get_export_job(first["job_uuid"])
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["word_refresh_status"], "succeeded")
        self.assertEqual(completed["page_count"], 82)
        self.assertTrue(completed["download_available"])
        path = report_exports.export_docx_path(first["job_uuid"])
        self.assertNotIn("客户复核版", path.name)
        self.assertEqual(path.name, report_exports.report_filename(context))
        with database.connect() as db:
            snapshot = db.execute(
                "SELECT * FROM report_export_snapshots WHERE job_uuid = ?",
                (first["job_uuid"],),
            ).fetchone()
            self.assertEqual(snapshot["r3_rule_set_hash"], self.r3.rules.content_sha256)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "UPDATE report_export_snapshots SET export_version = 'V9.9' WHERE job_uuid = ?",
                    (first["job_uuid"],),
                )
        repeated = report_exports.create_export_job(project["project_uuid"], payload)
        self.assertEqual(repeated["status"], "queued")

    def test_final_word_failure_has_no_download_but_draft_can_keep_unrefreshed_file(self) -> None:
        project, revision, _context = self._prepare_final_project()
        failure = WordRefreshError("WORD_REFRESH_FAILED", "Word 刷新失败")
        final_job = report_exports.create_export_job(
            project["project_uuid"],
            ReportExportJobWrite(
                mode="final", version="V1.0", expected_project_revision=revision
            ),
        )
        with patch.object(report_exports, "refresh_with_word", side_effect=failure):
            report_exports.process_export_job(final_job["job_uuid"])
        failed = report_exports.get_export_job(final_job["job_uuid"])
        self.assertEqual(failed["status"], "failed")
        self.assertFalse(failed["download_available"])
        with self.assertRaises(ReportDomainError) as unavailable:
            report_exports.export_docx_path(final_job["job_uuid"])
        self.assertEqual(unavailable.exception.code, "REPORT_EXPORT_NOT_READY")

        draft_job = report_exports.create_export_job(
            project["project_uuid"],
            ReportExportJobWrite(
                mode="draft", version="V1.0", expected_project_revision=revision
            ),
        )
        with patch.object(report_exports, "refresh_with_word", side_effect=failure):
            report_exports.process_export_job(draft_job["job_uuid"])
        draft = report_exports.get_export_job(draft_job["job_uuid"])
        self.assertEqual(draft["status"], "succeeded")
        self.assertEqual(draft["word_refresh_status"], "skipped")
        self.assertTrue(draft["download_available"])

    def test_openapi_exposes_validation_job_issue_and_docx_routes(self) -> None:
        paths = app.openapi()["paths"]
        self.assertIn("/api/projects/{project_uuid}/report-validations", paths)
        self.assertIn("/api/projects/{project_uuid}/report-export-jobs", paths)
        self.assertIn("/api/report-export-jobs/{job_uuid}", paths)
        self.assertIn("/api/report-export-jobs/{job_uuid}/issues", paths)
        self.assertIn("/api/report-export-jobs/{job_uuid}/docx", paths)

    def test_word_refresh_is_word_only_and_tracks_its_owned_process(self) -> None:
        script = (Path.cwd() / "scripts" / "word_refresh_report.ps1").read_text(encoding="utf-8")
        wrapper = (Path.cwd() / "backend" / "app" / "report_export" / "word.py").read_text(encoding="utf-8")
        self.assertIn("Get-OwnedWordProcessId", script)
        self.assertIn("ExistingProcessIds", script)
        self.assertIn("Stop-Process -Id", wrapper)
        self.assertNotIn("Stop-Process -Name", wrapper)
        self.assertNotIn("soffice", (script + wrapper).lower())
        self.assertNotIn("unoconv", (script + wrapper).lower())
        self.assertTrue(script.isascii(), "Windows PowerShell 5.1 脚本必须保持 ASCII，避免无 BOM 解码失败")


if __name__ == "__main__":
    unittest.main()
