"""Isolated Microsoft Word field refresh (LibreOffice is intentionally unsupported)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ..resource_paths import resolve_resource_path


class WordRefreshError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _status(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def _stop_owned_word(pid: int) -> None:
    if pid <= 0:
        return
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def refresh_with_word(
    input_path: Path,
    output_path: Path,
    *,
    status_path: Path,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    script = resolve_resource_path("scripts", "word_refresh_report.ps1")
    if not script.is_file():
        raise WordRefreshError("WORD_REFRESH_SCRIPT_UNAVAILABLE", "Microsoft Word 刷新脚本不存在。")
    status_path.unlink(missing_ok=True)
    command = [
        "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(script), "-InputPath", str(input_path), "-OutputPath", str(output_path),
        "-StatusPath", str(status_path),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        state = _status(status_path)
        _stop_owned_word(int(state.get("pid") or 0))
        raise WordRefreshError(
            "WORD_REFRESH_TIMEOUT", "Microsoft Word 字段刷新超时。",
            details={"pid": state.get("pid"), "timeout_seconds": timeout_seconds},
        ) from exc
    state = _status(status_path)
    if completed.returncode != 0 or state.get("status") != "succeeded" or not output_path.is_file():
        raise WordRefreshError(
            "WORD_REFRESH_FAILED", "Microsoft Word 未能完成字段刷新。",
            details={
                "pid": state.get("pid"), "error": state.get("error"),
                "returncode": completed.returncode,
                "stderr": completed.stderr[-1000:],
            },
        )
    return state
