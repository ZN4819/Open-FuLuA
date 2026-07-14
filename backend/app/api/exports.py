from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ..services.docx_generator import DocxGenerationError, generate_project_docx
from ..services.xlsx_generator import ScoreWorkbookExportError, generate_score_workbook

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


@router.post("/xlsx")
def export_xlsx(project_id: int) -> FileResponse:
    try:
        path = generate_score_workbook(project_id)
    except ScoreWorkbookExportError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"message": str(exc), "issues": exc.issues},
        ) from exc

    return FileResponse(
        path,
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
