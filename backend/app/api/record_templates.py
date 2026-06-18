from fastapi import APIRouter, HTTPException, Query

from ..schemas import RecordTemplateCreate, RecordTemplateRead, RecordTemplateUpdate
from ..services.record_templates import (
    RecordTemplateNotFoundError,
    RecordTemplatePermissionError,
    RecordTemplateValidationError,
    copy_record_template,
    create_user_record_template,
    delete_user_record_template,
    list_record_templates,
    update_user_record_template,
)

router = APIRouter(prefix="/record-templates", tags=["record-templates"])


@router.get("", response_model=list[RecordTemplateRead])
def get_record_templates(section_code: str | None = Query(default=None)) -> list[RecordTemplateRead]:
    return [RecordTemplateRead(**template) for template in list_record_templates(section_code)]


@router.post("", response_model=RecordTemplateRead, status_code=201)
def create_record_template(payload: RecordTemplateCreate) -> RecordTemplateRead:
    try:
        template = create_user_record_template(payload.model_dump())
    except RecordTemplateValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RecordTemplateRead(**template)


@router.put("/{template_key}", response_model=RecordTemplateRead)
def update_record_template(template_key: str, payload: RecordTemplateUpdate) -> RecordTemplateRead:
    try:
        template = update_user_record_template(template_key, payload.model_dump(exclude_unset=True))
    except RecordTemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RecordTemplatePermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RecordTemplateValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RecordTemplateRead(**template)


@router.delete("/{template_key}", response_model=RecordTemplateRead)
def delete_record_template(template_key: str) -> RecordTemplateRead:
    try:
        template = delete_user_record_template(template_key)
    except RecordTemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RecordTemplatePermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return RecordTemplateRead(**template)


@router.post("/{template_key}/copy", response_model=RecordTemplateRead, status_code=201)
def copy_record_template_endpoint(template_key: str) -> RecordTemplateRead:
    try:
        template = copy_record_template(template_key)
    except RecordTemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RecordTemplateValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RecordTemplateRead(**template)
