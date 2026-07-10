"""供 Windows 桌面客户端启动的本地 FastAPI 侧车。"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
import tempfile
from pathlib import Path


def _event(event: str, **values: object) -> None:
    print(json.dumps({"event": event, **values}, ensure_ascii=False), flush=True)


def _fallback_data_root() -> Path:
    return Path(tempfile.gettempdir()) / "fulua-desktop-failures"


def _conservative_data_root(arguments: list[str]) -> Path:
    try:
        index = arguments.index("--data-root")
        return Path(arguments[index + 1]).expanduser()
    except (ValueError, IndexError, OSError):
        return _fallback_data_root()


def _write_failure_log(data_root: Path, error: BaseException) -> None:
    for candidate in (data_root, _fallback_data_root()):
        try:
            logs_path = candidate / "logs"
            logs_path.mkdir(parents=True, exist_ok=True)
            (logs_path / "desktop-server.log").write_text(
                f"桌面侧车启动失败：{type(error).__name__}\n",
                encoding="utf-8",
            )
            return
        except OSError:
            continue


async def _serve(socket_handle: socket.socket) -> None:
    import uvicorn

    from app.main import app

    config = uvicorn.Config(app, log_config=None, access_log=False, log_level="warning")
    server = uvicorn.Server(config)
    serve_task = asyncio.create_task(server.serve(sockets=[socket_handle]))
    while not server.started:
        if serve_task.done():
            await serve_task
            raise RuntimeError("侧车在可用前退出")
        await asyncio.sleep(0.01)

    port = socket_handle.getsockname()[1]
    _event(
        "FULUA_READY",
        port=port,
        health_url=f"http://127.0.0.1:{port}/api/health",
    )
    await serve_task


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="附录A编写工具桌面侧车")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--web-dist", required=True)
    parser.add_argument("--session-token", required=True)
    return parser.parse_args()


def main() -> int:
    data_root = _conservative_data_root(sys.argv[1:])
    try:
        arguments = _arguments()
        data_root = Path(arguments.data_root).expanduser().resolve()
        # app.main 在其后导入，确保配置模块首次读取时就是桌面运行时路径。
        data_root.mkdir(parents=True, exist_ok=True)
        import os

        os.environ["FULUA_DATA_DIR"] = str(data_root)
        os.environ["FULUA_WEB_DIST_PATH"] = str(Path(arguments.web_dist).expanduser().resolve())

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as socket_handle:
            socket_handle.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            socket_handle.bind(("127.0.0.1", 0))
            asyncio.run(_serve(socket_handle))
    except (Exception, SystemExit) as error:
        _write_failure_log(data_root, error)
        _event("FULUA_FAILED", message="本地服务未能启动")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
