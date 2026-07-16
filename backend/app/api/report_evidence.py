from __future__ import annotations

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import FileResponse

from ..report_evidence.schemas import (
    EvidenceCategoryUpdate,
    EvidenceImageUpdate,
    EvidenceItemUpdate,
    EvidenceItemWrite,
    EvidenceReorderWrite,
    EvidenceValidationWrite,
)
from ..services import report_evidence


router = APIRouter(tags=["report-evidence"])


@router.get("/projects/{project_uuid}/report/appendix-b")
def get_appendix_b(project_uuid: str) -> dict:
    return report_evidence.get_appendix_b(project_uuid)


@router.put("/projects/{project_uuid}/report/appendix-b/{category_code}")
def update_appendix_b_category(
    project_uuid: str,
    category_code: str,
    payload: EvidenceCategoryUpdate,
) -> dict:
    return report_evidence.update_category(project_uuid, category_code, payload)


@router.post(
    "/projects/{project_uuid}/report/appendix-b/{category_code}/items",
    status_code=201,
)
def create_appendix_b_item(
    project_uuid: str,
    category_code: str,
    payload: EvidenceItemWrite,
) -> dict:
    return report_evidence.create_item(project_uuid, category_code, payload)


@router.put("/report-evidence-items/{item_uuid}")
def update_appendix_b_item(
    item_uuid: str,
    payload: EvidenceItemUpdate | EvidenceImageUpdate,
) -> dict:
    if isinstance(payload, EvidenceImageUpdate):
        return report_evidence.update_image(item_uuid, payload)
    return report_evidence.update_item(item_uuid, payload)


@router.delete("/report-evidence-items/{item_uuid}")
def delete_appendix_b_item(
    item_uuid: str,
    expected_project_revision: int = Query(ge=1),
    expected_revision: int = Query(ge=1),
) -> dict:
    return report_evidence.delete_item(
        item_uuid,
        expected_project_revision=expected_project_revision,
        expected_revision=expected_revision,
    )


@router.post("/report-evidence-items/{item_uuid}/images", status_code=201)
def upload_appendix_b_images(
    item_uuid: str,
    expected_project_revision: int = Form(..., ge=1),
    subtype: str = Form(..., min_length=1, max_length=80),
    caption: str = Form("", max_length=1000),
    alt_text: str = Form("", max_length=1000),
    files: list[UploadFile] = File(...),
) -> list[dict]:
    return report_evidence.upload_images(
        item_uuid,
        expected_project_revision=expected_project_revision,
        subtype=subtype,
        caption=caption,
        alt_text=alt_text,
        files=files,
    )


@router.post("/report-evidence-items/{item_uuid}/file")
def replace_appendix_b_image_file(
    item_uuid: str,
    expected_project_revision: int = Form(..., ge=1),
    expected_revision: int = Form(..., ge=1),
    file: UploadFile = File(...),
) -> dict:
    return report_evidence.replace_image_file(
        item_uuid,
        expected_project_revision=expected_project_revision,
        expected_revision=expected_revision,
        file=file,
    )


@router.put("/projects/{project_uuid}/report/appendix-b/{category_code}/reorder")
def reorder_appendix_b_items(
    project_uuid: str,
    category_code: str,
    payload: EvidenceReorderWrite,
) -> list[dict]:
    return report_evidence.reorder_items(project_uuid, category_code, payload)


@router.post("/projects/{project_uuid}/report/appendix-b/validations")
def validate_appendix_b(
    project_uuid: str,
    payload: EvidenceValidationWrite,
) -> dict:
    return report_evidence.validate_appendix_b(
        project_uuid,
        expected_project_revision=payload.expected_project_revision,
    )


@router.get("/projects/{project_id}/report-evidence-items/{item_uuid}/file")
def read_appendix_b_image(project_id: int, item_uuid: str) -> FileResponse:
    path = report_evidence.evidence_file_path(project_id, item_uuid)
    return FileResponse(path, filename=path.name, content_disposition_type="inline")
