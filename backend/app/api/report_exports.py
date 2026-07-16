from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Query
from fastapi.responses import FileResponse

from ..report_schemas import ReportExportJobWrite
from ..services import report_exports


router = APIRouter(tags=["report-exports"])


@router.post("/projects/{project_uuid}/report-validations")
def validate_report_export(
    project_uuid: str,
    mode: str = Query("final", pattern="^(draft|final)$"),
) -> dict:
    return report_exports.validate_project_export(project_uuid, mode=mode)


@router.get("/projects/{project_uuid}/report-validations/latest")
def latest_report_export_validation(
    project_uuid: str,
    mode: str = Query("final", pattern="^(draft|final)$"),
) -> dict:
    return report_exports.validate_project_export(project_uuid, mode=mode)


@router.post("/projects/{project_uuid}/report-export-jobs", status_code=202)
def create_report_export_job(
    project_uuid: str,
    payload: ReportExportJobWrite,
    background_tasks: BackgroundTasks,
) -> dict:
    job = report_exports.create_export_job(project_uuid, payload)
    background_tasks.add_task(report_exports.process_export_job, job["job_uuid"])
    return job


@router.get("/report-export-jobs/{job_uuid}")
def get_report_export_job(job_uuid: str) -> dict:
    return report_exports.get_export_job(job_uuid)


@router.get("/report-export-jobs/{job_uuid}/issues")
def get_report_export_issues(job_uuid: str) -> dict:
    return report_exports.get_export_issues(job_uuid)


@router.get("/report-export-jobs/{job_uuid}/docx")
def download_report_export(job_uuid: str) -> FileResponse:
    path = report_exports.export_docx_path(job_uuid)
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
