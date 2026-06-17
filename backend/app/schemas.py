from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    app_name: str
    database_path: str


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ProjectUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class SectionRead(BaseModel):
    id: int
    project_id: int
    code: str
    title: str
    table_title: str
    sort_order: int


class ProjectRead(BaseModel):
    id: int
    name: str
    created_at: str
    updated_at: str
    sections: list[SectionRead] = []
