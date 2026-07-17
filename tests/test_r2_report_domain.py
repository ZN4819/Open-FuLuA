from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from app import database
from app.contracts import (
    FULL_REPORT_TEMPLATE_ASSET_SET_HASH,
    FULL_REPORT_TEMPLATE_EDITION,
    FULL_REPORT_TEMPLATE_PACKAGE_ID,
    FULL_REPORT_TEMPLATE_REVISION,
)
from app.report_schemas import (
    AssessmentObjectUpdate,
    AssessmentObjectWrite,
    BindingChoice,
    BindingConfirmWrite,
    BlockReorderItem,
    BlockReorderWrite,
    CorrectionRelationWrite,
    CorrectionRelationUpdate,
    CryptoProductUpdate,
    CryptoProductWrite,
    DistributionWrite,
    MemberWrite,
    ObjectRelationWrite,
    ObjectMergeWrite,
    ObjectSubsystemWrite,
    OnsiteRecord,
    OrganizationWrite,
    PhaseDatesWrite,
    ReportBlockCreate,
    ReportBlockPatch,
    ReportMetadataWrite,
    ReportSectionUpdate,
    SpecialIndicatorWrite,
    StandardWrite,
    SystemProfileWrite,
    TravelRecord,
)
from app.report_core.field_matrix import load_default_field_matrix
from app.services.projects import ProjectServiceError, transition_workflow
from app.services.report_domain import basic, blocks, objects, validation
from app.services.report_domain.errors import ReportDomainError


class R2ReportDomainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.previous_data_dir = os.environ.get("FULUA_DATA_DIR")
        self.previous_database = os.environ.get("FULUA_DATABASE_PATH")
        os.environ["FULUA_DATA_DIR"] = str(self.root)
        os.environ["FULUA_DATABASE_PATH"] = str(self.root / "data" / "app.db")
        database.init_db()
        self.project = self._create_full_report("R2 测试项目")

    def tearDown(self) -> None:
        if self.previous_data_dir is None:
            os.environ.pop("FULUA_DATA_DIR", None)
        else:
            os.environ["FULUA_DATA_DIR"] = self.previous_data_dir
        if self.previous_database is None:
            os.environ.pop("FULUA_DATABASE_PATH", None)
        else:
            os.environ["FULUA_DATABASE_PATH"] = self.previous_database
        self.temporary.cleanup()

    @staticmethod
    def _full_report_arguments() -> dict[str, str]:
        return {
            "project_type": "full_report",
            "template_package_id": FULL_REPORT_TEMPLATE_PACKAGE_ID,
            "template_edition": FULL_REPORT_TEMPLATE_EDITION,
            "template_revision": FULL_REPORT_TEMPLATE_REVISION,
            "template_asset_set_hash": FULL_REPORT_TEMPLATE_ASSET_SET_HASH,
        }

    def _create_full_report(self, name: str):
        return database.create_project(name, **self._full_report_arguments())

    @property
    def project_uuid(self) -> str:
        return str(self.project["project_uuid"])

    def _create_assessed_organization(self, name: str = "被测单位") -> dict:
        return basic.create_organization(
            self.project_uuid,
            OrganizationWrite(organization_type="assessed", name=name),
        )

    def _create_member(self, name: str, *, leader: bool = False) -> dict:
        return basic.create_member(
            self.project_uuid,
            MemberWrite(
                name=name,
                team_role="leader" if leader else "member",
                is_leader=leader,
                qualification_passed_at="2026-01-01",
            ),
        )

    def _insert_appendix_row(self, section_code: str, unit: str, object_name: str, *, project_id: int | None = None) -> int:
        with database.connect() as connection:
            target_project_id = project_id if project_id is not None else int(self.project["id"])
            section = connection.execute(
                "SELECT id FROM appendix_sections WHERE project_id=? AND code=?",
                (target_project_id, section_code),
            ).fetchone()
            sort_order = int(
                connection.execute(
                    "SELECT COUNT(*) FROM assessment_rows WHERE section_id=?",
                    (section["id"],),
                ).fetchone()[0]
            )
            timestamp = database.utc_now()
            cursor = connection.execute(
                """
                INSERT INTO assessment_rows (
                    section_id,unit,object_name,subsystem,record_text,sort_order,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (section["id"], unit, object_name, "", "", sort_order, timestamp, timestamp),
            )
            connection.execute(
                "INSERT INTO metric_results (row_id,object_score,unit_score,compliance) VALUES (?,?,?,?)",
                (cursor.lastrowid, "1.0000", "1.0000", "符合"),
            )
            return int(cursor.lastrowid)

    def _complete_phase_dates(self, member_uuid: str, *, revision: int = 1) -> dict:
        return basic.update_phase_dates(
            self.project_uuid,
            PhaseDatesWrite(
                expected_revision=revision,
                preparation_start="2026-01-01",
                preparation_end="2026-01-02",
                plan_start="2026-01-03",
                plan_end="2026-01-04",
                onsite_start="2026-01-10",
                onsite_end="2026-01-12",
                report_start="2026-01-13",
                report_end="2026-01-20",
                onsite_records=[
                    OnsiteRecord(entry_date="2026-01-10", exit_date="2026-01-10", member_uuids=[member_uuid]),
                    OnsiteRecord(entry_date="2026-01-12", exit_date="2026-01-12", member_uuids=[member_uuid]),
                ],
                travel_records=[
                    TravelRecord(local_project=False, start_date="2026-01-09", end_date="2026-01-13", member_uuids=[member_uuid])
                ],
                plan_review_date="2026-01-09",
                report_review_date=None,
                approval_date=None,
            ),
        )

    def _complete_profile(self, *, revision: int = 1, department: str = "") -> dict:
        return basic.update_system_profile(
            self.project_uuid,
            SystemProfileWrite(
                expected_revision=revision,
                system_name="特色系统",
                system_summary="用于测试的完整报告系统。",
                critical_infrastructure_status="not_recognized",
                critical_infrastructure_department=department,
                level_filing_status="not_filed",
                level_assessment_status="not_assessed",
                cloud_dependency="no",
                crypto_plan_status="none",
                operation_status="running",
                operation_started_at="2025-12-01",
                service_scope="local",
                no_crypto_products=True,
                selected_algorithms=["SM4"],
                other_algorithms=["自定义算法"],
                application_catalog=["核心业务应用"],
            ),
        )

    def test_overview_matrix_and_project_type_isolation(self) -> None:
        overview = validation.overview(self.project_uuid)
        self.assertEqual(overview["section_count"], 109)
        self.assertEqual(overview["completed_section_count"], 0)
        self.assertEqual(overview["template_package_id"], FULL_REPORT_TEMPLATE_PACKAGE_ID)
        public_matrix = validation.field_relations()
        self.assertEqual(len(public_matrix["relations"]), 70)
        public_json = json.dumps(public_matrix, ensure_ascii=False)
        internal_term = re.compile(
            r"(?<![A-Za-z0-9_])(?:ra|rk)(?![A-Za-z0-9_])",
            re.IGNORECASE,
        )
        self.assertIsNone(internal_term.search(public_json), public_json)
        public_technical_inputs = next(
            item
            for item in public_matrix["fields"]
            if item["field_id"] == "report.appendix_a.technical_inputs"
        )
        self.assertEqual(
            public_technical_inputs["entity_paths"],
            ["metric_results[*].d", "metric_results[*].a", "metric_results[*].k"],
        )
        public_technical_relation = next(
            item
            for item in public_matrix["relations"]
            if item["relation_id"] == "FRM-3.6.4.02"
        )
        self.assertEqual(
            public_technical_relation["reference_paths"],
            ["metric_results[*].d", "metric_results[*].a", "metric_results[*].k"],
        )
        self.assertEqual(public_technical_relation["constraint_expression"], "[internal-only]")

        machine_matrix = load_default_field_matrix()
        technical_inputs = next(
            item
            for item in machine_matrix.fields
            if item.field_id == "report.appendix_a.technical_inputs"
        )
        self.assertIn("metric_results[*].ra", technical_inputs.entity_paths)
        self.assertIn("metric_results[*].rk", technical_inputs.entity_paths)
        technical_relation = machine_matrix.relation("FRM-3.6.4.02")
        self.assertIn("metric_results[*].ra", technical_relation.reference_paths)
        self.assertIn("metric_results[*].rk", technical_relation.reference_paths)

        appendix = database.create_project("仅附录 A")
        with self.assertRaises(ReportDomainError) as captured:
            validation.overview(str(appendix["project_uuid"]))
        self.assertEqual(captured.exception.code, "REPORT_DOMAIN_NOT_AVAILABLE")

    def test_effective_client_metadata_patch_and_role_contract(self) -> None:
        assessed = self._create_assessed_organization()
        organizations = basic.list_organizations(self.project_uuid)
        self.assertIn(
            assessed["organization_uuid"],
            {item["organization_uuid"] for item in organizations},
        )
        metadata = basic.get_metadata(self.project_uuid)
        self.assertEqual(metadata["effective_client_organization_uuid"], assessed["organization_uuid"])
        client = basic.create_organization(
            self.project_uuid,
            OrganizationWrite(organization_type="client", name="委托单位"),
        )
        metadata = basic.get_metadata(self.project_uuid)
        self.assertEqual(metadata["effective_client_organization_uuid"], client["organization_uuid"])
        self.assertEqual(metadata["operator_organization_uuid"], assessed["organization_uuid"])

        compiler = self._create_member("编制人")
        reviewer = self._create_member("审核人")
        updated = basic.update_metadata(
            self.project_uuid,
            ReportMetadataWrite(
                expected_revision=metadata["revision"],
                report_number="BG-001",
                classification_level="三级",
                compiler_member_uuid=compiler["member_uuid"],
                reviewer_member_uuid=reviewer["member_uuid"],
            ),
        )
        patched = basic.update_metadata(
            self.project_uuid,
            ReportMetadataWrite(expected_revision=updated["revision"], confidentiality_level="内部"),
        )
        self.assertEqual(patched["report_number"], "BG-001")
        self.assertEqual(patched["compiler_member_uuid"], compiler["member_uuid"])
        self.assertEqual(patched["reviewer_member_uuid"], reviewer["member_uuid"])
        self.assertTrue(basic.report_number_availability(self.project_uuid, " BG-001 ")["available"])
        other = self._create_full_report("报告编号查重项目")
        other_availability = basic.report_number_availability(
            str(other["project_uuid"]),
            " bg-001 ",
        )
        self.assertFalse(other_availability["available"])
        self.assertEqual(other_availability["duplicate_project_count"], 1)
        self.assertEqual(other_availability["report_number"], "bg-001")
        with self.assertRaises(ReportDomainError) as stale:
            basic.update_metadata(
                self.project_uuid,
                ReportMetadataWrite(expected_revision=updated["revision"], report_number="BG-002"),
            )
        self.assertEqual(stale.exception.code, "REVISION_CONFLICT")

    def test_phase_dates_multi_site_coverage_and_member_references(self) -> None:
        member = self._create_member("现场成员")
        saved = self._complete_phase_dates(member["member_uuid"])
        self.assertEqual(saved["assessment_start"], "2026-01-01")
        self.assertEqual(saved["assessment_end"], "2026-01-20")
        self.assertEqual(saved["compiled_date"], "2026-01-20")
        self.assertFalse(saved["local_travel_not_applicable"])

        with self.assertRaises(ReportDomainError) as uncovered:
            basic.update_phase_dates(
                self.project_uuid,
                PhaseDatesWrite(
                    expected_revision=saved["revision"],
                    preparation_start="2026-01-01", preparation_end="2026-01-02",
                    plan_start="2026-01-03", plan_end="2026-01-04",
                    onsite_start="2026-01-10", onsite_end="2026-01-12",
                    report_start="2026-01-13", report_end="2026-01-20",
                    onsite_records=[OnsiteRecord(entry_date="2026-01-10", exit_date="2026-01-12", member_uuids=[member["member_uuid"]])],
                    travel_records=[TravelRecord(local_project=False, start_date="2026-01-11", end_date="2026-01-12", member_uuids=[member["member_uuid"]])],
                    plan_review_date="2026-01-09",
                ),
            )
        self.assertEqual(uncovered.exception.code, "TRAVEL_PERIOD_NOT_COVER_ONSITE")

        with self.assertRaises(ReportDomainError) as foreign_member:
            basic.update_phase_dates(
                self.project_uuid,
                PhaseDatesWrite(
                    expected_revision=saved["revision"],
                    preparation_start="2026-01-01", preparation_end="2026-01-02",
                    plan_start="2026-01-03", plan_end="2026-01-04",
                    onsite_start="2026-01-10", onsite_end="2026-01-10",
                    report_start="2026-01-11", report_end="2026-01-20",
                    onsite_records=[OnsiteRecord(entry_date="2026-01-10", exit_date="2026-01-10", member_uuids=["00000000-0000-0000-0000-000000000000"])],
                    travel_records=[TravelRecord(local_project=True, member_uuids=["00000000-0000-0000-0000-000000000000"])],
                    plan_review_date="2026-01-09",
                ),
            )
        self.assertEqual(foreign_member.exception.code, "PHASE_MEMBER_REFERENCE_INVALID")

    def test_system_profile_roundtrip_and_confirmable_warning(self) -> None:
        saved = self._complete_profile(department="仍保留的部门")
        self.assertEqual(saved["selected_algorithms"], ["SM4"])
        self.assertEqual(saved["other_algorithms"], ["自定义算法"])
        self.assertEqual(saved["application_catalog"], ["核心业务应用"])

        result = validation.validate_report(self.project_uuid)
        warning = next(item for item in result["issues"] if item["code"] == "UNSELECTED_BRANCH_HAS_VALUE" and item["field"] == "critical_infrastructure_department")
        confirmation = validation.confirm_warning(
            self.project_uuid,
            warning["relation_id"],
            warning["entity_path"],
            warning["code"],
            warning["details"]["source_hash"],
        )
        self.assertTrue(confirmation["confirmed"])
        confirmed = validation.validate_report(self.project_uuid)
        self.assertTrue(any(item["severity"] == "info" and item["details"].get("confirmed") for item in confirmed["issues"]))

        changed = self._complete_profile(revision=saved["revision"], department="变化后的部门")
        self.assertEqual(changed["critical_infrastructure_department"], "变化后的部门")
        with self.assertRaises(ReportDomainError) as stale:
            validation.confirm_warning(
                self.project_uuid,
                warning["relation_id"],
                warning["entity_path"],
                warning["code"],
                warning["details"]["source_hash"],
            )
        self.assertEqual(stale.exception.code, "WARNING_SOURCE_CHANGED")

        with self.assertRaises(ReportDomainError) as invalid_cloud:
            basic.update_system_profile(
                self.project_uuid,
                SystemProfileWrite(
                    expected_revision=changed["revision"],
                    system_name="特色系统",
                    critical_infrastructure_status="not_recognized",
                    level_filing_status="not_filed",
                    level_assessment_status="not_assessed",
                    cloud_dependency="yes",
                    crypto_plan_status="none",
                    operation_status="not_running",
                    construction_stage="建设中",
                    service_scope="local",
                    no_crypto_products=True,
                    selected_algorithms=["SM4"],
                ),
            )
        self.assertEqual(invalid_cloud.exception.code, "CLOUD_PLATFORM_FIELDS_REQUIRED")

    def test_crypto_products_and_standards_roundtrip(self) -> None:
        first = basic.create_crypto_product(
            self.project_uuid,
            CryptoProductWrite(name="密码机", model="M1", quantity_text="若干", use_mode="exclusive", classification="certified"),
        )
        self.assertEqual(first["name"], "密码机")
        self.assertEqual(first["normalized_quantity"], 1)
        basic.create_crypto_product(
            self.project_uuid,
            CryptoProductWrite(name="密码卡", quantity_text="2", use_mode="shared", classification="foreign"),
        )
        products = basic.list_crypto_products(self.project_uuid)
        self.assertEqual(products["summary"], {"total": 3, "exclusive": 1, "shared": 2, "certified": 1, "uncertified_domestic": 0, "foreign": 2})
        with self.assertRaises(ReportDomainError) as invalid_quantity:
            basic.create_crypto_product(
                self.project_uuid,
                CryptoProductWrite(name="错误产品", quantity_text="1.5", use_mode="shared", classification="certified"),
            )
        self.assertEqual(invalid_quantity.exception.code, "CRYPTO_PRODUCT_QUANTITY_INVALID")

        constants = basic.list_standards(self.project_uuid)
        self.assertEqual(len(constants), 5)
        self.assertTrue(all(item["kind"] == "template_constant" for item in constants))
        with self.assertRaises(ReportDomainError) as immutable:
            basic.delete_standard(self.project_uuid, constants[0]["standard_uuid"], constants[0]["revision"])
        self.assertEqual(immutable.exception.code, "TEMPLATE_CONSTANT_READ_ONLY")
        manual = basic.create_standard(self.project_uuid, StandardWrite(code="GM/X", name="补充标准"))
        basic.create_special_indicator(
            self.project_uuid,
            SpecialIndicatorWrite(manual_standard_uuid=manual["standard_uuid"], indicator_name="特殊指标"),
        )
        with self.assertRaises(ReportDomainError) as referenced:
            basic.delete_standard(self.project_uuid, manual["standard_uuid"], manual["revision"])
        self.assertEqual(referenced.exception.code, "REPORT_ENTITY_REFERENCED")

    def test_appendix_bindings_allow_one_object_for_multiple_indicator_rows(self) -> None:
        first_row = self._insert_appendix_row("A-2", "通信数据完整性", "通道一")
        second_row = self._insert_appendix_row("A-2", "通信过程中重要数据的机密性", "通道一")
        network = objects.create_object(
            self.project_uuid,
            AssessmentObjectWrite(object_type="network", name_snapshot="通道一", source_row_id=first_row),
        )
        confirmed = objects.confirm_bindings(
            self.project_uuid,
            BindingConfirmWrite(choices=[BindingChoice(source_row_id=second_row, object_uuid=network["object_uuid"])]),
        )
        self.assertEqual(confirmed["bound_count"], 1)
        projection = objects.get_projection(self.project_uuid, "table_3_5")
        self.assertEqual(projection["rows"], [{"object_name": "通道一"}])
        preview = objects.preview_bindings(self.project_uuid)
        self.assertEqual(len(preview["exact"]), 2)

        with self.assertRaises(ReportDomainError) as wrong_type:
            objects.create_object(
                self.project_uuid,
                AssessmentObjectWrite(object_type="application", name_snapshot="错误类型", source_row_id=second_row),
            )
        self.assertEqual(wrong_type.exception.code, "ASSESSMENT_OBJECT_TYPE_MISMATCH")

    def test_appendix_section_save_preserves_bound_row_identity(self) -> None:
        row_id = self._insert_appendix_row("A-2", "通信数据完整性", "原通道名称")
        bound = objects.create_object(
            self.project_uuid,
            AssessmentObjectWrite(
                object_type="network",
                name_snapshot="原通道名称",
                source_row_id=row_id,
            ),
        )

        database.replace_section_rows(
            int(self.project["id"]),
            "A-2",
            [
                {
                    "id": row_id,
                    "unit": "通信数据完整性",
                    "object_name": "更新后的通道名称",
                    "record_text": "保存后仍应保持中央对象绑定。",
                    "sort_order": 1,
                    "metric_result": {
                        "d": "√",
                        "a": "√",
                        "k": "√",
                        "ra": "1",
                        "rk": "1",
                    },
                }
            ],
        )

        with database.connect() as connection:
            section = database.get_section(int(self.project["id"]), "A-2", connection)
            saved_rows = database.list_assessment_rows(int(section["id"]), connection)
            saved_object = connection.execute(
                "SELECT * FROM assessment_objects WHERE object_uuid=?",
                (bound["object_uuid"],),
            ).fetchone()

        self.assertEqual(len(saved_rows), 1)
        self.assertEqual(saved_rows[0]["id"], row_id)
        self.assertEqual(saved_rows[0]["assessment_object_uuid"], bound["object_uuid"])
        self.assertEqual(saved_object["source_row_id"], row_id)
        self.assertEqual(saved_object["name_snapshot"], "更新后的通道名称")

    def test_binding_and_object_type_updates_cannot_corrupt_appendix_authority(self) -> None:
        first_row = self._insert_appendix_row("A-2", "通信数据完整性", "通道一")
        second_row = self._insert_appendix_row("A-2", "身份鉴别", "通道一")
        unbound_row = self._insert_appendix_row("A-2", "安全接入认证", "通道二")
        network = objects.create_object(
            self.project_uuid,
            AssessmentObjectWrite(object_type="network", name_snapshot="通道一", source_row_id=first_row),
        )
        objects.confirm_bindings(
            self.project_uuid,
            BindingConfirmWrite(
                choices=[BindingChoice(source_row_id=second_row, object_uuid=network["object_uuid"])]
            ),
        )
        other = objects.create_object(
            self.project_uuid,
            AssessmentObjectWrite(
                object_type="network",
                name_snapshot="通道二",
                source_section_code="A-2",
            ),
        )
        with self.assertRaises(ReportDomainError) as rebind:
            objects.confirm_bindings(
                self.project_uuid,
                BindingConfirmWrite(
                    choices=[
                        BindingChoice(source_row_id=unbound_row, object_uuid=other["object_uuid"]),
                        BindingChoice(source_row_id=second_row, object_uuid=other["object_uuid"]),
                    ]
                ),
            )
        self.assertEqual(rebind.exception.code, "APPENDIX_A_ROW_ALREADY_BOUND")
        with database.connect() as connection:
            still_unbound = connection.execute(
                "SELECT assessment_object_uuid FROM assessment_rows WHERE id=?",
                (unbound_row,),
            ).fetchone()[0]
        self.assertIsNone(still_unbound)

        with self.assertRaises(ReportDomainError) as type_change:
            objects.update_object(
                self.project_uuid,
                network["object_uuid"],
                AssessmentObjectUpdate(
                    expected_revision=network["revision"] + 1,
                    object_type="application",
                    name_snapshot="错误类型",
                    source_section_code="A-4",
                    source_row_id=None,
                ),
            )
        self.assertEqual(type_change.exception.code, "ASSESSMENT_OBJECT_TYPE_MISMATCH")

        application = objects.create_object(
            self.project_uuid,
            AssessmentObjectWrite(object_type="application", name_snapshot="应用对象"),
        )
        refreshed = next(
            item for item in objects.list_objects(self.project_uuid)
            if item["object_uuid"] == network["object_uuid"]
        )
        with self.assertRaises(ReportDomainError) as merge_type:
            objects.merge_object(
                self.project_uuid,
                network["object_uuid"],
                ObjectMergeWrite(
                    target_object_uuid=application["object_uuid"],
                    source_expected_revision=refreshed["revision"],
                    target_expected_revision=application["revision"],
                ),
            )
        self.assertEqual(merge_type.exception.code, "OBJECT_MERGE_TYPE_CONFLICT")

    def test_a4_subsystem_projection_relations_and_corrections(self) -> None:
        self._complete_profile()
        a2_row = self._insert_appendix_row("A-2", "通信过程中重要数据的机密性", "通道一")
        a4_row = self._insert_appendix_row("A-4", "重要数据传输机密性", "客户数据")
        a4_row_two = self._insert_appendix_row("A-4", "重要数据传输机密性", "交易数据")
        identity_row = self._insert_appendix_row("A-4", "身份鉴别", "客户数据")
        access_integrity_row = self._insert_appendix_row("A-4", "访问控制信息完整性", "客户数据")
        non_repudiation_row = self._insert_appendix_row("A-4", "不可否认性", "客户数据")
        channel = objects.create_object(self.project_uuid, AssessmentObjectWrite(object_type="network", name_snapshot="通道一", source_row_id=a2_row))
        data_one = objects.create_object(self.project_uuid, AssessmentObjectWrite(object_type="application", name_snapshot="客户数据", source_row_id=a4_row))
        data_two = objects.create_object(self.project_uuid, AssessmentObjectWrite(object_type="application", name_snapshot="交易数据", source_row_id=a4_row_two))
        subsystem = objects.upsert_subsystem(
            self.project_uuid,
            ObjectSubsystemWrite(object_uuid=data_one["object_uuid"], subsystem_name="核心业务应用", methods=["访谈", "配置检查"]),
        )
        objects.upsert_subsystem(
            self.project_uuid,
            ObjectSubsystemWrite(object_uuid=data_two["object_uuid"], subsystem_name="核心业务应用", methods=["文档审查"]),
        )
        objects.confirm_bindings(
            self.project_uuid,
            BindingConfirmWrite(
                choices=[
                    BindingChoice(source_row_id=identity_row, object_uuid=data_one["object_uuid"]),
                    BindingChoice(source_row_id=access_integrity_row, object_uuid=data_one["object_uuid"]),
                    BindingChoice(source_row_id=non_repudiation_row, object_uuid=data_one["object_uuid"]),
                ]
            ),
        )
        self.assertEqual(subsystem["methods"], ["访谈", "配置检查"])
        self.assertEqual(objects.get_projection(self.project_uuid, "table_3_7")["rows"], [{"object_name": "核心业务应用"}])
        table_4_4 = objects.get_projection(self.project_uuid, "table_4_4")
        transfer_group = next(
            row for row in table_4_4["rows"]
            if row["indicator"] == "重要数据传输机密性"
        )
        self.assertEqual(transfer_group["object_name"], "核心业务应用")
        self.assertEqual(len(transfer_group["source_records"]), 2)
        table_4_5 = objects.get_projection(self.project_uuid, "table_4_5")
        self.assertEqual([row["object_name"] for row in table_4_5["rows"]], ["客户数据"])
        table_4_6 = objects.get_projection(self.project_uuid, "table_4_6")
        customer = next(row for row in table_4_6["rows"] if row["object_name"] == "客户数据")
        self.assertEqual(customer["cells"]["transmission_confidentiality"]["compliance"], "符合")
        self.assertEqual(customer["cells"]["storage_integrity"]["source_indicator"], "访问控制信息完整性")
        self.assertEqual(customer["cells"]["storage_confidentiality"]["compliance"], "不适用")
        table_4_7 = objects.get_projection(self.project_uuid, "table_4_7")
        self.assertEqual([row["object_name"] for row in table_4_7["rows"]], ["客户数据"])

        correction = objects.create_correction_relation(
            self.project_uuid,
            CorrectionRelationWrite(
                a2_object_uuid=channel["object_uuid"],
                a4_object_uuid=data_one["object_uuid"],
                correction_kind="confidentiality",
                a2_metric_code="通信过程中重要数据的机密性",
                a4_metric_code="重要数据传输机密性",
                original_references={"a2_row_id": a2_row, "a4_row_id": a4_row},
            ),
        )
        self.assertEqual(correction["original_references"]["a2_row_id"], a2_row)
        second = objects.create_correction_relation(
            self.project_uuid,
            CorrectionRelationWrite(
                a2_object_uuid=channel["object_uuid"],
                a4_object_uuid=data_two["object_uuid"],
                correction_kind="confidentiality",
                a2_metric_code="通信过程中重要数据的机密性",
                a4_metric_code="重要数据传输机密性",
                original_references={"a2_row_id": a2_row, "a4_row_id": a4_row_two},
            ),
        )
        self.assertNotEqual(correction["correction_uuid"], second["correction_uuid"])
        with self.assertRaises(ReportDomainError) as wrong_metric:
            objects.create_correction_relation(
                self.project_uuid,
                CorrectionRelationWrite(
                    a2_object_uuid=channel["object_uuid"],
                    a4_object_uuid=data_two["object_uuid"],
                    correction_kind="integrity",
                    a2_metric_code="通信过程中重要数据的机密性",
                    a4_metric_code="重要数据传输完整性",
                ),
            )
        self.assertEqual(wrong_metric.exception.code, "CORRECTION_METRIC_PAIR_INVALID")

        parent = objects.create_object(self.project_uuid, AssessmentObjectWrite(object_type="other", name_snapshot="父对象"))
        child = objects.create_object(self.project_uuid, AssessmentObjectWrite(object_type="other", name_snapshot="子对象"))
        objects.create_object_relation(self.project_uuid, ObjectRelationWrite(source_object_uuid=parent["object_uuid"], target_object_uuid=child["object_uuid"], relation_type="contains"))
        with self.assertRaises(ReportDomainError) as cycle:
            objects.create_object_relation(self.project_uuid, ObjectRelationWrite(source_object_uuid=child["object_uuid"], target_object_uuid=parent["object_uuid"], relation_type="contains"))
        self.assertEqual(cycle.exception.code, "OBJECT_RELATION_CYCLE")

        conflicting_storage = self._insert_appendix_row("A-4", "重要数据存储完整性", "客户数据")
        objects.confirm_bindings(
            self.project_uuid,
            BindingConfirmWrite(
                choices=[BindingChoice(source_row_id=conflicting_storage, object_uuid=data_one["object_uuid"])]
            ),
        )
        with self.assertRaises(ReportDomainError) as projection_conflict:
            objects.get_projection(self.project_uuid, "table_4_6")
        self.assertEqual(projection_conflict.exception.code, "TABLE_4_6_INTEGRITY_MAPPING_CONFLICT")
        validation_result = validation.validate_report(self.project_uuid)
        self.assertTrue(
            any(item["code"] == "TABLE_4_6_INTEGRITY_MAPPING_CONFLICT" for item in validation_result["issues"])
        )

    def test_correction_original_references_are_authoritative(self) -> None:
        a2_row = self._insert_appendix_row("A-2", "通信过程中重要数据的机密性", "通道一")
        a4_row = self._insert_appendix_row("A-4", "重要数据传输机密性", "客户数据")
        other_a4_row = self._insert_appendix_row("A-4", "重要数据传输机密性", "交易数据")
        unbound_a4_row = self._insert_appendix_row("A-4", "重要数据传输机密性", "未绑定数据")
        channel = objects.create_object(self.project_uuid, AssessmentObjectWrite(object_type="network", name_snapshot="通道一", source_row_id=a2_row))
        data = objects.create_object(self.project_uuid, AssessmentObjectWrite(object_type="application", name_snapshot="客户数据", source_row_id=a4_row))
        other_data = objects.create_object(self.project_uuid, AssessmentObjectWrite(object_type="application", name_snapshot="交易数据", source_row_id=other_a4_row))

        def write(references: dict[str, int] | None = None) -> CorrectionRelationWrite:
            return CorrectionRelationWrite(
                a2_object_uuid=channel["object_uuid"],
                a4_object_uuid=data["object_uuid"],
                correction_kind="confidentiality",
                a2_metric_code="通信过程中重要数据的机密性",
                a4_metric_code="重要数据传输机密性",
                original_references=references or {},
            )

        for references, reason in (
            ({}, None),
            ({"a2_row_id": a4_row, "a4_row_id": a2_row}, "section_mismatch"),
            ({"a2_row_id": a2_row, "a4_row_id": unbound_a4_row}, "object_not_bound"),
            ({"a2_row_id": a2_row, "a4_row_id": other_a4_row}, "object_mismatch"),
        ):
            with self.subTest(reason=reason), self.assertRaises(ReportDomainError) as invalid:
                objects.create_correction_relation(self.project_uuid, write(references))
            self.assertEqual(invalid.exception.code, "CORRECTION_ORIGINAL_REFERENCE_INVALID")
            if reason:
                self.assertEqual(invalid.exception.details["reason"], reason)

        other_project = self._create_full_report("其他项目")
        cross_project_row = self._insert_appendix_row(
            "A-2",
            "通信过程中重要数据的机密性",
            "其他通道",
            project_id=int(other_project["id"]),
        )
        with self.assertRaises(ReportDomainError) as cross_project:
            objects.create_correction_relation(
                self.project_uuid,
                write({"a2_row_id": cross_project_row, "a4_row_id": a4_row}),
            )
        self.assertEqual(cross_project.exception.code, "CORRECTION_ORIGINAL_REFERENCE_INVALID")
        self.assertEqual(cross_project.exception.details["reason"], "project_mismatch")

        relation = objects.create_correction_relation(
            self.project_uuid,
            write({"a2_row_id": a2_row, "a4_row_id": a4_row}),
        )
        with self.assertRaises(ReportDomainError) as update_invalid:
            objects.update_correction_relation(
                self.project_uuid,
                relation["correction_uuid"],
                CorrectionRelationUpdate(
                    **write({"a2_row_id": a2_row, "a4_row_id": other_a4_row}).model_dump(),
                    expected_revision=relation["revision"],
                ),
            )
        self.assertEqual(update_invalid.exception.code, "CORRECTION_ORIGINAL_REFERENCE_INVALID")
        self.assertEqual(update_invalid.exception.details["reason"], "object_mismatch")
        self.assertNotEqual(other_data["object_uuid"], data["object_uuid"])

    def test_merge_rejects_cycle_created_by_endpoint_contraction(self) -> None:
        target = objects.create_object(self.project_uuid, AssessmentObjectWrite(object_type="other", name_snapshot="目标"))
        middle = objects.create_object(self.project_uuid, AssessmentObjectWrite(object_type="other", name_snapshot="中间"))
        source = objects.create_object(self.project_uuid, AssessmentObjectWrite(object_type="other", name_snapshot="来源"))
        objects.create_object_relation(self.project_uuid, ObjectRelationWrite(source_object_uuid=target["object_uuid"], target_object_uuid=middle["object_uuid"], relation_type="contains"))
        objects.create_object_relation(self.project_uuid, ObjectRelationWrite(source_object_uuid=middle["object_uuid"], target_object_uuid=source["object_uuid"], relation_type="contains"))

        with self.assertRaises(ReportDomainError) as cycle:
            objects.merge_object(
                self.project_uuid,
                source["object_uuid"],
                ObjectMergeWrite(
                    target_object_uuid=target["object_uuid"],
                    source_expected_revision=source["revision"],
                    target_expected_revision=target["revision"],
                ),
            )
        self.assertEqual(cycle.exception.code, "OBJECT_RELATION_CYCLE")
        self.assertEqual(
            {item["object_uuid"] for item in objects.list_objects(self.project_uuid)},
            {target["object_uuid"], middle["object_uuid"], source["object_uuid"]},
        )

    def test_projections_use_bound_appendix_row_name_not_editable_snapshot(self) -> None:
        row_id = self._insert_appendix_row("A-2", "通信数据完整性", "权威通道名称")
        created = objects.create_object(
            self.project_uuid,
            AssessmentObjectWrite(object_type="network", name_snapshot="伪造快照", source_row_id=row_id),
        )
        self.assertEqual(created["name_snapshot"], "权威通道名称")
        with database.connect() as connection:
            connection.execute(
                "UPDATE assessment_objects SET name_snapshot='被编辑的检索快照' WHERE object_uuid=?",
                (created["object_uuid"],),
            )
        self.assertEqual(
            objects.get_projection(self.project_uuid, "table_3_5")["rows"],
            [{"object_name": "权威通道名称"}],
        )
        table_4_2 = objects.get_projection(self.project_uuid, "table_4_2")
        self.assertEqual(table_4_2["rows"][0]["object_name"], "权威通道名称")
        self.assertNotIn("name_snapshot", table_4_2["rows"][0])

    def test_database_cas_detects_interleaved_update_and_stale_delete(self) -> None:
        metadata = basic.get_metadata(self.project_uuid)
        original_safe_json_size = basic.safe_json_size
        interleaved = False

        def bump_revision(value, **kwargs):
            nonlocal interleaved
            if not interleaved:
                interleaved = True
                with database.connect() as connection:
                    connection.execute(
                        "UPDATE report_metadata SET revision=revision+1 WHERE project_id=?",
                        (self.project["id"],),
                    )
            return original_safe_json_size(value, **kwargs)

        with patch.object(basic, "safe_json_size", side_effect=bump_revision):
            with self.assertRaises(ReportDomainError) as raced:
                basic.update_metadata(
                    self.project_uuid,
                    ReportMetadataWrite(expected_revision=metadata["revision"], report_number="不应覆盖"),
                )
        self.assertEqual(raced.exception.code, "REVISION_CONFLICT")
        current_metadata = basic.get_metadata(self.project_uuid)
        self.assertEqual(current_metadata["revision"], metadata["revision"] + 1)
        self.assertNotEqual(current_metadata["report_number"], "不应覆盖")

        product = basic.create_crypto_product(
            self.project_uuid,
            CryptoProductWrite(name="密码机", quantity_text="1", use_mode="exclusive", classification="certified"),
        )
        updated = basic.update_crypto_product(
            self.project_uuid,
            product["product_uuid"],
            CryptoProductUpdate(
                expected_revision=product["revision"],
                name="密码机二版",
                quantity_text="1",
                use_mode="exclusive",
                classification="certified",
            ),
        )
        with self.assertRaises(ReportDomainError) as stale_delete:
            basic.delete_crypto_product(self.project_uuid, product["product_uuid"], product["revision"])
        self.assertEqual(stale_delete.exception.code, "REVISION_CONFLICT")
        self.assertEqual(basic.list_crypto_products(self.project_uuid)["items"][0]["name"], "密码机二版")
        basic.delete_crypto_product(self.project_uuid, product["product_uuid"], updated["revision"])

    def test_structured_blocks_reject_html_and_preserve_revision(self) -> None:
        sections = blocks.list_sections(self.project_uuid)
        editable = next(item for item in sections if item["section_key"] == "chapter.1.1")
        first = blocks.create_block(
            self.project_uuid,
            editable["section_uuid"],
            ReportBlockCreate(block_type="paragraph", payload={"text": "测评目的正文"}),
        )
        second = blocks.create_block(
            self.project_uuid,
            editable["section_uuid"],
            ReportBlockCreate(block_type="bullet_list", payload={"items": ["第一项", "第二项"]}),
        )
        updated = blocks.update_block(
            self.project_uuid,
            first["block_uuid"],
            ReportBlockPatch(expected_revision=first["revision"], payload={"text": "更新后的正文"}),
        )
        with self.assertRaises(ReportDomainError) as stale:
            blocks.update_block(
                self.project_uuid,
                first["block_uuid"],
                ReportBlockPatch(expected_revision=first["revision"], payload={"text": "覆盖"}),
            )
        self.assertEqual(stale.exception.code, "REVISION_CONFLICT")
        reordered = blocks.reorder_blocks(
            self.project_uuid,
            BlockReorderWrite(
                section_uuid=editable["section_uuid"],
                items=[
                    BlockReorderItem(block_uuid=updated["block_uuid"], sort_order=1, expected_revision=updated["revision"]),
                    BlockReorderItem(block_uuid=second["block_uuid"], sort_order=0, expected_revision=second["revision"]),
                ],
            ),
        )
        self.assertEqual([item["block_uuid"] for item in reordered], [second["block_uuid"], first["block_uuid"]])
        numbered = blocks.create_block(
            self.project_uuid,
            editable["section_uuid"],
            ReportBlockCreate(block_type="numbered_list", payload={"items": ["步骤一"]}),
        )
        key_value = blocks.create_block(
            self.project_uuid,
            editable["section_uuid"],
            ReportBlockCreate(
                block_type="key_value_table",
                payload={"rows": [{"key": "测评对象", "value": "核心系统"}]},
            ),
        )
        data_table = blocks.create_block(
            self.project_uuid,
            editable["section_uuid"],
            ReportBlockCreate(
                block_type="data_table",
                payload={
                    "schema_version": "1.0",
                    "columns": [{"key": "name", "label": "名称"}],
                    "rows": [{"name": "数据一"}],
                },
            ),
        )
        reference = blocks.create_block(
            self.project_uuid,
            editable["section_uuid"],
            ReportBlockCreate(
                block_type="reference",
                payload={"target_uuid": editable["section_uuid"], "label": "本节"},
            ),
        )
        self.assertEqual(numbered["payload"], {"items": ["步骤一"]})
        self.assertEqual(key_value["payload"]["rows"][0]["key"], "测评对象")
        self.assertEqual(data_table["payload"]["schema_version"], "1.0")
        self.assertEqual(reference["payload"]["target_uuid"], editable["section_uuid"])
        block_reference = blocks.create_block(
            self.project_uuid,
            editable["section_uuid"],
            ReportBlockCreate(
                block_type="reference",
                payload={"target_uuid": numbered["block_uuid"], "label": "步骤列表"},
            ),
        )
        self.assertEqual(block_reference["payload"]["target_uuid"], numbered["block_uuid"])
        with self.assertRaises(ReportDomainError) as referenced_delete:
            blocks.delete_block(self.project_uuid, numbered["block_uuid"], numbered["revision"])
        self.assertEqual(referenced_delete.exception.code, "REPORT_ENTITY_REFERENCED")
        with self.assertRaises(ReportDomainError) as html:
            blocks.create_block(
                self.project_uuid,
                editable["section_uuid"],
                ReportBlockCreate(block_type="paragraph", payload={"text": "<strong>不允许</strong>"}),
            )
        self.assertEqual(html.exception.code, "BLOCK_FORBIDDEN_CONTENT")
        with self.assertRaises(ReportDomainError) as ooxml:
            blocks.create_block(
                self.project_uuid,
                editable["section_uuid"],
                ReportBlockCreate(block_type="paragraph", payload={"text": "<w:p>非法 OOXML</w:p>"}),
            )
        self.assertEqual(ooxml.exception.code, "BLOCK_FORBIDDEN_CONTENT")
        with self.assertRaises(ReportDomainError) as oversized:
            blocks.create_block(
                self.project_uuid,
                editable["section_uuid"],
                ReportBlockCreate(block_type="paragraph", payload={"text": "长" * 20_001}),
            )
        self.assertEqual(oversized.exception.code, "BLOCK_PAYLOAD_INVALID")
        with self.assertRaises(ReportDomainError) as generated:
            blocks.create_block(
                self.project_uuid,
                editable["section_uuid"],
                ReportBlockCreate(block_type="generated", payload={"status": "伪生成"}),
            )
        self.assertEqual(generated.exception.code, "BLOCK_TYPE_NOT_ALLOWED")
        with self.assertRaises(ValidationError):
            ReportBlockCreate.model_validate({"block_type": "unknown", "payload": {}})

        foreign_project = self._create_full_report("跨项目块引用")
        foreign_section = next(
            item
            for item in blocks.list_sections(str(foreign_project["project_uuid"]))
            if item["section_key"] == "chapter.1.1"
        )
        with self.assertRaises(ReportDomainError) as cross_project_reference:
            blocks.create_block(
                self.project_uuid,
                editable["section_uuid"],
                ReportBlockCreate(
                    block_type="reference",
                    payload={"target_uuid": foreign_section["section_uuid"], "label": "越界引用"},
                ),
            )
        self.assertEqual(cross_project_reference.exception.code, "REPORT_REFERENCE_TARGET_INVALID")

        with database.connect() as connection:
            fixed_uuid = connection.execute(
                "SELECT block_uuid FROM report_blocks WHERE project_id=? AND source_kind='template_constant' LIMIT 1",
                (self.project["id"],),
            ).fetchone()["block_uuid"]
        with self.assertRaises(ReportDomainError) as readonly:
            blocks.update_block(
                self.project_uuid,
                fixed_uuid,
                ReportBlockPatch(expected_revision=1, payload={"status": "not_generated"}),
            )
        self.assertEqual(readonly.exception.code, "BLOCK_READ_ONLY")

        generated_section = next(item for item in sections if item["section_type"] == "generated")
        with self.assertRaises(ReportDomainError) as readonly_section:
            blocks.update_section(
                self.project_uuid,
                generated_section["section_uuid"],
                ReportSectionUpdate(
                    expected_revision=generated_section["revision"],
                    completion_status="complete",
                ),
            )
        self.assertEqual(readonly_section.exception.code, "SECTION_READ_ONLY")

        phase_section = next(item for item in sections if item["section_key"] == "chapter.1.3")
        phase_detail = blocks.get_section(self.project_uuid, phase_section["section_uuid"])
        self.assertTrue(any(issue["code"] == "REPORT_PHASE_DATES_INCOMPLETE" for issue in phase_detail["issues"]))

    def test_figure_blocks_require_same_project_evidence_uuid(self) -> None:
        editable = next(
            item
            for item in blocks.list_sections(self.project_uuid)
            if item["section_key"] == "chapter.1.1"
        )
        image = database.create_evidence_image(
            int(self.project["id"]),
            "A-1",
            {
                "file_path": "uploads/r2/figure.png",
                "original_name": "figure.png",
                "caption": "证据图片",
                "alt_text": "证据图片",
            },
        )
        figure = blocks.create_block(
            self.project_uuid,
            editable["section_uuid"],
            ReportBlockCreate(
                block_type="figure",
                payload={"figure_uuid": image["evidence_uuid"], "caption": "现场证据"},
            ),
        )
        self.assertEqual(figure["payload"]["figure_uuid"], image["evidence_uuid"])
        with self.assertRaises(ValueError):
            database.delete_evidence_image(int(image["id"]))

        foreign = self._create_full_report("图片跨项目测试")
        foreign_image = database.create_evidence_image(
            int(foreign["id"]),
            "A-1",
            {"file_path": "uploads/r2/foreign.png", "original_name": "foreign.png"},
        )
        with self.assertRaises(ReportDomainError) as cross_project:
            blocks.create_block(
                self.project_uuid,
                editable["section_uuid"],
                ReportBlockCreate(
                    block_type="figure",
                    payload={"figure_uuid": foreign_image["evidence_uuid"]},
                ),
            )
        self.assertEqual(cross_project.exception.code, "REPORT_REFERENCE_TARGET_INVALID")

        with self.assertRaises(ValidationError):
            ReportBlockCreate.model_validate(
                {
                    "block_key": "client.must.not.choose",
                    "block_type": "paragraph",
                    "payload": {"text": "非法客户端键"},
                }
            )

    def test_ready_for_review_uses_authoritative_r2_validation(self) -> None:
        self._create_assessed_organization()
        compiler = self._create_member("编制人")
        self._create_member("项目组成员")
        metadata = basic.get_metadata(self.project_uuid)
        basic.update_metadata(
            self.project_uuid,
            ReportMetadataWrite(
                expected_revision=metadata["revision"],
                report_number="BG-R2-READY",
                classification_level="三级",
                compiler_member_uuid=compiler["member_uuid"],
            ),
        )
        self._complete_phase_dates(compiler["member_uuid"])
        basic.update_distribution(
            self.project_uuid,
            DistributionWrite(expected_revision=1, regulator_copies=1, client_copies=1, assessment_copies=1),
        )
        self._complete_profile()

        result = validation.validate_report(self.project_uuid)
        incomplete = [
            issue for issue in result["issues"] if issue["code"] == "REPORT_SECTION_INCOMPLETE"
        ]
        with database.connect() as connection:
            required_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM report_sections
                    WHERE project_id = ?
                      AND section_type IN ('form', 'blocks')
                      AND edit_policy <> 'readonly'
                    """,
                    (self.project["id"],),
                ).fetchone()[0]
            )
        self.assertGreater(required_count, 0)
        self.assertEqual(len(incomplete), required_count)
        self.assertEqual(incomplete[0]["relation_id"], "R2-SECTION-COMPLETION")
        self.assertEqual(incomplete[0]["field"], "completion_status")
        self.assertEqual(incomplete[0]["target"], incomplete[0]["details"]["section_key"])
        with self.assertRaises(ProjectServiceError) as blocked:
            transition_workflow(self.project_uuid, "ready-for-review")
        self.assertEqual(blocked.exception.code, "REPORT_VALIDATION_FAILED")

        with database.connect() as connection:
            connection.execute(
                """
                UPDATE report_sections
                SET completion_status = 'complete', revision = revision + 1, updated_at = ?
                WHERE project_id = ?
                  AND section_type IN ('form', 'blocks')
                  AND edit_policy <> 'readonly'
                """,
                (database.utc_now(), self.project["id"]),
            )
            excluded_incomplete = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM report_sections
                    WHERE project_id = ?
                      AND completion_status <> 'complete'
                      AND (section_type IN ('generated', 'appendix_a', 'appendix_b')
                           OR edit_policy = 'readonly')
                    """,
                    (self.project["id"],),
                ).fetchone()[0]
            )
        self.assertGreater(excluded_incomplete, 0)

        result = validation.validate_report(self.project_uuid)
        self.assertEqual(result["errors"], 0, json.dumps(result["issues"], ensure_ascii=False, indent=2))
        self.assertFalse(any(issue["code"] == "REPORT_SECTION_INCOMPLETE" for issue in result["issues"]))
        with self.assertRaises(ProjectServiceError) as derived_blocked:
            transition_workflow(self.project_uuid, "ready-for-review")
        self.assertEqual(derived_blocked.exception.code, "R3_CONTEXT_STALE")
        with patch(
            "app.services.report_generation.get_projection_context",
            return_value={"consistency": {"status": "valid"}},
        ):
            updated = transition_workflow(self.project_uuid, "ready-for-review")
        self.assertEqual(updated["workflow_status"], "ready_for_review")


if __name__ == "__main__":
    unittest.main()
