from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_name: str = "附录A编写工具"
    api_prefix: str = "/api"
    database_path: Path = Path(__file__).resolve().parents[1] / "data" / "app.db"


settings = Settings()
