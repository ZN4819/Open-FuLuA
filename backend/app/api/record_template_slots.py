from fastapi import APIRouter, HTTPException

from ..schemas import RecordTemplateSlotRead, RecordTemplateSlotUpdate
from ..services.record_templates import (
    RecordTemplateNotFoundError,
    RecordTemplateValidationError,
    list_record_template_slots,
    reset_record_template_slot,
    update_record_template_slot,
)

router = APIRouter(prefix="/record-template-slots", tags=["record-template-slots"])


@router.get("", response_model=list[RecordTemplateSlotRead])
def get_record_template_slots(
    section_code: str | None = None,
    unit: str | None = None,
    template_type: str | None = None,
) -> list[RecordTemplateSlotRead]:
    try:
        slots = list_record_template_slots(section_code=section_code, unit=unit, template_type=template_type)
    except RecordTemplateValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [RecordTemplateSlotRead(**slot) for slot in slots]


@router.put("/{slot_id}", response_model=RecordTemplateSlotRead)
def update_record_template_slot_endpoint(
    slot_id: int,
    payload: RecordTemplateSlotUpdate,
) -> RecordTemplateSlotRead:
    try:
        slot = update_record_template_slot(slot_id, payload.model_dump(exclude_unset=True))
    except RecordTemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RecordTemplateValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RecordTemplateSlotRead(**slot)


@router.post("/{slot_id}/reset", response_model=RecordTemplateSlotRead)
def reset_record_template_slot_endpoint(slot_id: int) -> RecordTemplateSlotRead:
    try:
        slot = reset_record_template_slot(slot_id)
    except RecordTemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RecordTemplateValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RecordTemplateSlotRead(**slot)