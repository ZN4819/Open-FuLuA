from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ..services.docx_generator import DocxGenerationError, generate_project_docx

router = APIRouter(prefix="/projects/{project_id}/exports", tags=["exports"])


@router.post("/docx")
def export_docx(
    project_id: int,
    mode: Literal["editable", "final"] = Query("editable"),
) -> FileResponse:
    try:
        path = generate_project_docx(project_id, mode)
    except DocxGenerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return FileResponse(
        path,
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
