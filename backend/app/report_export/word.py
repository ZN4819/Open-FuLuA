"""Isolated Microsoft Word field refresh (LibreOffice is intentionally unsupported)."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lxml import etree

from ..resource_paths import resolve_resource_path


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS = {"w": W}


@dataclass(frozen=True)
class _ContentControlLock:
    part: str
    ordinal: int
    tag: str
    insert_at: int
    xml: bytes


@dataclass(frozen=True)
class _ContentControlContract:
    tags_by_part: dict[str, tuple[str, ...]]
    locks: tuple[_ContentControlLock, ...]


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


def _control_tag(control: etree._Element) -> str:
    return str(control.xpath("string(w:sdtPr/w:tag/@w:val)", namespaces=_NS))


def _write_package(path: Path, infos: list[zipfile.ZipInfo], parts: dict[str, bytes]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as package:
            for info in infos:
                package.writestr(info, parts[info.filename])
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _unlock_content_controls(
    source: Path,
    destination: Path,
) -> _ContentControlContract | None:
    """Create a private Word-only copy with SDT content locks removed.

    Microsoft Word cannot rebuild a TOC contained in an ``sdtContentLocked``
    control.  The unlocked copy never leaves export staging; the exact locks
    and control order are captured for deterministic restoration after Word
    saves the document.
    """

    try:
        with zipfile.ZipFile(source) as package:
            infos = package.infolist()
            parts = {info.filename: package.read(info.filename) for info in infos}
        tags_by_part: dict[str, tuple[str, ...]] = {}
        locks: list[_ContentControlLock] = []
        for part, data in list(parts.items()):
            if not part.startswith("word/") or not part.lower().endswith(".xml"):
                continue
            root = etree.fromstring(data)
            controls = root.xpath(".//w:sdt", namespaces=_NS)
            if not controls:
                continue
            tags_by_part[part] = tuple(_control_tag(control) for control in controls)
            changed = False
            for ordinal, control in enumerate(controls):
                properties = control.find(f"{{{W}}}sdtPr")
                if properties is None:
                    raise ValueError(f"missing sdtPr: {part}#{ordinal}")
                lock_nodes = properties.findall(f"{{{W}}}lock")
                if len(lock_nodes) > 1:
                    raise ValueError(f"duplicate lock: {part}#{ordinal}")
                if not lock_nodes:
                    continue
                lock = lock_nodes[0]
                locks.append(
                    _ContentControlLock(
                        part=part,
                        ordinal=ordinal,
                        tag=_control_tag(control),
                        insert_at=properties.index(lock),
                        xml=etree.tostring(lock),
                    )
                )
                properties.remove(lock)
                changed = True
            if changed:
                parts[part] = etree.tostring(
                    root,
                    xml_declaration=True,
                    encoding="UTF-8",
                    standalone="yes",
                )
        if not locks:
            return None
        _write_package(destination, infos, parts)
        return _ContentControlContract(
            tags_by_part=tags_by_part,
            locks=tuple(locks),
        )
    except (OSError, ValueError, zipfile.BadZipFile, etree.XMLSyntaxError) as exc:
        raise WordRefreshError(
            "WORD_CONTENT_CONTROL_UNLOCK_FAILED",
            "无法准备 Microsoft Word 字段刷新所需的临时文档。",
            details={"error": str(exc)},
        ) from exc


def _restore_content_control_locks(
    path: Path,
    contract: _ContentControlContract,
) -> None:
    """Restore the exact input lock set and reject Word SDT reordering."""

    try:
        with zipfile.ZipFile(path) as package:
            infos = package.infolist()
            parts = {info.filename: package.read(info.filename) for info in infos}
        roots: dict[str, etree._Element] = {}
        controls_by_part: dict[str, list[etree._Element]] = {}
        for part, expected_tags in contract.tags_by_part.items():
            if part not in parts:
                raise ValueError(f"missing control part: {part}")
            root = etree.fromstring(parts[part])
            controls = list(root.xpath(".//w:sdt", namespaces=_NS))
            actual_tags = tuple(_control_tag(control) for control in controls)
            if actual_tags != expected_tags:
                raise ValueError(f"content-control order changed: {part}")
            for control in controls:
                properties = control.find(f"{{{W}}}sdtPr")
                if properties is None:
                    raise ValueError(f"missing sdtPr after Word refresh: {part}")
                for lock in list(properties.findall(f"{{{W}}}lock")):
                    properties.remove(lock)
            roots[part] = root
            controls_by_part[part] = controls

        for item in contract.locks:
            control = controls_by_part[item.part][item.ordinal]
            if _control_tag(control) != item.tag:
                raise ValueError(f"content-control identity changed: {item.part}#{item.ordinal}")
            properties = control.find(f"{{{W}}}sdtPr")
            assert properties is not None
            properties.insert(min(item.insert_at, len(properties)), etree.fromstring(item.xml))

        for part, root in roots.items():
            parts[part] = etree.tostring(
                root,
                xml_declaration=True,
                encoding="UTF-8",
                standalone="yes",
            )
        _write_package(path, infos, parts)
    except (OSError, ValueError, IndexError, zipfile.BadZipFile, etree.XMLSyntaxError) as exc:
        raise WordRefreshError(
            "WORD_CONTENT_CONTROL_RESTORE_FAILED",
            "Microsoft Word 保存后无法恢复受控编辑边界。",
            details={"error": str(exc)},
        ) from exc


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
    unlocked_path = status_path.with_name(
        f".{status_path.stem}.{uuid.uuid4().hex}.word-input.docx"
    )
    contract = _unlock_content_controls(input_path, unlocked_path)
    word_input = unlocked_path if contract is not None else input_path
    command = [
        "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(script), "-InputPath", str(word_input), "-OutputPath", str(output_path),
        "-StatusPath", str(status_path),
    ]
    try:
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
    finally:
        unlocked_path.unlink(missing_ok=True)
    state = _status(status_path)
    if completed.returncode != 0 or state.get("status") != "succeeded" or not output_path.is_file():
        raise WordRefreshError(
            "WORD_REFRESH_FAILED", "Microsoft Word 未能完成字段刷新。",
            details={
                "pid": state.get("pid"), "error": state.get("error"),
                "stage": state.get("stage"),
                "returncode": completed.returncode,
                "stderr": completed.stderr[-1000:],
            },
        )
    if contract is not None:
        _restore_content_control_locks(output_path, contract)
    return state
