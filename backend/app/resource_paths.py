from __future__ import annotations

import sys
from pathlib import Path


def resolve_resource_root() -> Path:
    """返回源码仓库或 PyInstaller 冻结包中的只读资源根。"""
    if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[2]


def resolve_resource_path(*parts: str) -> Path:
    return resolve_resource_root().joinpath(*parts)
