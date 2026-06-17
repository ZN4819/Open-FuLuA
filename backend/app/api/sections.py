from fastapi import APIRouter, HTTPException

from .. import database
from ..schemas import (
    AssessmentRowRead,
    CrossReferenceRead,
    EvidenceImageRead,
    MetricResultRead,
    SectionDetailRead,
    SectionRead,
    SectionUpdate,
)

router = APIRouter(prefix="/projects/{project_id}/sections", tags=["sections"])


def section_to_schema(row) -> SectionRead:
    return SectionRead(
        id=row["id"],
        project_id=row["project_id"],
        code=row["code"],
        title=row["title"],
        table_title=row["table_title"],
        sort_order=row["sort_order"],
    )


def assessment_row_to_schema(row) -> AssessmentRowRead:
    return AssessmentRowRead(
        id=row["id"],
        section_id=row["section_id"],
        unit=row["unit"],
        object_name=row["object_name"],
        record_text=row["record_text"],
        sort_order=row["sort_order"],
        metric_result=MetricResultRead(
            d=row["d"],
            a=row["a"],
            k=row["k"],
            object_score=row["object_score"],
            unit_score=row["unit_score"],
            compliance=row["compliance"],
        ),
    )


def evidence_image_to_schema(row) -> EvidenceImageRead:
    return EvidenceImageRead(
        id=row["id"],
        project_id=row["project_id"],
        section_code=row["section_code"],
        file_path=row["file_path"],
        original_name=row["original_name"],
        caption=row["caption"],
        alt_text=row["alt_text"],
        sort_order=row["sort_order"],
        pixel_width=row["pixel_width"],
        pixel_height=row["pixel_height"],
        dpi_x=row["dpi_x"],
        dpi_y=row["dpi_y"],
        display_width_in=row["display_width_in"],
        display_height_in=row["display_height_in"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def cross_reference_to_schema(row) -> CrossReferenceRead:
    return CrossReferenceRead(
        id=row["id"],
        source_row_id=row["source_row_id"],
        target_image_id=row["target_image_id"],
        token=row["token"],
        display_text=row["display_text"],
    )


def build_section_detail(project_id: int, code: str) -> SectionDetailRead:
    section = database.get_section(project_id, code)
    if section is None:
        raise HTTPException(status_code=404, detail="章节不存在")

    rows = [assessment_row_to_schema(row) for row in database.list_assessment_rows(section["id"])]
    evidence_images = [
        evidence_image_to_schema(row)
        for row in database.list_evidence_images(project_id, section["code"])
    ]
    cross_references = [
        cross_reference_to_schema(row)
        for row in database.list_cross_references(section["id"])
    ]

    return SectionDetailRead(
        section=section_to_schema(section),
        rows=rows,
        evidence_images=evidence_images,
        cross_references=cross_references,
    )


@router.get("/{code}", response_model=SectionDetailRead)
def get_section_detail(project_id: int, code: str) -> SectionDetailRead:
    return build_section_detail(project_id, code)


@router.put("/{code}", response_model=SectionDetailRead)
def update_section_detail(project_id: int, code: str, payload: SectionUpdate) -> SectionDetailRead:
    updated = database.replace_section_rows(
        project_id=project_id,
        code=code,
        title=payload.title,
        table_title=payload.table_title,
        rows=[row.model_dump() for row in payload.rows],
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    return build_section_detail(project_id, code)
