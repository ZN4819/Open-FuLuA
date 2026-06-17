from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from ..schemas import RenderJobRead
from ..services.preview import create_preview_job, get_preview_job, process_preview_job

router = APIRouter(tags=["render-jobs"])


@router.post("/projects/{project_id}/render-jobs", response_model=RenderJobRead, status_code=202)
def create_render_job(
    project_id: int,
    background_tasks: BackgroundTasks,
    mode: Literal["editable", "final"] = Query("final"),
) -> RenderJobRead:
    try:
        job = create_preview_job(project_id, mode)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    background_tasks.add_task(process_preview_job, job.id)
    return job


@router.get("/render-jobs/{job_id}", response_model=RenderJobRead)
def read_render_job(job_id: int) -> RenderJobRead:
    job = get_preview_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="预览任务不存在")
    return job
