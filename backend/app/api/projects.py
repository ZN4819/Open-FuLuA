from fastapi import APIRouter, HTTPException

from .. import database
from ..schemas import (
    ProjectCreate,
    ProjectRead,
    ProjectUpdate,
    ProjectUpgradeCopyRequest,
    SectionRead,
)
from ..services.projects import (
    ProjectServiceError,
    create_typed_project,
    remove_project_runtime_files,
    transition_workflow,
    upgrade_project_copy,
)

router = APIRouter(prefix="/projects", tags=["projects"])


def section_to_schema(row) -> SectionRead:
    return SectionRead(
        id=row["id"],
        project_id=row["project_id"],
        code=row["code"],
        title=row["title"],
        table_title=row["table_title"],
        sort_order=row["sort_order"],
    )


def project_to_schema(row) -> ProjectRead:
    sections = [section_to_schema(section) for section in database.list_sections(row["id"])]
    return ProjectRead(
        id=row["id"],
        project_uuid=row["project_uuid"],
        name=row["name"],
        project_type=row["project_type"],
        workflow_status=row["workflow_status"],
        template_package_id=row["template_package_id"],
        template_edition=row["template_edition"],
        template_revision=row["template_revision"],
        template_asset_set_hash=row["template_asset_set_hash"],
        source_project_uuid=row["source_project_uuid"],
        created_by_operation=row["created_by_operation"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        sections=sections,
    )


@router.post("", response_model=ProjectRead, status_code=201)
def create_project(payload: ProjectCreate) -> ProjectRead:
    try:
        project = create_typed_project(
            payload.name.strip(),
            project_type=payload.project_type,
            template_package_id=payload.template_package_id,
            template_edition=payload.template_edition,
            template_revision=payload.template_revision,
        )
    except ProjectServiceError as exc:
        _raise_project_error(exc)
    return project_to_schema(project)


@router.get("", response_model=list[ProjectRead])
def list_projects() -> list[ProjectRead]:
    return [project_to_schema(project) for project in database.list_projects()]


@router.post("/{project_uuid}/upgrade-copy", response_model=ProjectRead, status_code=201)
def upgrade_project(project_uuid: str, payload: ProjectUpgradeCopyRequest) -> ProjectRead:
    try:
        project = upgrade_project_copy(
            project_uuid,
            name=payload.name.strip(),
            template_package_id=payload.template_package_id,
            template_edition=payload.template_edition,
            template_revision=payload.template_revision,
            idempotency_key=str(payload.idempotency_key),
        )
    except ProjectServiceError as exc:
        _raise_project_error(exc)
    return project_to_schema(project)


@router.post("/{project_uuid}/workflow/{action}", response_model=ProjectRead)
def change_project_workflow(project_uuid: str, action: str) -> ProjectRead:
    try:
        project = transition_workflow(project_uuid, action)
    except ProjectServiceError as exc:
        _raise_project_error(exc)
    return project_to_schema(project)


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: int) -> ProjectRead:
    project = database.get_project_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project_to_schema(project)


@router.put("/{project_id}", response_model=ProjectRead)
def update_project(project_id: int, payload: ProjectUpdate) -> ProjectRead:
    project = database.update_project(project_id, payload.name.strip())
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project_to_schema(project)


@router.delete("/{project_id}", response_model=ProjectRead)
def delete_project(project_id: int) -> ProjectRead:
    project = database.get_project_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    deleted = project_to_schema(project)
    database.delete_project(project_id)
    remove_project_runtime_files(project_id, str(project["project_uuid"]))
    return deleted


def _raise_project_error(exc: ProjectServiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail()) from exc
