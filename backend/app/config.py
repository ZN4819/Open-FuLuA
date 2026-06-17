from dataclasses import dataclass
import os
from pathlib import Path


def default_database_path() -> Path:
    override = os.getenv("FULUA_DATABASE_PATH")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1] / "data" / "app.db"


@dataclass(frozen=True)
class Settings:
    app_name: str = "附录A编写工具"
    api_prefix: str = "/api"
    database_path: Path = default_database_path()
    storage_path: Path = Path(__file__).resolve().parents[2] / "storage"


settings = Settings()
