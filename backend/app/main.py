from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.evidence import router as evidence_router
from .api.exports import router as exports_router
from .api.projects import router as projects_router
from .api.render_jobs import router as render_jobs_router
from .api.sections import router as sections_router
from .api.templates import router as templates_router
from .api.validation import router as validation_router
from .config import settings
from .database import current_database_path, init_db
from .schemas import HealthResponse

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    settings.storage_path.mkdir(parents=True, exist_ok=True)
    init_db()


@app.get(f"{settings.api_prefix}/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        app_name=settings.app_name,
        database_path=str(current_database_path()),
    )


app.include_router(projects_router, prefix=settings.api_prefix)
app.include_router(render_jobs_router, prefix=settings.api_prefix)
app.include_router(sections_router, prefix=settings.api_prefix)
app.include_router(evidence_router, prefix=settings.api_prefix)
app.include_router(exports_router, prefix=settings.api_prefix)
app.include_router(templates_router, prefix=settings.api_prefix)
app.include_router(validation_router, prefix=settings.api_prefix)
app.mount(f"{settings.api_prefix}/files", StaticFiles(directory=settings.storage_path), name="files")
