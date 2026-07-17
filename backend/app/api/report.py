from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Query

from ..report_schemas import (
    AppendixTransmissionRelationWrite,
    AssessmentObjectUpdate,
    AssessmentObjectWrite,
    BindingConfirmWrite,
    BlockReorderWrite,
    CorrectionRelationUpdate,
    CorrectionRelationWrite,
    ConsistencyCheckWrite,
    CryptoProductUpdate,
    CryptoProductWrite,
    DerivedBlockConfirmationWrite,
    DerivedBlockOverrideWrite,
    DistributionWrite,
    GenerationRunWrite,
    MemberUpdate,
    MemberWrite,
    ObjectMergeWrite,
    ObjectRelationUpdate,
    ObjectRelationWrite,
    ObjectSubsystemWrite,
    OrganizationUpdate,
    OrganizationWrite,
    PhaseDatesWrite,
    ReportBlockCreate,
    ReportBlockPatch,
    ReportMetadataWrite,
    ReportSectionUpdate,
    RiskUpdateWrite,
    SpecialIndicatorUpdate,
    SpecialIndicatorWrite,
    StandardUpdate,
    StandardWrite,
    SystemProfileWrite,
    WarningConfirmationWrite,
)
from ..services import report_generation
from ..services.report_domain import basic, blocks, objects, validation
from ..services.report_domain.errors import ReportDomainError


router = APIRouter(prefix="/projects/{project_uuid}/report", tags=["report"])


def _match_revision(expected_revision: int, if_match: str | None, project_uuid: str) -> None:
    if if_match is None:
        return
    normalized = if_match.strip().strip('"')
    if normalized != str(expected_revision):
        raise ReportDomainError(
            "REVISION_HEADER_MISMATCH",
            "If-Match 与请求体中的 expected_revision 不一致。",
            status_code=422,
            project_uuid=project_uuid,
            field="expected_revision",
        )


@router.get("/overview")
def get_overview(project_uuid: str) -> dict[str, Any]:
    return validation.overview(project_uuid)


@router.get("/field-relations")
def get_field_relations(project_uuid: str) -> dict[str, Any]:
    # overview 先执行项目类型/模板绑定校验。
    validation.overview(project_uuid)
    return validation.field_relations()


@router.post("/validate")
def validate_report(project_uuid: str) -> dict[str, Any]:
    return validation.validate_report(project_uuid)


@router.post("/warnings/confirm")
def confirm_warning(project_uuid: str, payload: WarningConfirmationWrite) -> dict[str, Any]:
    return validation.confirm_warning(project_uuid, payload.relation_id, payload.entity_path, payload.warning_code, payload.source_hash)


@router.get("/metadata")
def get_metadata(project_uuid: str) -> dict[str, Any]:
    return basic.get_metadata(project_uuid)


@router.get("/metadata/report-number-availability")
def report_number_availability(
    project_uuid: str,
    report_number: str = Query(max_length=120),
) -> dict[str, Any]:
    return basic.report_number_availability(project_uuid, report_number)


@router.put("/metadata")
def update_metadata(project_uuid: str, payload: ReportMetadataWrite, if_match: str | None = Header(default=None)) -> dict[str, Any]:
    _match_revision(payload.expected_revision, if_match, project_uuid)
    return basic.update_metadata(project_uuid, payload)


@router.get("/organizations")
def list_organizations(project_uuid: str) -> list[dict[str, Any]]:
    return basic.list_organizations(project_uuid)


@router.post("/organizations", status_code=201)
def create_organization(project_uuid: str, payload: OrganizationWrite) -> dict[str, Any]:
    return basic.create_organization(project_uuid, payload)


@router.put("/organizations/{organization_uuid}")
def update_organization(project_uuid: str, organization_uuid: str, payload: OrganizationUpdate, if_match: str | None = Header(default=None)) -> dict[str, Any]:
    _match_revision(payload.expected_revision, if_match, project_uuid)
    return basic.update_organization(project_uuid, organization_uuid, payload)


@router.delete("/organizations/{organization_uuid}")
def delete_organization(project_uuid: str, organization_uuid: str, expected_revision: int = Query(ge=0), if_match: str | None = Header(default=None)) -> dict[str, Any]:
    _match_revision(expected_revision, if_match, project_uuid)
    return basic.delete_organization(project_uuid, organization_uuid, expected_revision)


@router.get("/members")
def list_members(project_uuid: str) -> list[dict[str, Any]]:
    return basic.list_members(project_uuid)


@router.post("/members", status_code=201)
def create_member(project_uuid: str, payload: MemberWrite) -> dict[str, Any]:
    return basic.create_member(project_uuid, payload)


@router.put("/members/{member_uuid}")
def update_member(project_uuid: str, member_uuid: str, payload: MemberUpdate, if_match: str | None = Header(default=None)) -> dict[str, Any]:
    _match_revision(payload.expected_revision, if_match, project_uuid)
    return basic.update_member(project_uuid, member_uuid, payload)


@router.delete("/members/{member_uuid}")
def delete_member(project_uuid: str, member_uuid: str, expected_revision: int = Query(ge=0), if_match: str | None = Header(default=None)) -> dict[str, Any]:
    _match_revision(expected_revision, if_match, project_uuid)
    return basic.delete_member(project_uuid, member_uuid, expected_revision)


@router.get("/phase-dates")
def get_phase_dates(project_uuid: str) -> dict[str, Any]:
    return basic.get_phase_dates(project_uuid)


@router.put("/phase-dates")
def update_phase_dates(project_uuid: str, payload: PhaseDatesWrite, if_match: str | None = Header(default=None)) -> dict[str, Any]:
    _match_revision(payload.expected_revision, if_match, project_uuid)
    return basic.update_phase_dates(project_uuid, payload)


@router.get("/distribution")
def get_distribution(project_uuid: str) -> dict[str, Any]:
    return basic.get_distribution(project_uuid)


@router.put("/distribution")
def update_distribution(project_uuid: str, payload: DistributionWrite, if_match: str | None = Header(default=None)) -> dict[str, Any]:
    _match_revision(payload.expected_revision, if_match, project_uuid)
    return basic.update_distribution(project_uuid, payload)


@router.get("/system-profile")
def get_system_profile(project_uuid: str) -> dict[str, Any]:
    return basic.get_system_profile(project_uuid)


@router.put("/system-profile")
def update_system_profile(project_uuid: str, payload: SystemProfileWrite, if_match: str | None = Header(default=None)) -> dict[str, Any]:
    _match_revision(payload.expected_revision, if_match, project_uuid)
    return basic.update_system_profile(project_uuid, payload)


@router.get("/crypto-products")
def list_crypto_products(project_uuid: str) -> dict[str, Any]:
    return basic.list_crypto_products(project_uuid)


@router.post("/crypto-products", status_code=201)
def create_crypto_product(project_uuid: str, payload: CryptoProductWrite) -> dict[str, Any]:
    return basic.create_crypto_product(project_uuid, payload)


@router.put("/crypto-products/{product_uuid}")
def update_crypto_product(project_uuid: str, product_uuid: str, payload: CryptoProductUpdate, if_match: str | None = Header(default=None)) -> dict[str, Any]:
    _match_revision(payload.expected_revision, if_match, project_uuid)
    return basic.update_crypto_product(project_uuid, product_uuid, payload)


@router.delete("/crypto-products/{product_uuid}")
def delete_crypto_product(project_uuid: str, product_uuid: str, expected_revision: int = Query(ge=0), if_match: str | None = Header(default=None)) -> dict[str, Any]:
    _match_revision(expected_revision, if_match, project_uuid)
    return basic.delete_crypto_product(project_uuid, product_uuid, expected_revision)


@router.get("/standards")
def list_standards(project_uuid: str) -> list[dict[str, Any]]:
    return basic.list_standards(project_uuid)


@router.post("/standards", status_code=201)
def create_standard(project_uuid: str, payload: StandardWrite) -> dict[str, Any]:
    return basic.create_standard(project_uuid, payload)


@router.put("/standards/{standard_uuid}")
def update_standard(project_uuid: str, standard_uuid: str, payload: StandardUpdate, if_match: str | None = Header(default=None)) -> dict[str, Any]:
    _match_revision(payload.expected_revision, if_match, project_uuid)
    return basic.update_standard(project_uuid, standard_uuid, payload)


@router.delete("/standards/{standard_uuid}")
def delete_standard(project_uuid: str, standard_uuid: str, expected_revision: int = Query(ge=0), if_match: str | None = Header(default=None)) -> dict[str, Any]:
    _match_revision(expected_revision, if_match, project_uuid)
    return basic.delete_standard(project_uuid, standard_uuid, expected_revision)


@router.get("/special-indicators")
def list_special_indicators(project_uuid: str) -> list[dict[str, Any]]:
    return basic.list_special_indicators(project_uuid)


@router.post("/special-indicators", status_code=201)
def create_special_indicator(project_uuid: str, payload: SpecialIndicatorWrite) -> dict[str, Any]:
    return basic.create_special_indicator(project_uuid, payload)


@router.put("/special-indicators/{indicator_uuid}")
def update_special_indicator(project_uuid: str, indicator_uuid: str, payload: SpecialIndicatorUpdate, if_match: str | None = Header(default=None)) -> dict[str, Any]:
    _match_revision(payload.expected_revision, if_match, project_uuid)
    return basic.update_special_indicator(project_uuid, indicator_uuid, payload)


@router.delete("/special-indicators/{indicator_uuid}")
def delete_special_indicator(project_uuid: str, indicator_uuid: str, expected_revision: int = Query(ge=0), if_match: str | None = Header(default=None)) -> dict[str, Any]:
    _match_revision(expected_revision, if_match, project_uuid)
    return basic.delete_special_indicator(project_uuid, indicator_uuid, expected_revision)


@router.get("/objects")
def list_objects(project_uuid: str) -> list[dict[str, Any]]:
    return objects.list_objects(project_uuid)


@router.post("/objects", status_code=201)
def create_object(project_uuid: str, payload: AssessmentObjectWrite) -> dict[str, Any]:
    return objects.create_object(project_uuid, payload)


@router.post("/objects/duplicate-candidates")
def duplicate_candidates(project_uuid: str) -> list[dict[str, Any]]:
    return objects.duplicate_candidates(project_uuid)


@router.put("/objects/{object_uuid}")
def update_object(project_uuid: str, object_uuid: str, payload: AssessmentObjectUpdate, if_match: str | None = Header(default=None)) -> dict[str, Any]:
    _match_revision(payload.expected_revision, if_match, project_uuid)
    return objects.update_object(project_uuid, object_uuid, payload)


@router.delete("/objects/{object_uuid}")
def delete_object(project_uuid: str, object_uuid: str, expected_revision: int = Query(ge=0), if_match: str | None = Header(default=None)) -> dict[str, Any]:
    _match_revision(expected_revision, if_match, project_uuid)
    return objects.delete_object(project_uuid, object_uuid, expected_revision)


@router.post("/objects/{source_uuid}/merge")
def merge_object(project_uuid: str, source_uuid: str, payload: ObjectMergeWrite) -> dict[str, Any]:
    return objects.merge_object(project_uuid, source_uuid, payload)


@router.post("/appendix-a-bindings/preview")
def preview_bindings(project_uuid: str) -> dict[str, Any]:
    return objects.preview_bindings(project_uuid)


@router.post("/appendix-a-bindings/confirm")
def confirm_bindings(project_uuid: str, payload: BindingConfirmWrite) -> dict[str, Any]:
    return objects.confirm_bindings(project_uuid, payload)


@router.get("/assessment-object-subsystems")
def list_subsystems(project_uuid: str) -> list[dict[str, Any]]:
    return objects.list_subsystems(project_uuid)


@router.put("/assessment-object-subsystems")
def upsert_subsystem(project_uuid: str, payload: ObjectSubsystemWrite) -> dict[str, Any]:
    return objects.upsert_subsystem(project_uuid, payload)


@router.get("/object-relations")
def list_object_relations(project_uuid: str) -> list[dict[str, Any]]:
    return objects.list_object_relations(project_uuid)


@router.post("/object-relations", status_code=201)
def create_object_relation(project_uuid: str, payload: ObjectRelationWrite) -> dict[str, Any]:
    return objects.create_object_relation(project_uuid, payload)


@router.put("/object-relations/{relation_uuid}")
def update_object_relation(project_uuid: str, relation_uuid: str, payload: ObjectRelationUpdate, if_match: str | None = Header(default=None)) -> dict[str, Any]:
    _match_revision(payload.expected_revision, if_match, project_uuid)
    return objects.update_object_relation(project_uuid, relation_uuid, payload)


@router.delete("/object-relations/{relation_uuid}")
def delete_object_relation(project_uuid: str, relation_uuid: str, expected_revision: int = Query(ge=0), if_match: str | None = Header(default=None)) -> dict[str, Any]:
    _match_revision(expected_revision, if_match, project_uuid)
    return objects.delete_object_relation(project_uuid, relation_uuid, expected_revision)


@router.get("/result-correction-relations")
def list_correction_relations(project_uuid: str) -> list[dict[str, Any]]:
    return objects.list_correction_relations(project_uuid)


@router.get("/appendix-transmission-relations")
def get_appendix_transmission_relations(project_uuid: str) -> dict[str, Any]:
    return objects.get_appendix_transmission_relations(project_uuid)


@router.put("/appendix-transmission-relations")
def put_appendix_transmission_relation(
    project_uuid: str,
    payload: AppendixTransmissionRelationWrite,
    if_match: str | None = Header(default=None),
) -> dict[str, Any]:
    if payload.expected_revision is not None:
        _match_revision(payload.expected_revision, if_match, project_uuid)
    elif if_match is not None:
        raise ReportDomainError(
            "REVISION_HEADER_MISMATCH",
            "新建关系时 expected_revision 必须为 null，且不应提供 If-Match。",
            status_code=422,
            project_uuid=project_uuid,
            field="expected_revision",
        )
    return objects.put_appendix_transmission_relation(project_uuid, payload)


@router.post("/result-correction-relations", status_code=201)
def create_correction_relation(project_uuid: str, payload: CorrectionRelationWrite) -> dict[str, Any]:
    return objects.create_correction_relation(project_uuid, payload)


@router.put("/result-correction-relations/{correction_uuid}")
def update_correction_relation(project_uuid: str, correction_uuid: str, payload: CorrectionRelationUpdate, if_match: str | None = Header(default=None)) -> dict[str, Any]:
    _match_revision(payload.expected_revision, if_match, project_uuid)
    return objects.update_correction_relation(project_uuid, correction_uuid, payload)


@router.delete("/result-correction-relations/{correction_uuid}")
def delete_correction_relation(project_uuid: str, correction_uuid: str, expected_revision: int = Query(ge=0), if_match: str | None = Header(default=None)) -> dict[str, Any]:
    _match_revision(expected_revision, if_match, project_uuid)
    return objects.delete_correction_relation(project_uuid, correction_uuid, expected_revision)


@router.get("/projections/{projection_id}")
def get_projection(project_uuid: str, projection_id: str) -> dict[str, Any]:
    return objects.get_projection(project_uuid, projection_id)


@router.get("/sections")
def list_sections(project_uuid: str) -> list[dict[str, Any]]:
    return blocks.list_sections(project_uuid)


@router.get("/sections/{section_uuid}")
def get_section(project_uuid: str, section_uuid: str) -> dict[str, Any]:
    return blocks.get_section(project_uuid, section_uuid)


@router.put("/sections/{section_uuid}")
def update_section(project_uuid: str, section_uuid: str, payload: ReportSectionUpdate, if_match: str | None = Header(default=None)) -> dict[str, Any]:
    _match_revision(payload.expected_revision, if_match, project_uuid)
    return blocks.update_section(project_uuid, section_uuid, payload)


@router.post("/sections/{section_uuid}/blocks", status_code=201)
def create_block(project_uuid: str, section_uuid: str, payload: ReportBlockCreate) -> dict[str, Any]:
    return blocks.create_block(project_uuid, section_uuid, payload)


@router.put("/blocks/{block_uuid}")
def update_block(project_uuid: str, block_uuid: str, payload: ReportBlockPatch, if_match: str | None = Header(default=None)) -> dict[str, Any]:
    _match_revision(payload.expected_revision, if_match, project_uuid)
    return blocks.update_block(project_uuid, block_uuid, payload)


@router.delete("/blocks/{block_uuid}")
def delete_block(project_uuid: str, block_uuid: str, expected_revision: int = Query(ge=0), if_match: str | None = Header(default=None)) -> dict[str, Any]:
    _match_revision(expected_revision, if_match, project_uuid)
    return blocks.delete_block(project_uuid, block_uuid, expected_revision)


@router.post("/blocks/reorder")
def reorder_blocks(project_uuid: str, payload: BlockReorderWrite) -> list[dict[str, Any]]:
    return blocks.reorder_blocks(project_uuid, payload)


@router.post("/generation/impact-preview")
def preview_generation_impact(project_uuid: str) -> dict[str, Any]:
    return report_generation.impact_preview(project_uuid)


@router.post("/generation/runs", status_code=201)
def create_generation_run(project_uuid: str, payload: GenerationRunWrite) -> dict[str, Any]:
    return report_generation.create_generation_run(project_uuid, payload)


@router.get("/generation/runs/{run_uuid}")
def get_generation_run(project_uuid: str, run_uuid: str) -> dict[str, Any]:
    return report_generation.get_generation_run(project_uuid, run_uuid)


@router.get("/generation/review")
def get_generation_review(project_uuid: str) -> dict[str, Any]:
    return report_generation.review_state(project_uuid)


@router.get("/findings")
def list_report_findings(project_uuid: str) -> dict[str, Any]:
    return report_generation.list_findings(project_uuid)


@router.get("/risks")
def list_report_risks(project_uuid: str) -> dict[str, Any]:
    return report_generation.list_risks(project_uuid)


@router.put("/risks/{risk_uuid}")
def update_report_risk(project_uuid: str, risk_uuid: str, payload: RiskUpdateWrite) -> dict[str, Any]:
    return report_generation.update_risk(project_uuid, risk_uuid, payload)


@router.put("/derived-blocks/{block_uuid}/override")
def override_derived_block(
    project_uuid: str,
    block_uuid: str,
    payload: DerivedBlockOverrideWrite,
) -> dict[str, Any]:
    return report_generation.override_block(project_uuid, block_uuid, payload)


@router.post("/derived-blocks/{block_uuid}/confirmation")
def confirm_derived_block(
    project_uuid: str,
    block_uuid: str,
    payload: DerivedBlockConfirmationWrite,
) -> dict[str, Any]:
    return report_generation.confirm_block(project_uuid, block_uuid, payload)


@router.post("/consistency-checks", status_code=201)
def create_consistency_check(project_uuid: str, payload: ConsistencyCheckWrite) -> dict[str, Any]:
    return report_generation.run_consistency_check(project_uuid, payload)


@router.get("/consistency-checks/latest")
def get_latest_consistency_check(project_uuid: str) -> dict[str, Any] | None:
    return report_generation.latest_consistency_check(project_uuid)


@router.get("/projection-context")
def get_projection_context(project_uuid: str) -> dict[str, Any]:
    return report_generation.get_projection_context(project_uuid)
