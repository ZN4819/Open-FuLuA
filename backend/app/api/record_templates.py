from fastapi import APIRouter, Query

from ..schemas import RecordTemplateRead
from ..services.record_templates import list_record_templates

router = APIRouter(prefix="/record-templates", tags=["record-templates"])


@router.get("", response_model=list[RecordTemplateRead])
def get_record_templates(section_code: str | None = Query(default=None)) -> list[RecordTemplateRead]:
    return [RecordTemplateRead(**template) for template in list_record_templates(section_code)]
