from dataclasses import dataclass
from pathlib import Path

from .runtime import RuntimePaths, resolve_runtime_paths


_DEVELOPMENT_STORAGE_PATH = Path(__file__).resolve().parents[2] / "storage"


@dataclass(frozen=True)
class Settings:
    app_name: str = "附录A编写工具"
    api_prefix: str = "/api"
    database_path: Path | None = None
    storage_path: Path | None = None

    @property
    def runtime_paths(self) -> RuntimePaths:
        return resolve_runtime_paths()

    def __getattribute__(self, name: str):
        if name in {"database_path", "storage_path"}:
            override = object.__getattribute__(self, name)
            if name == "storage_path" and override == _DEVELOPMENT_STORAGE_PATH:
                override = None
            if override is not None:
                return override
            runtime_paths = object.__getattribute__(self, "runtime_paths")
            return getattr(runtime_paths, name)
        return object.__getattribute__(self, name)


settings = Settings()
