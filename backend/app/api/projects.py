from fastapi import APIRouter, HTTPException

from .. import database
from ..schemas import ProjectCreate, ProjectRead, ProjectUpdate, SectionRead

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
        name=row["name"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        sections=sections,
    )


@router.post("", response_model=ProjectRead, status_code=201)
def create_project(payload: ProjectCreate) -> ProjectRead:
    project = database.create_project(payload.name.strip())
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
