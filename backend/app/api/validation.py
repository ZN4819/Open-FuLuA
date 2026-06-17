from fastapi import APIRouter, HTTPException

from ..schemas import ValidationResponse
from ..services.validator import validate_project

router = APIRouter(prefix="/projects/{project_id}", tags=["validation"])


@router.post("/validate", response_model=ValidationResponse)
def validate_project_endpoint(project_id: int) -> ValidationResponse:
    try:
        return validate_project(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
