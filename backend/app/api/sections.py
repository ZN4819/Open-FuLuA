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
from .evidence import evidence_to_schema

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
        subsystem=row["subsystem"],
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
    subsystems = _unique_values(
        [row["name"] for row in database.list_section_subsystems(project_id, code)] +
        [row.subsystem for row in rows]
    )
    evidence_images = [
        evidence_to_schema(row, index)
        for index, row in enumerate(database.list_evidence_images(project_id, section["code"]), start=1)
    ]
    cross_references = [
        cross_reference_to_schema(row)
        for row in database.list_cross_references(section["id"])
    ]

    return SectionDetailRead(
        section=section_to_schema(section),
        rows=rows,
        subsystems=subsystems,
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
        subsystems=payload.subsystems,
        rows=[row.model_dump() for row in payload.rows],
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    return build_section_detail(project_id, code)


def _unique_values(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = (value or "").strip()
        if text and text not in result:
            result.append(text)
    return result
