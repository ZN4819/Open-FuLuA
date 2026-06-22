from fastapi import APIRouter, File, HTTPException, UploadFile

from ..schemas import DocxImportJobRead
from ..services.docx_importer.preview import DocxImportPreviewError, create_docx_import_preview, get_docx_import_preview

router = APIRouter(prefix="/imports", tags=["imports"])


@router.post("/docx", response_model=DocxImportJobRead, status_code=201)
def upload_docx_import(file: UploadFile = File(...)) -> DocxImportJobRead:
    try:
        return create_docx_import_preview(file)
    except DocxImportPreviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{job_id}", response_model=DocxImportJobRead)
def get_docx_import(job_id: int) -> DocxImportJobRead:
    job = get_docx_import_preview(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="导入任务不存在")
    return job