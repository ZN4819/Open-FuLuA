from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_frontend_dev_server_defaults_to_non_vite_default_port() -> None:
    vite_config = (ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    start_script = (ROOT / "scripts" / "start_dev.ps1").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "port: 5174" in vite_config
    assert "param(" in start_script
    assert "[int] $FrontendPort = 5174" in start_script
    assert "'--port', $FrontendPort" in start_script
    assert "http://127.0.0.1:$FrontendPort" in start_script
    assert "http://127.0.0.1:5174" in readme
    assert "http://127.0.0.1:5173" not in readme


def test_backend_cors_allows_configured_frontend_dev_port() -> None:
    main_source = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")

    assert "http://localhost:5174" in main_source
    assert "http://127.0.0.1:5174" in main_source
    assert "allow_origin_regex" in main_source
    assert "localhost|127\\.0\\.0\\.1" in main_source
    assert "http://localhost:5173" not in main_source
    assert "http://127.0.0.1:5173" not in main_source
