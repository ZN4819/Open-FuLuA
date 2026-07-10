import asyncio
import importlib.util
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class DesktopStaticFrontendTests(unittest.TestCase):
    @staticmethod
    def _request(
        app,
        path: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        messages: list[dict[str, object]] = []

        async def receive() -> dict[str, object]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, object]) -> None:
            messages.append(message)

        async def request() -> None:
            await app(
                {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": method,
                    "scheme": "http",
                    "path": path,
                    "raw_path": path.encode(),
                    "query_string": b"",
                    "root_path": "",
                    "headers": [
                        (key.lower().encode(), value.encode())
                        for key, value in (headers or {}).items()
                    ],
                    "client": ("127.0.0.1", 8000),
                    "server": ("127.0.0.1", 8000),
                },
                receive,
                send,
            )

        asyncio.run(request())
        start = next(message for message in messages if message["type"] == "http.response.start")
        headers = {
            key.decode().lower(): value.decode()
            for key, value in start.get("headers", [])
        }
        body = b"".join(
            message.get("body", b"")
            for message in messages
            if message["type"] == "http.response.body"
        )
        return int(start["status"]), headers, body

    @staticmethod
    def _load_app() -> object:
        module_name = f"app._desktop_static_frontend_{uuid.uuid4().hex}"
        module_path = ROOT / "backend" / "app" / "main.py"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            del sys.modules[module_name]
        return module.app

    def test_explicit_dist_serves_page_assets_and_history_routes_without_overriding_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            dist_path = temp_path / "frontend-dist"
            data_path = temp_path / "runtime-data"
            dist_path.mkdir()
            (dist_path / "assets").mkdir()
            index = b"<!doctype html><title>CD-3 frontend</title>"
            asset = b"console.log('CD-3 asset');"
            (dist_path / "index.html").write_bytes(index)
            (dist_path / "assets" / "app.js").write_bytes(asset)
            (data_path / "storage").mkdir(parents=True)

            with patch.dict(
                os.environ,
                {
                    "FULUA_WEB_DIST_PATH": str(dist_path),
                    "FULUA_DATA_DIR": str(data_path),
                },
                clear=True,
            ):
                app = self._load_app()
                root_status, _, root_body = self._request(app, "/")
                html_navigation = {"accept": "text/html,application/xhtml+xml"}
                route_status, _, route_body = self._request(app, "/projects/42", headers=html_navigation)
                asset_status, _, asset_body = self._request(app, "/assets/app.js")
                head_status, _, head_body = self._request(
                    app,
                    "/projects/42",
                    method="HEAD",
                    headers=html_navigation,
                )
                health_status, health_headers, health_body = self._request(app, "/api/health")
                files_status, _, files_body = self._request(app, "/api/files/missing.png")
                missing_api_status, _, missing_api_body = self._request(app, "/api/not-found")
                missing_asset_status, _, missing_asset_body = self._request(app, "/assets/missing.js")
                missing_static_status, _, missing_static_body = self._request(
                    app,
                    "/static/missing",
                    headers={"accept": "*/*"},
                )
                missing_icon_status, _, missing_icon_body = self._request(
                    app,
                    "/icons/app",
                    headers={"accept": "application/json"},
                )
                missing_manifest_status, _, missing_manifest_body = self._request(
                    app,
                    "/manifest",
                    headers={"accept": "*/*"},
                )
                non_html_route_status, _, non_html_route_body = self._request(
                    app,
                    "/projects/42",
                    headers={"accept": "*/*"},
                )

        self.assertEqual(root_status, 200)
        self.assertEqual(root_body, index)
        self.assertEqual(route_status, 200)
        self.assertEqual(route_body, index)
        self.assertEqual(asset_status, 200)
        self.assertEqual(asset_body, asset)
        self.assertEqual(head_status, 200)
        self.assertEqual(head_body, b"")
        self.assertEqual(health_status, 200)
        self.assertIn("application/json", health_headers.get("content-type", ""))
        self.assertIn(b'"status":"ok"', health_body)
        self.assertEqual(files_status, 404)
        self.assertNotEqual(files_body, index)
        self.assertEqual(missing_api_status, 404)
        self.assertNotEqual(missing_api_body, index)
        self.assertEqual(missing_asset_status, 404)
        self.assertNotEqual(missing_asset_body, index)
        self.assertEqual(missing_static_status, 404)
        self.assertNotEqual(missing_static_body, index)
        self.assertEqual(missing_icon_status, 404)
        self.assertNotEqual(missing_icon_body, index)
        self.assertEqual(missing_manifest_status, 404)
        self.assertNotEqual(missing_manifest_body, index)
        self.assertEqual(non_html_route_status, 404)
        self.assertNotEqual(non_html_route_body, index)

    def test_frontend_api_base_prefers_override_then_dev_fallback_then_same_origin(self) -> None:
        client_source = (ROOT / "frontend" / "src" / "api" / "client.ts").read_text(encoding="utf-8")

        self.assertIn("import.meta.env.VITE_API_BASE_URL?.trim()", client_source)
        self.assertIn("import.meta.env.DEV", client_source)
        self.assertIn('"http://127.0.0.1:8000"', client_source)
        self.assertIn(': ""', client_source)
        self.assertNotIn('VITE_API_BASE_URL ?? "http://127.0.0.1:8000"', client_source)
        self.assertIn('request<Project[]>("/api/projects")', client_source)
        self.assertIn('`${API_BASE_URL}${fileUrl}`', client_source)

    def test_vite_proxy_only_forwards_api_paths(self) -> None:
        vite_source = (ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")

        self.assertIn("proxy", vite_source)
        self.assertIn('"/api"', vite_source)
        self.assertIn('"http://127.0.0.1:8000"', vite_source)
        self.assertNotIn('"/": "http://127.0.0.1:8000"', vite_source)

    def test_development_cors_accepts_localhost_and_loopback_on_any_port(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            app = self._load_app()
            for origin in ("http://localhost:5180", "http://127.0.0.1:49152"):
                status, headers, _ = self._request(
                    app,
                    "/api/health",
                    method="OPTIONS",
                    headers={
                        "origin": origin,
                        "access-control-request-method": "GET",
                    },
                )

                self.assertEqual(status, 200)
                self.assertEqual(headers.get("access-control-allow-origin"), origin)


if __name__ == "__main__":
    unittest.main()
