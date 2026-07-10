from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, PlainTextResponse
from starlette.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send


def configured_web_dist_path() -> Path | None:
    configured_path = os.getenv("FULUA_WEB_DIST_PATH", "").strip()
    if not configured_path:
        return None

    dist_path = Path(configured_path)
    return dist_path if (dist_path / "index.html").is_file() else None


class FrontendAssets:
    def __init__(self, dist_path: Path) -> None:
        self._index_path = dist_path / "index.html"
        self._static_files = StaticFiles(directory=dist_path, check_dir=True)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._static_files(scope, receive, send)
            return

        request_path = scope["path"]
        if request_path == "/api" or request_path.startswith("/api/"):
            await PlainTextResponse("Not Found", status_code=404)(scope, receive, send)
            return

        if request_path == "/" and scope["method"] in {"GET", "HEAD"}:
            await FileResponse(self._index_path)(scope, receive, send)
            return

        try:
            response = await self._static_files.get_response(request_path.lstrip("/"), scope)
        except HTTPException as error:
            if error.status_code == 404 and self._is_history_route(scope):
                response = FileResponse(self._index_path)
            else:
                response = PlainTextResponse("Not Found", status_code=error.status_code)

        await response(scope, receive, send)

    @staticmethod
    def _is_history_route(scope: Scope) -> bool:
        if scope["method"] not in {"GET", "HEAD"}:
            return False

        path = scope["path"].lstrip("/")
        if path.split("/", 1)[0] in {"assets", "static", "icons", "manifest"}:
            return False
        if "." in path.rsplit("/", 1)[-1]:
            return False

        return FrontendAssets._accepts_html(scope)

    @staticmethod
    def _accepts_html(scope: Scope) -> bool:
        for name, value in scope.get("headers", []):
            if name.lower() != b"accept":
                continue
            for media_range in value.lower().split(b","):
                parts = [part.strip() for part in media_range.split(b";")]
                if parts[0] != b"text/html":
                    continue

                quality = 1.0
                for parameter in parts[1:]:
                    key, separator, raw_value = parameter.partition(b"=")
                    if key.strip() != b"q" or not separator:
                        continue
                    try:
                        quality = float(raw_value.strip())
                    except ValueError:
                        quality = 0.0
                    break

                if 0.0 < quality <= 1.0:
                    return True
        return False


def mount_frontend_assets(app: FastAPI) -> None:
    dist_path = configured_web_dist_path()
    if dist_path is not None:
        app.mount("/", FrontendAssets(dist_path), name="frontend")
