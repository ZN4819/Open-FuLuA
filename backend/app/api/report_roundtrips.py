"""R7 controlled Word roundtrip API."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile

from ..report_roundtrip.schemas import (
    ReportRoundtripCommitRead,
    ReportRoundtripCommitWrite,
    ReportRoundtripDiffRead,
    ReportRoundtripIssueCollection,
    ReportRoundtripJobRead,
    ReportRoundtripResolutionRead,
    ReportRoundtripResolutionWrite,
)
from ..services.report_domain.errors import ReportDomainError
from ..services.report_roundtrips import (
    commit_roundtrip_job,
    create_roundtrip_job,
    get_roundtrip_diff,
    get_roundtrip_issues,
    get_roundtrip_job,
    resolve_roundtrip_conflicts,
)


project_router = APIRouter(prefix="/projects", tags=["report-roundtrip"])
router = APIRouter(prefix="/report-import-jobs", tags=["report-roundtrip"])


@project_router.post(
    "/{project_uuid}/report-import-jobs",
    response_model=ReportRoundtripJobRead,
    status_code=201,
)
def upload_roundtrip_document(
    project_uuid: str,
    file: UploadFile = File(...),
    mode: str = Form(default="roundtrip"),
) -> ReportRoundtripJobRead:
    if mode != "roundtrip":
        raise ReportDomainError(
            "ROUNDTRIP_MODE_INVALID",
            "该入口只接受受控 Word 回收任务。",
            status_code=422,
            project_uuid=project_uuid,
            field="mode",
        )
    return ReportRoundtripJobRead.model_validate(create_roundtrip_job(project_uuid, file))


@router.get("/{job_id}", response_model=ReportRoundtripJobRead)
def read_roundtrip_job(job_id: int) -> ReportRoundtripJobRead:
    return ReportRoundtripJobRead.model_validate(get_roundtrip_job(job_id))


@router.get("/{job_id}/diff", response_model=ReportRoundtripDiffRead)
def read_roundtrip_diff(job_id: int) -> ReportRoundtripDiffRead:
    return ReportRoundtripDiffRead.model_validate(get_roundtrip_diff(job_id))


@router.get("/{job_id}/issues", response_model=ReportRoundtripIssueCollection)
def read_roundtrip_issues(job_id: int) -> ReportRoundtripIssueCollection:
    return ReportRoundtripIssueCollection.model_validate(get_roundtrip_issues(job_id))


@router.put("/{job_id}/resolution", response_model=ReportRoundtripResolutionRead)
def update_roundtrip_resolution(
    job_id: int,
    payload: ReportRoundtripResolutionWrite,
) -> ReportRoundtripResolutionRead:
    return ReportRoundtripResolutionRead.model_validate(
        resolve_roundtrip_conflicts(job_id, payload)
    )


@router.post("/{job_id}/commit", response_model=ReportRoundtripCommitRead)
def commit_roundtrip(
    job_id: int,
    payload: ReportRoundtripCommitWrite,
) -> ReportRoundtripCommitRead:
    return ReportRoundtripCommitRead.model_validate(commit_roundtrip_job(job_id, payload))
