from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app import database
from app.contracts import (
    FULL_REPORT_TEMPLATE_ASSET_SET_HASH,
    FULL_REPORT_TEMPLATE_EDITION,
    FULL_REPORT_TEMPLATE_PACKAGE_ID,
    FULL_REPORT_TEMPLATE_REVISION,
)
from app.report_derived.engine import (
    aggregate_indicator_result,
    build_projection,
    calculate_a2_correction,
    calculate_a4_correction,
    validate_golden_vectors,
)
from app.report_derived.context_contract import (
    ContextContractViolation,
    ENVELOPE_KEYS,
    SOURCE_HASH_KEYS,
    validate_context_envelope,
)
from app.report_derived.rules import (
    RULE_MATRIX_PATH,
    RuleSetUnavailable,
    load_default_rule_set,
    load_rule_set,
    stable_hash,
)
from app.report_derived.narratives import assessment_conclusion, build_finding_baselines
from app.report_schemas import (
    ConsistencyCheckWrite,
    DerivedBlockConfirmationWrite,
    DerivedBlockOverrideWrite,
    GenerationRunWrite,
    RiskUpdateWrite,
)
from app.main import app
from app.services import report_generation
from app.services.report_domain.errors import ReportDomainError


TECHNICAL_SECTIONS = {"A-1", "A-2", "A-3", "A-4"}


def _has_private_factor(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).casefold() in {"ra", "rk"} or _has_private_factor(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_has_private_factor(item) for item in value)
    return False


class R3ReportGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "app.db"
        self.previous_database = os.environ.get("FULUA_DATABASE_PATH")
        os.environ["FULUA_DATABASE_PATH"] = str(self.database_path)
        database.init_db()
        self.rules = load_default_rule_set()

    def tearDown(self) -> None:
        if self.previous_database is None:
            os.environ.pop("FULUA_DATABASE_PATH", None)
        else:
            os.environ["FULUA_DATABASE_PATH"] = self.previous_database
        self.temporary.cleanup()

    def _create_project(self) -> object:
        project = database.create_project(
            "R3 集成项目",
            project_type="full_report",
            template_package_id=FULL_REPORT_TEMPLATE_PACKAGE_ID,
            template_edition=FULL_REPORT_TEMPLATE_EDITION,
            template_revision=FULL_REPORT_TEMPLATE_REVISION,
            template_asset_set_hash=FULL_REPORT_TEMPLATE_ASSET_SET_HASH,
        )
        timestamp = database.utc_now()
        with database.connect() as db:
            db.execute(
                "UPDATE system_profiles SET system_name = ?, system_summary = ? WHERE project_id = ?",
                ("分行特色系统", "本系统用于办理分行特色业务。", project["id"]),
            )
            db.execute(
                "UPDATE report_metadata SET classification_level = '三级' WHERE project_id = ?",
                (project["id"],),
            )
            db.execute(
                """
                UPDATE report_phase_dates
                SET preparation_start = '2026-01-01', analysis_end = '2026-01-31'
                WHERE project_id = ?
                """,
                (project["id"],),
            )
            db.execute(
                """
                UPDATE report_organizations SET name = '被测单位'
                WHERE project_id = ? AND organization_type = 'assessed'
                """,
                (project["id"],),
            )
            sections = {
                row["code"]: int(row["id"])
                for row in db.execute(
                    "SELECT id, code FROM appendix_sections WHERE project_id = ?",
                    (project["id"],),
                )
            }
            object_uuids: dict[str, str] = {}
            for layer in self.rules.layers:
                object_uuid = str(uuid.uuid4())
                object_uuids[layer.section_code] = object_uuid
                db.execute(
                    """
                    INSERT INTO assessment_objects (
                        object_uuid, project_id, object_type, name_snapshot,
                        source_section_code, properties_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, '{}', ?, ?)
                    """,
                    (
                        object_uuid,
                        project["id"],
                        "management" if layer.category == "management" else "other",
                        f"{layer.name}测评对象",
                        layer.section_code,
                        timestamp,
                        timestamp,
                    ),
                )
                if layer.section_code == "A-4":
                    db.execute(
                        """
                        INSERT INTO assessment_object_subsystems (
                            binding_uuid, project_id, object_uuid, subsystem_name,
                            assessment_methods_json, remark, created_at, updated_at
                        ) VALUES (?, ?, ?, '业务子系统', '[]', '', ?, ?)
                        """,
                        (str(uuid.uuid4()), project["id"], object_uuid, timestamp, timestamp),
                    )
            self.rows: dict[str, int] = {}
            for sort_order, indicator in enumerate(self.rules.indicators, start=1):
                cursor = db.execute(
                    """
                    INSERT INTO assessment_rows (
                        section_id, unit, object_name, subsystem, record_text,
                        sort_order, created_at, updated_at, assessment_object_uuid
                    ) VALUES (?, ?, ?, '业务子系统', '符合测评要求。', ?, ?, ?, ?)
                    """,
                    (
                        sections[indicator.section_code],
                        indicator.name,
                        f"{indicator.section_code}测评对象",
                        sort_order,
                        timestamp,
                        timestamp,
                        object_uuids[indicator.section_code],
                    ),
                )
                row_id = int(cursor.lastrowid)
                self.rows[indicator.code] = row_id
                if indicator.section_code in TECHNICAL_SECTIONS:
                    db.execute(
                        """
                        INSERT INTO metric_results (
                            row_id, d, a, k, ra, rk, object_score, unit_score, compliance
                        ) VALUES (?, ?, ?, ?, '1', '1', '1.0000', '1.0000', '符合')
                        """,
                        (row_id, "\u221a", "\u221a", "\u221a"),
                    )
                else:
                    db.execute(
                        """
                        INSERT INTO metric_results (row_id, object_score, unit_score, compliance)
                        VALUES (?, '1.0000', '1.0000', '符合')
                        """,
                        (row_id,),
                    )
        return project

    def _confirm_all_blocks(self, project_uuid: str) -> int:
        review = report_generation.review_state(project_uuid)
        revision = int(review["project_revision"])
        for block in review["blocks"]:
            if block["confirmation_status"] == "confirmed":
                continue
            result = report_generation.confirm_block(
                project_uuid,
                block["block_uuid"],
                DerivedBlockConfirmationWrite(
                    expected_project_revision=revision,
                    action="confirm",
                ),
            )
            revision = int(result["project_revision"])
        return revision

    def _downgrade_to_schema_five(self) -> None:
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("PRAGMA foreign_keys = OFF")
            for table in (
                "report_consistency_checks",
                "report_block_revisions",
                "report_risk_threat_relations",
                "report_risks",
                "report_findings",
                "report_generation_runs",
                "report_generation_state",
            ):
                connection.execute(f"DROP TABLE IF EXISTS {table}")
            connection.execute("DROP INDEX IF EXISTS idx_report_blocks_uuid_project")
            connection.execute("PRAGMA user_version = 5")
            connection.commit()
        finally:
            connection.close()

    def test_current_schema_initializes_derived_state_only_for_full_report(self) -> None:
        appendix_project = database.create_project("仅附录 A")
        full_report = self._create_project()
        with database.connect() as db:
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
            full_count = int(
                db.execute(
                    "SELECT COUNT(*) FROM report_generation_state WHERE project_id = ?",
                    (full_report["id"],),
                ).fetchone()[0]
            )
            appendix_count = int(
                db.execute(
                    "SELECT COUNT(*) FROM report_generation_state WHERE project_id = ?",
                    (appendix_project["id"],),
                ).fetchone()[0]
            )
            foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
        self.assertEqual(version, 8)
        self.assertEqual((full_count, appendix_count), (1, 0))
        self.assertEqual(foreign_keys, [])

    def test_schema_five_upgrade_creates_r3_tables_and_initializes_existing_project(self) -> None:
        project = self._create_project()
        self._downgrade_to_schema_five()
        database.init_db()
        with database.connect() as db:
            self.assertEqual(int(db.execute("PRAGMA user_version").fetchone()[0]), 8)
            state = db.execute(
                "SELECT project_revision FROM report_generation_state WHERE project_id = ?",
                (project["id"],),
            ).fetchone()
            self.assertIsNotNone(state)
            self.assertEqual(int(state["project_revision"]), 1)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_schema_six_upgrade_failure_keeps_schema_five_version(self) -> None:
        self._create_project()
        self._downgrade_to_schema_five()
        with patch.object(
            database,
            "ensure_report_derived_schema",
            side_effect=RuntimeError("INJECTED_R3_SCHEMA_FAILURE"),
        ):
            with self.assertRaisesRegex(RuntimeError, "INJECTED_R3_SCHEMA_FAILURE"):
                database.init_db()
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(int(connection.execute("PRAGMA user_version").fetchone()[0]), 5)
            self.assertIsNone(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'report_generation_state'"
                ).fetchone()
            )
        finally:
            connection.close()

    def test_rule_matrix_is_closed_versioned_and_tamper_evident(self) -> None:
        self.assertEqual(self.rules.rule_set_id, "report-derived-2023-2025.12.08-v1")
        self.assertEqual((len(self.rules.layers), len(self.rules.indicators)), (8, 41))
        self.assertEqual(len(self.rules.threat_catalog), 24)
        self.assertEqual(validate_golden_vectors(self.rules), [])

        source = Path.cwd().joinpath(*RULE_MATRIX_PATH)
        tampered = json.loads(source.read_text(encoding="utf-8"))
        tampered["payload"]["indicators"][0]["weight"] = "9"
        target = Path(self.temporary.name) / "tampered.json"
        target.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(RuleSetUnavailable) as error:
            load_rule_set(target)
        self.assertEqual(error.exception.reason, "RULE_SET_HASH_MISMATCH")

        dynamic = json.loads(source.read_text(encoding="utf-8"))
        dynamic["payload"]["rules"][0]["algorithm"] = "eval(user_input)"
        dynamic["content_sha256"] = stable_hash(dynamic["payload"])
        dynamic_target = Path(self.temporary.name) / "dynamic.json"
        dynamic_target.write_text(json.dumps(dynamic, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(RuleSetUnavailable) as dynamic_error:
            load_rule_set(dynamic_target)
        self.assertEqual(dynamic_error.exception.reason, "RULE_SET_ALGORITHM_NOT_ALLOWED")

        with patch.object(
            report_generation,
            "load_default_rule_set",
            side_effect=RuleSetUnavailable(
                "RULE_SET_FILE_UNAVAILABLE",
                details={"path": "C:/Users/private/template.json"},
            ),
        ):
            with self.assertRaises(ReportDomainError) as unavailable:
                report_generation.impact_preview(str(uuid.uuid4()))
        self.assertEqual(unavailable.exception.code, "RULE_SET_UNAVAILABLE")
        self.assertNotIn("path", unavailable.exception.details)

    def test_projection_context_schema_asset_matches_runtime_contract(self) -> None:
        schema_path = Path.cwd() / "templates" / "report" / "contracts" / "2023-2025.12.08" / "r3_projection_context.v1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schema_version"]["const"], "1.0")
        self.assertEqual(set(schema["required"]), set(ENVELOPE_KEYS))
        self.assertEqual(
            set(schema["properties"]["source_hashes"]["required"]),
            set(SOURCE_HASH_KEYS),
        )
        self.assertEqual(schema["properties"]["blocks"]["minItems"], len(report_generation.DERIVED_BLOCK_KEYS))
        self.assertEqual(schema["properties"]["threat_catalog"]["minItems"], 24)
        chapter4_contract = schema["properties"]["original_projection"]["allOf"][1]["properties"]["chapter4_tables"]
        self.assertEqual(
            set(chapter4_contract["required"]),
            {f"table_4_{index}" for index in range(1, 12)},
        )
        self.assertFalse(chapter4_contract["additionalProperties"])
        for table_id, contract in chapter4_contract["properties"].items():
            definition = schema["$defs"][contract["$ref"].rsplit("/", 1)[-1]]
            self.assertEqual(
                definition["allOf"][1]["properties"]["projection_id"]["const"],
                table_id,
            )

    def test_indicator_aggregation_and_bidirectional_correction_contract(self) -> None:
        vectors = {
            ("符合", "不适用"): "符合",
            ("符合", "部分符合"): "部分符合",
            ("符合", "不符合"): "部分符合",
            ("部分符合", "不符合"): "部分符合",
            ("不符合", "不适用"): "不符合",
            ("不符合", "不符合"): "不符合",
            ("不适用", "不适用"): "不适用",
        }
        for values, expected in vectors.items():
            with self.subTest(values=values):
                self.assertEqual(aggregate_indicator_result(values), expected)
        self.assertEqual(calculate_a2_correction("0.1000", ["0.8000", "0.4000"]), "0.2000")
        self.assertEqual(calculate_a2_correction("0.3000", ["0.8000", "0.4000"]), "0.3000")
        self.assertEqual(calculate_a4_correction("0.1000", "0.6000"), "0.3000")
        self.assertEqual(calculate_a4_correction("0.4000", "0.6000"), "0.4000")

    def test_findings_merge_noncompliant_objects_and_keep_partial_objects_separate(self) -> None:
        target = self.rules.indicators[0]
        indicators = [
            {
                "indicator_code": rule.code,
                "indicator_result": "部分符合" if rule.code == target.code else "符合",
            }
            for rule in self.rules.indicators
        ]
        rows = [
            {
                "indicator_code": target.code,
                "object_result": "不符合",
                "object_uuid": "object-noncompliant-1",
                "object_name": "机房一",
                "source_row_id": 1,
                "record_text": "未采用密码技术进行身份鉴别。",
            },
            {
                "indicator_code": target.code,
                "object_result": "不符合",
                "object_uuid": "object-noncompliant-2",
                "object_name": "机房二",
                "source_row_id": 2,
                "record_text": "未采用密码技术进行身份鉴别。",
            },
            {
                "indicator_code": target.code,
                "object_result": "部分符合",
                "object_uuid": "object-partial-1",
                "object_name": "机房三",
                "source_row_id": 3,
                "record_text": "已有措施但覆盖范围不足。",
            },
            {
                "indicator_code": target.code,
                "object_result": "部分符合",
                "object_uuid": "object-partial-2",
                "object_name": "机房四",
                "source_row_id": 4,
                "record_text": "已有措施但证据不完整。",
            },
        ]

        findings = build_finding_baselines({"rows": rows, "indicators": indicators}, self.rules)

        self.assertEqual(len(findings), 1)
        problem_items = findings[0]["problem_items"]
        self.assertEqual([item["result"] for item in problem_items], ["不符合", "部分符合", "部分符合"])
        self.assertEqual(problem_items[0]["object_uuids"], ["object-noncompliant-1", "object-noncompliant-2"])
        self.assertIn("机房一、机房二", problem_items[0]["text"])
        self.assertEqual(problem_items[1]["object_uuids"], ["object-partial-1"])
        self.assertEqual(problem_items[2]["object_uuids"], ["object-partial-2"])

    def test_identical_finding_is_reactivated_after_disappearing(self) -> None:
        project = self._create_project()
        project_uuid = project["project_uuid"]
        row_id = self.rows["A-5.01"]
        with database.connect() as db:
            db.execute(
                """
                UPDATE metric_results
                SET compliance = '部分符合', object_score = '0.5000', unit_score = '0.5000'
                WHERE row_id = ?
                """,
                (row_id,),
            )

        first = report_generation.create_generation_run(
            project_uuid,
            GenerationRunWrite(expected_project_revision=1),
        )
        self.assertEqual(first["status"], "needs_input")
        original_finding = report_generation.list_findings(project_uuid)["items"][0]
        original_risk = report_generation.list_risks(project_uuid)["items"][0]

        with database.connect() as db:
            db.execute(
                """
                UPDATE metric_results
                SET compliance = '符合', object_score = '1.0000', unit_score = '1.0000'
                WHERE row_id = ?
                """,
                (row_id,),
            )
        without_finding = report_generation.create_generation_run(
            project_uuid,
            GenerationRunWrite(expected_project_revision=first["project_revision"]),
        )
        self.assertEqual(without_finding["status"], "current")
        self.assertEqual(report_generation.list_findings(project_uuid)["items"], [])

        with database.connect() as db:
            db.execute(
                """
                UPDATE metric_results
                SET compliance = '部分符合', object_score = '0.5000', unit_score = '0.5000'
                WHERE row_id = ?
                """,
                (row_id,),
            )
        restored = report_generation.create_generation_run(
            project_uuid,
            GenerationRunWrite(expected_project_revision=without_finding["project_revision"]),
        )

        self.assertEqual(restored["status"], "needs_input")
        restored_finding = report_generation.list_findings(project_uuid)["items"][0]
        restored_risk = report_generation.list_risks(project_uuid)["items"][0]
        self.assertEqual(restored_finding["finding_uuid"], original_finding["finding_uuid"])
        self.assertEqual(restored_risk["risk_uuid"], original_risk["risk_uuid"])
        with database.connect() as db:
            finding_count = int(
                db.execute(
                    "SELECT COUNT(*) FROM report_findings WHERE project_id = ? AND indicator_code = 'A-5.01'",
                    (project["id"],),
                ).fetchone()[0]
            )
        self.assertEqual(finding_count, 1)

    def test_assessment_conclusion_uses_display_score_and_high_risk_gate(self) -> None:
        risk = {
            "counts": {"high": 0, "medium": 0, "low": 0},
            "overall_risk": "未发现安全风险",
            "high_risk_judgment": "判定系统不存在高风险",
        }
        self.assertEqual(assessment_conclusion({"display_score": "100.00"}, risk)["conclusion"], "符合")
        self.assertEqual(assessment_conclusion({"display_score": "99.99"}, risk)["conclusion"], "基本符合")
        self.assertEqual(assessment_conclusion({"display_score": "60.00"}, risk)["conclusion"], "基本符合")
        self.assertEqual(assessment_conclusion({"display_score": "59.99"}, risk)["conclusion"], "不符合")
        with_high = {
            **risk,
            "counts": {"high": 1, "medium": 0, "low": 0},
            "overall_risk": "高",
            "high_risk_judgment": "判定系统存在高风险",
        }
        self.assertEqual(assessment_conclusion({"display_score": "99.99"}, with_high)["conclusion"], "不符合")

    def test_complete_projection_score_and_context_exclude_private_factors(self) -> None:
        project = self._create_project()
        with database.connect() as db:
            projection = build_projection(db, int(project["id"]), rule_set=self.rules)
        final = projection["final_projection"]
        self.assertEqual(final["statistics"]["total"]["indicator_total"], 41)
        self.assertEqual(final["statistics"]["total"]["compliant"], 41)
        self.assertEqual(final["score"]["display_score"], "100.00")
        chapter4 = projection["original_projection"]["chapter4_tables"]
        self.assertEqual(set(chapter4), {f"table_4_{index}" for index in range(1, 12)})
        self.assertEqual(chapter4["table_4_4"]["rows"][0]["object_name"], "业务子系统")
        self.assertEqual(
            chapter4["table_4_6"]["rows"][0]["cells"]["存储完整性"]["result"],
            "符合",
        )
        self.assertEqual(
            chapter4["table_4_6"]["summary"]["存储完整性"]["result"],
            "符合",
        )
        self.assertFalse(_has_private_factor(projection))

        run = report_generation.create_generation_run(
            project["project_uuid"], GenerationRunWrite(expected_project_revision=1)
        )
        self.assertEqual(run["status"], "current")
        self.assertEqual(len(run["projection"]["blocks"]), len(report_generation.DERIVED_BLOCK_KEYS))
        self.assertFalse(_has_private_factor(run))
        blocks = {item["block_key"]: item["baseline"] for item in run["projection"]["blocks"]}
        summary = blocks["conclusion.assessment_summary"]
        self.assertFalse(summary["italic"])
        self.assertEqual(summary["first_line_indent_chars"], 2)
        self.assertTrue(summary["text"].startswith("　　受被测单位委托"))
        self.assertNotIn("建议不超过200字", json.dumps(blocks, ensure_ascii=False))
        self.assertNotIn("【", json.dumps(blocks, ensure_ascii=False))
        self.assertTrue(blocks["overall_evaluation.intro"]["text"])
        self.assertTrue(blocks["overall_evaluation.outro"]["text"])
        self.assertTrue(blocks["risk_analysis.summary"]["method_text"])
        for index in range(1, 9):
            self.assertFalse(blocks[f"security_issues.layer.{index}"]["visible"])
            self.assertFalse(blocks[f"recommendations.layer.{index}"]["visible"])
        self.assertFalse(
            any(
                value.get("italic") is True
                for value in blocks.values()
                if isinstance(value, dict)
            )
        )
        revision = self._confirm_all_blocks(project["project_uuid"])
        check = report_generation.run_consistency_check(
            project["project_uuid"],
            ConsistencyCheckWrite(expected_project_revision=revision),
        )
        self.assertEqual(check["status"], "valid")
        context = report_generation.get_projection_context(project["project_uuid"])
        self.assertEqual(context["schema_version"], "1.0")
        self.assertEqual(context["generation_run_uuid"], run["run_uuid"])
        self.assertEqual(set(context["source_hashes"]), set(SOURCE_HASH_KEYS))
        self.assertEqual(context["final_projection"]["score"]["display_score"], "100.00")
        self.assertEqual(len(context["threat_catalog"]), 24)
        self.assertFalse(_has_private_factor(context))
        validate_context_envelope(
            context,
            expected_block_keys=report_generation.DERIVED_BLOCK_KEYS,
            expected_threat_ids=(item["id"] for item in self.rules.threat_catalog),
        )
        incomplete_context = {key: value for key, value in context.items() if key != "source_hashes"}
        with self.assertRaises(ContextContractViolation):
            validate_context_envelope(
                incomplete_context,
                expected_block_keys=report_generation.DERIVED_BLOCK_KEYS,
                expected_threat_ids=(item["id"] for item in self.rules.threat_catalog),
            )
        with database.connect() as db:
            db.execute(
                "UPDATE system_profiles SET system_summary = '一致性校验后的新事实。' WHERE project_id = ?",
                (project["id"],),
            )
        with self.assertRaises(ReportDomainError) as stale_context:
            report_generation.get_projection_context(project["project_uuid"])
        self.assertEqual(stale_context.exception.code, "R3_CONTEXT_STALE")

    def test_multi_object_correction_uses_lowest_a4_source_and_only_changes_below_threshold(self) -> None:
        project = self._create_project()
        timestamp = database.utc_now()
        with database.connect() as db:
            a4_section_id = int(
                db.execute(
                    "SELECT id FROM appendix_sections WHERE project_id = ? AND code = 'A-4'",
                    (project["id"],),
                ).fetchone()["id"]
            )
            existing_a2_conf = db.execute(
                "SELECT assessment_object_uuid FROM assessment_rows WHERE id = ?",
                (self.rows["A-2.03"],),
            ).fetchone()["assessment_object_uuid"]
            existing_a2_int = db.execute(
                "SELECT assessment_object_uuid FROM assessment_rows WHERE id = ?",
                (self.rows["A-2.02"],),
            ).fetchone()["assessment_object_uuid"]
            existing_a4 = db.execute(
                "SELECT assessment_object_uuid FROM assessment_rows WHERE id = ?",
                (self.rows["A-4.04"],),
            ).fetchone()["assessment_object_uuid"]

            db.execute(
                """
                UPDATE metric_results
                SET d = ?, a = ?, k = ?, ra = '0.2', rk = '1', object_score = '0.1000'
                WHERE row_id = ?
                """,
                ("\u221a", "\u00d7", "\u221a", self.rows["A-2.03"]),
            )
            db.execute(
                """
                UPDATE metric_results
                SET d = ?, a = ?, k = ?, ra = '1', rk = '1.2', object_score = '0.6000'
                WHERE row_id = ?
                """,
                ("\u221a", "\u221a", "\u00d7", self.rows["A-2.02"]),
            )

            second_a4 = str(uuid.uuid4())
            db.execute(
                """
                INSERT INTO assessment_objects (
                    object_uuid, project_id, object_type, name_snapshot,
                    source_section_code, properties_json, created_at, updated_at
                ) VALUES (?, ?, 'data', '第二个重要数据', 'A-4', '{}', ?, ?)
                """,
                (second_a4, project["id"], timestamp, timestamp),
            )
            db.execute(
                """
                INSERT INTO assessment_object_subsystems (
                    binding_uuid, project_id, object_uuid, subsystem_name,
                    assessment_methods_json, remark, created_at, updated_at
                ) VALUES (?, ?, ?, '业务子系统', '[]', '', ?, ?)
                """,
                (str(uuid.uuid4()), project["id"], second_a4, timestamp, timestamp),
            )
            extra_rows: dict[str, int] = {}
            for sort_order, (indicator_name, score_kind) in enumerate(
                (("重要数据传输机密性", "half"), ("重要数据传输完整性", "low")),
                start=100,
            ):
                cursor = db.execute(
                    """
                    INSERT INTO assessment_rows (
                        section_id, unit, object_name, subsystem, record_text,
                        sort_order, created_at, updated_at, assessment_object_uuid
                    ) VALUES (?, ?, '第二个重要数据', '业务子系统', '存在部分密码保护措施。', ?, ?, ?, ?)
                    """,
                    (a4_section_id, indicator_name, sort_order, timestamp, timestamp, second_a4),
                )
                row_id = int(cursor.lastrowid)
                extra_rows[indicator_name] = row_id
                factor = "1" if score_kind == "half" else "0.2"
                db.execute(
                    """
                    INSERT INTO metric_results (
                        row_id, d, a, k, ra, rk, object_score, unit_score, compliance
                    ) VALUES (?, ?, ?, ?, ?, '1', ?, ?, '部分符合')
                    """,
                    (
                        row_id,
                        "\u221a",
                        "\u00d7",
                        "\u221a",
                        factor,
                        "0.5000" if score_kind == "half" else "0.1000",
                        "0.7500" if score_kind == "half" else "0.5500",
                    ),
                )

            relations = (
                (existing_a2_conf, "通信过程中重要数据的机密性", existing_a4, "重要数据传输机密性", "confidentiality"),
                (existing_a2_conf, "通信过程中重要数据的机密性", second_a4, "重要数据传输机密性", "confidentiality"),
                (existing_a2_int, "通信数据完整性", existing_a4, "重要数据传输完整性", "integrity"),
                (existing_a2_int, "通信数据完整性", second_a4, "重要数据传输完整性", "integrity"),
            )
            for relation in relations:
                db.execute(
                    """
                    INSERT INTO result_correction_relations (
                        correction_uuid, project_id, a2_object_uuid, a2_metric_code,
                        a4_object_uuid, a4_metric_code, correction_kind,
                        original_references_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)
                    """,
                    (str(uuid.uuid4()), project["id"], *relation, timestamp, timestamp),
                )

            projection = build_projection(db, int(project["id"]), rule_set=self.rules)

        corrections = projection["correction_projection"]["rows"]
        self.assertEqual(len(corrections), 2)
        by_row = {item["source_row_id"]: item for item in corrections}
        self.assertEqual(by_row[self.rows["A-2.03"]]["final_score"], "0.2500")
        self.assertEqual(
            by_row[extra_rows["重要数据传输完整性"]]["final_score"],
            "0.3000",
        )
        self.assertNotIn(self.rows["A-4.04"], by_row)
        self.assertNotIn(extra_rows["重要数据传输机密性"], by_row)

        final_rows = {
            item["source_row_id"]: item
            for item in projection["final_projection"]["rows"]
        }
        self.assertTrue(final_rows[self.rows["A-2.03"]]["was_corrected"])
        self.assertEqual(final_rows[self.rows["A-2.03"]]["original_object_score"], "0.1000")
        self.assertEqual(final_rows[self.rows["A-2.03"]]["final_object_score"], "0.2500")
        self.assertFalse(final_rows[self.rows["A-4.04"]]["was_corrected"])

    def test_http_api_exposes_generation_review_and_consistency_boundaries(self) -> None:
        routes = {
            (route.path, method)
            for route in app.routes
            for method in getattr(route, "methods", set())
        }
        expected = {
            ("/api/projects/{project_uuid}/report/generation/impact-preview", "POST"),
            ("/api/projects/{project_uuid}/report/generation/runs", "POST"),
            ("/api/projects/{project_uuid}/report/generation/review", "GET"),
            ("/api/projects/{project_uuid}/report/findings", "GET"),
            ("/api/projects/{project_uuid}/report/risks", "GET"),
            ("/api/projects/{project_uuid}/report/risks/{risk_uuid}", "PUT"),
            ("/api/projects/{project_uuid}/report/consistency-checks", "POST"),
            ("/api/projects/{project_uuid}/report/projection-context", "GET"),
        }
        self.assertEqual(expected - routes, set())

    def test_problem_layers_keep_continuous_indicator_numbers_and_high_risk_conclusion(self) -> None:
        project = self._create_project()
        with database.connect() as db:
            db.execute(
                """
                UPDATE metric_results
                SET compliance = '部分符合', object_score = '0.5000', unit_score = '0.5000'
                WHERE row_id = ?
                """,
                (self.rows["A-5.01"],),
            )
            db.execute(
                """
                UPDATE metric_results
                SET compliance = '不符合', object_score = '0.0000', unit_score = '0.0000'
                WHERE row_id = ?
                """,
                (self.rows["A-5.02"],),
            )
        first = report_generation.create_generation_run(
            project["project_uuid"], GenerationRunWrite(expected_project_revision=1)
        )
        self.assertEqual(first["status"], "needs_input")
        risks = report_generation.list_risks(project["project_uuid"])
        self.assertEqual(len(risks["items"]), 2)
        self.assertEqual(len(risks["threat_catalog"]), 24)
        by_indicator = {item["indicator_code"]: item for item in risks["items"]}
        high_risk = by_indicator["A-5.01"]
        updated = report_generation.update_risk(
            project["project_uuid"],
            high_risk["risk_uuid"],
            RiskUpdateWrite(
                expected_project_revision=risks["project_revision"],
                expected_revision=high_risk["revision"],
                risk_level="high",
                threat_ids=["TP1"],
                analysis_text="该问题可能被物理环境威胁利用，整体判定为高风险。",
                confirm=True,
            ),
        )
        low_risk = by_indicator["A-5.02"]
        updated = report_generation.update_risk(
            project["project_uuid"],
            low_risk["risk_uuid"],
            RiskUpdateWrite(
                expected_project_revision=updated["project_revision"],
                expected_revision=low_risk["revision"],
                risk_level="low",
                threat_ids=["TP2"],
                analysis_text="该问题可能被物理环境威胁利用，整体判定为低风险。",
                confirm=True,
            ),
        )
        second = report_generation.create_generation_run(
            project["project_uuid"],
            GenerationRunWrite(expected_project_revision=updated["project_revision"]),
        )
        self.assertEqual(second["status"], "current")
        snapshot = second["projection"]["risk_snapshot"]
        self.assertEqual(snapshot["risk_total"], 2)
        self.assertEqual(snapshot["counts"], {"high": 1, "medium": 0, "low": 1})
        self.assertEqual(snapshot["high_risk_judgment"], "判定系统存在高风险")
        self.assertEqual(second["projection"]["assessment_conclusion"]["conclusion"], "不符合")
        blocks = {item["block_key"]: item["baseline"] for item in second["projection"]["blocks"]}
        policy_problems = blocks["security_issues.layer.5"]
        policy_recommendations = blocks["recommendations.layer.5"]
        self.assertTrue(policy_problems["visible"])
        self.assertEqual([item["number"] for item in policy_problems["problems"]], [1, 2])
        self.assertEqual([item["number"] for item in policy_recommendations["items"]], [1, 2])
        self.assertEqual([item["number"] for item in blocks["risk_analysis.rows"]["rows"]], [1, 2])
        self.assertIn("判定系统存在高风险", blocks["risk_analysis.summary"]["summary_text"])
        for index in (1, 2, 3, 4, 6, 7, 8):
            self.assertFalse(blocks[f"security_issues.layer.{index}"]["visible"])

    def test_revision_conflict_and_source_change_make_only_dependent_block_stale(self) -> None:
        project = self._create_project()
        report_generation.create_generation_run(
            project["project_uuid"], GenerationRunWrite(expected_project_revision=1)
        )
        review = report_generation.review_state(project["project_uuid"])
        with self.assertRaises(ReportDomainError) as error:
            report_generation.run_consistency_check(
                project["project_uuid"], ConsistencyCheckWrite(expected_project_revision=1)
            )
        self.assertEqual(error.exception.code, "PROJECT_REVISION_CONFLICT")

        with database.connect() as db:
            db.execute(
                "UPDATE system_profiles SET system_summary = '更新后的系统简介。' WHERE project_id = ?",
                (project["id"],),
            )
        check = report_generation.run_consistency_check(
            project["project_uuid"],
            ConsistencyCheckWrite(expected_project_revision=review["project_revision"]),
        )
        stale = {
            item.get("block_key")
            for item in check["issues"]
            if item["code"] == "R3_BLOCK_STALE"
        }
        self.assertEqual(stale, {"conclusion.system_summary"})

    def test_manual_block_override_requires_non_whitespace_reason(self) -> None:
        project = self._create_project()
        report_generation.create_generation_run(
            project["project_uuid"], GenerationRunWrite(expected_project_revision=1)
        )
        review = report_generation.review_state(project["project_uuid"])
        block = next(item for item in review["blocks"] if item["block_key"] == "conclusion.system_summary")

        with self.assertRaises(ReportDomainError) as error:
            report_generation.override_block(
                project["project_uuid"],
                block["block_uuid"],
                DerivedBlockOverrideWrite(
                    expected_project_revision=review["project_revision"],
                    override={"text": "人工确认后的系统简介。"},
                    override_reason="   ",
                ),
            )

        self.assertEqual(error.exception.code, "BLOCK_OVERRIDE_REASON_REQUIRED")

    def test_special_indicator_and_correction_relation_have_precise_stale_boundaries(self) -> None:
        project = self._create_project()
        first = report_generation.create_generation_run(
            project["project_uuid"], GenerationRunWrite(expected_project_revision=1)
        )
        timestamp = database.utc_now()
        with database.connect() as db:
            standard_uuid = str(uuid.uuid4())
            db.execute(
                """
                INSERT INTO report_standards (
                    standard_uuid, project_id, standard_kind, standard_code,
                    standard_name, source_reference, sort_order, created_at, updated_at
                ) VALUES (?, ?, 'manual', 'GM/T TEST', '人工补充标准', '测试引用', 100, ?, ?)
                """,
                (standard_uuid, project["id"], timestamp, timestamp),
            )
            db.execute(
                """
                INSERT INTO special_indicators (
                    indicator_uuid, project_id, manual_standard_uuid, indicator_code,
                    indicator_name, description, sort_order, created_at, updated_at
                ) VALUES (?, ?, ?, 'S-01', '特殊指标', '特殊指标说明', 1, ?, ?)
                """,
                (str(uuid.uuid4()), project["id"], standard_uuid, timestamp, timestamp),
            )

        special_preview = report_generation.impact_preview(project["project_uuid"])
        self.assertEqual(special_preview["affected_blocks"], ["overall_evaluation.intro"])

        second = report_generation.create_generation_run(
            project["project_uuid"],
            GenerationRunWrite(expected_project_revision=first["project_revision"]),
        )
        with database.connect() as db:
            a2 = db.execute(
                "SELECT assessment_object_uuid FROM assessment_rows WHERE id = ?",
                (self.rows["A-2.02"],),
            ).fetchone()
            a4 = db.execute(
                "SELECT assessment_object_uuid FROM assessment_rows WHERE id = ?",
                (self.rows["A-4.06"],),
            ).fetchone()
            db.execute(
                """
                INSERT INTO result_correction_relations (
                    correction_uuid, project_id, a2_object_uuid, a2_metric_code,
                    a4_object_uuid, a4_metric_code, correction_kind,
                    original_references_json, created_at, updated_at
                ) VALUES (?, ?, ?, '通信数据完整性', ?, '重要数据传输完整性', 'integrity', ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    project["id"],
                    a2["assessment_object_uuid"],
                    a4["assessment_object_uuid"],
                    json.dumps(
                        {"a2_row_id": self.rows["A-2.02"], "a4_row_id": self.rows["A-4.06"]},
                        ensure_ascii=False,
                    ),
                    timestamp,
                    timestamp,
                ),
            )

        correction_preview = report_generation.impact_preview(project["project_uuid"])
        expected = set(report_generation.DERIVED_BLOCK_KEYS) - {
            "conclusion.system_summary",
            "risk_analysis.summary",
        }
        self.assertEqual(set(correction_preview["affected_blocks"]), expected)
        self.assertEqual(correction_preview["project_revision"], second["project_revision"])


if __name__ == "__main__":
    unittest.main()
