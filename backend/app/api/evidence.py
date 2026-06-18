from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .. import database
from ..schemas import EvidenceImageRead, EvidenceImageUpdate, EvidenceOrderUpdate
from ..services.evidence import EvidenceImageError, image_warnings, remove_stored_file, save_upload_file

router = APIRouter(tags=["evidence"])


def evidence_to_schema(row, section_index: int | None = None) -> EvidenceImageRead:
    raw = dict(row)
    figure_label = None
    if section_index is not None:
        figure_label = f"图{raw['section_code']}-{section_index}"
    return EvidenceImageRead(
        id=raw["id"],
        project_id=raw["project_id"],
        section_code=raw["section_code"],
        file_path=raw["file_path"],
        original_name=raw["original_name"],
        caption=raw["caption"],
        alt_text=raw["alt_text"],
        sort_order=raw["sort_order"],
        pixel_width=raw["pixel_width"],
        pixel_height=raw["pixel_height"],
        dpi_x=raw["dpi_x"],
        dpi_y=raw["dpi_y"],
        display_width_in=raw["display_width_in"],
        display_height_in=raw["display_height_in"],
        created_at=raw["created_at"],
        updated_at=raw["updated_at"],
        file_url=f"/api/files/{raw['file_path']}",
        figure_label=figure_label,
        warnings=image_warnings(raw),
    )


def list_section_evidence(project_id: int, section_code: str) -> list[EvidenceImageRead]:
    rows = database.list_evidence_images(project_id, section_code)
    return [evidence_to_schema(row, index) for index, row in enumerate(rows, start=1)]


@router.post("/projects/{project_id}/evidence", response_model=EvidenceImageRead, status_code=201)
def upload_evidence_image(
    project_id: int,
    section_code: str = Form(...),
    caption: str = Form(""),
    alt_text: str = Form(""),
    file: UploadFile = File(...),
) -> EvidenceImageRead:
    if database.get_section(project_id, section_code) is None:
        raise HTTPException(status_code=404, detail="章节不存在")

    try:
        image_data = save_upload_file(project_id, section_code, file)
        image_data["caption"] = caption
        image_data["alt_text"] = alt_text
        row = database.create_evidence_image(project_id, section_code, image_data)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EvidenceImageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    rows = database.list_evidence_images(project_id, section_code)
    index = next((idx for idx, item in enumerate(rows, start=1) if item["id"] == row["id"]), None)
    return evidence_to_schema(row, index)


@router.post("/projects/{project_id}/evidence/batch", response_model=list[EvidenceImageRead], status_code=201)
def upload_evidence_images(
    project_id: int,
    section_code: str = Form(...),
    caption: str = Form(""),
    alt_text: str = Form(""),
    files: list[UploadFile] = File(...),
) -> list[EvidenceImageRead]:
    if not files:
        raise HTTPException(status_code=400, detail="请选择至少一张 PNG 或 JPEG 图片。")
    if database.get_section(project_id, section_code) is None:
        raise HTTPException(status_code=404, detail="章节不存在")

    created_rows = []
    saved_paths: list[str] = []
    try:
        for file in files:
            image_data = save_upload_file(project_id, section_code, file)
            saved_paths.append(str(image_data["file_path"]))
            image_data["caption"] = caption
            image_data["alt_text"] = alt_text
            created_rows.append(database.create_evidence_image(project_id, section_code, image_data))
    except ValueError as exc:
        _rollback_uploaded_images(created_rows, saved_paths)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EvidenceImageError as exc:
        _rollback_uploaded_images(created_rows, saved_paths)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    rows = database.list_evidence_images(project_id, section_code)
    index_by_id = {row["id"]: index for index, row in enumerate(rows, start=1)}
    return [evidence_to_schema(row, index_by_id.get(row["id"])) for row in created_rows]


@router.put("/evidence/{image_id}", response_model=EvidenceImageRead)
def update_evidence_image(image_id: int, payload: EvidenceImageUpdate) -> EvidenceImageRead:
    row = database.update_evidence_image(
        image_id,
        {key: value for key, value in payload.model_dump().items() if value is not None},
    )
    if row is None:
        raise HTTPException(status_code=404, detail="图片不存在")
    rows = database.list_evidence_images(row["project_id"], row["section_code"])
    index = next((idx for idx, item in enumerate(rows, start=1) if item["id"] == row["id"]), None)
    return evidence_to_schema(row, index)


@router.delete("/evidence/{image_id}", response_model=EvidenceImageRead)
def delete_evidence_image(image_id: int) -> EvidenceImageRead:
    row = database.delete_evidence_image(image_id)
    if row is None:
        raise HTTPException(status_code=404, detail="图片不存在")
    remove_stored_file(row["file_path"])
    return evidence_to_schema(row)


@router.put("/projects/{project_id}/sections/{section_code}/evidence-order", response_model=list[EvidenceImageRead])
def reorder_evidence_images(project_id: int, section_code: str, payload: EvidenceOrderUpdate) -> list[EvidenceImageRead]:
    try:
        rows = database.reorder_evidence_images(project_id, section_code, payload.image_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [evidence_to_schema(row, index) for index, row in enumerate(rows, start=1)]


def _rollback_uploaded_images(rows, file_paths: list[str]) -> None:
    for row in rows:
        database.delete_evidence_image(row["id"])
    for file_path in file_paths:
        remove_stored_file(file_path)
