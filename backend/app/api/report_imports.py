"""R6 既有完整报告迁移 API。"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from ..report_import.schemas import (
    ReportAppendixACopyRead,
    ReportAppendixACopyWrite,
    ReportImportConfirmWrite,
    ReportImportJobRead,
    ReportImportResolutionsWrite,
)
from ..services.report_imports import (
    ReportImportServiceError,
    copy_appendix_a_into_report,
    confirm_report_import_job,
    create_report_import_job,
    get_project_migration_review,
    get_report_import_job,
    resolve_report_import_issues,
)


router = APIRouter(prefix="/report-imports", tags=["report-imports"])
project_router = APIRouter(prefix="/projects", tags=["report-imports"])


def _raise(exc: ReportImportServiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail()) from exc


@router.post("/docx", response_model=ReportImportJobRead, status_code=201)
def upload_report_import(
    file: UploadFile = File(...),
    mode: str = Query(default="migration"),
) -> ReportImportJobRead:
    try:
        return create_report_import_job(file, mode=mode)
    except ReportImportServiceError as exc:
        _raise(exc)


@router.get("/{job_id}", response_model=ReportImportJobRead)
def get_report_import(job_id: int) -> ReportImportJobRead:
    job = get_report_import_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "REPORT_IMPORT_NOT_FOUND", "message": "迁移任务不存在。", "details": {}},
        )
    return job


@router.put("/{job_id}/resolutions", response_model=ReportImportJobRead)
def update_report_import_resolutions(
    job_id: int,
    payload: ReportImportResolutionsWrite,
) -> ReportImportJobRead:
    try:
        return resolve_report_import_issues(job_id, payload)
    except ReportImportServiceError as exc:
        _raise(exc)


@router.post("/{job_id}/confirm", response_model=ReportImportJobRead, status_code=201)
def confirm_report_import(
    job_id: int,
    payload: ReportImportConfirmWrite,
) -> ReportImportJobRead:
    try:
        return confirm_report_import_job(job_id, payload)
    except ReportImportServiceError as exc:
        _raise(exc)


@project_router.get("/{project_uuid}/report/migration-review", response_model=ReportImportJobRead)
def get_migration_review(project_uuid: str) -> ReportImportJobRead:
    review = get_project_migration_review(project_uuid)
    if review is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "REPORT_IMPORT_REVIEW_NOT_FOUND",
                "message": "该项目没有可用的迁移审阅记录。",
                "details": {},
            },
        )
    return review


@project_router.post(
    "/{target_uuid}/report/appendix-a/copy",
    response_model=ReportAppendixACopyRead,
)
def copy_report_appendix_a(
    target_uuid: str,
    payload: ReportAppendixACopyWrite,
) -> ReportAppendixACopyRead:
    try:
        return copy_appendix_a_into_report(target_uuid, payload)
    except ReportImportServiceError as exc:
        _raise(exc)
