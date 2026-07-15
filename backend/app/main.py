import logging
import hmac
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.evidence import router as evidence_router
from .api.imports import router as imports_router
from .api.exports import router as exports_router
from .api.projects import router as projects_router
from .api.record_template_slots import router as record_template_slots_router
from .api.record_templates import router as record_templates_router
from .api.report_templates import router as report_templates_router
from .api.render_jobs import router as render_jobs_router
from .api.runtime import router as runtime_router, runtime_operations
from .api.sections import router as sections_router
from .api.templates import router as templates_router
from .api.validation import router as validation_router
from .config import settings
from .database import current_database_path, init_db, read_schema_version
from .runtime import BACKEND_VERSION, ensure_runtime_directories
from .schemas import HealthResponse
from .web_assets import mount_frontend_assets

logger = logging.getLogger(__name__)
app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174", "http://127.0.0.1:5174"],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def is_business_write_request(method: str, path: str) -> bool:
    return (
        method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
        and path.startswith("/api/")
        and not path.startswith(("/api/runtime", "/api/health", "/api/files"))
        and not path.endswith("/import-preview")
    )


@app.middleware("http")
async def reject_business_writes_during_maintenance(request: Request, call_next):
    token = os.getenv("FULUA_SESSION_TOKEN")
    if request.url.path.startswith("/api/runtime") and token and not hmac.compare_digest(request.headers.get("x-fulua-session-token", ""), token):
        return JSONResponse(status_code=403, content={"detail": "本机操作验证失败"})
    if is_business_write_request(request.method, request.url.path):
        try:
            with runtime_operations.business_write():
                return await call_next(request)
        except RuntimeError as exc:
            return JSONResponse(status_code=409, content={"detail": str(exc)})
    return await call_next(request)


@app.on_event("startup")
def on_startup() -> None:
    runtime_paths = ensure_runtime_directories()
    _bind_files_directory(runtime_paths.storage_path)
    logger.info("附录A编写工具以 %s 模式启动，数据根目录：%s", runtime_paths.mode, runtime_paths.data_root)
    init_db()


def _bind_files_directory(storage_path) -> None:
    files_app = next(route.app for route in app.routes if getattr(route, "name", None) == "files")
    files_app.directory = storage_path
    files_app.all_directories = files_app.get_directories(storage_path, files_app.packages)
    files_app.config_checked = False


@app.get(f"{settings.api_prefix}/health", response_model=HealthResponse)
def health() -> HealthResponse:
    runtime_paths = settings.runtime_paths
    return HealthResponse(
        status="ok",
        app_name=settings.app_name,
        database_path=str(current_database_path()),
        runtime_mode=runtime_paths.mode,
        data_root=str(runtime_paths.data_root),
        schema_version=read_schema_version(current_database_path(), readonly=True),
        backend_version=BACKEND_VERSION,
    )


app.include_router(projects_router, prefix=settings.api_prefix)
app.include_router(record_templates_router, prefix=settings.api_prefix)
app.include_router(report_templates_router, prefix=settings.api_prefix)
app.include_router(record_template_slots_router, prefix=settings.api_prefix)
app.include_router(render_jobs_router, prefix=settings.api_prefix)
app.include_router(sections_router, prefix=settings.api_prefix)
app.include_router(evidence_router, prefix=settings.api_prefix)
app.include_router(imports_router, prefix=settings.api_prefix)
app.include_router(exports_router, prefix=settings.api_prefix)
app.include_router(templates_router, prefix=settings.api_prefix)
app.include_router(validation_router, prefix=settings.api_prefix)
app.include_router(runtime_router, prefix=f"{settings.api_prefix}/runtime")
app.mount(f"{settings.api_prefix}/files", StaticFiles(directory=settings.storage_path, check_dir=False), name="files")
mount_frontend_assets(app)
