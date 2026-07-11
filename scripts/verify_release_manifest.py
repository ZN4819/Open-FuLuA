"""校验 electron-builder 更新元数据与安装包 SHA-512。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from pathlib import Path


def _scalar(text: str, key: str) -> str:
    match = re.search(rf"(?m)^{re.escape(key)}:\s*(.+?)\s*$", text)
    if not match:
        raise ValueError(f"latest.yml 缺少 {key}")
    return match.group(1).strip().strip("'\"")


def _unsafe_path(path: Path) -> bool:
    try:
        stat = path.lstat()
    except OSError:
        return True
    return path.is_symlink() or bool(getattr(stat, "st_file_attributes", 0) & 0x400)


def verify_latest_yml(metadata_path: Path, artifact_root: Path) -> dict[str, str]:
    text = metadata_path.read_text(encoding="utf-8")
    version = _scalar(text, "version")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version):
        raise ValueError("latest.yml 版本不是语义版本")
    relative_path = _scalar(text, "path")
    expected_sha512 = _scalar(text, "sha512")
    if Path(relative_path).name != relative_path or not relative_path.lower().endswith(".exe") or "setup" not in relative_path.lower():
        raise ValueError("latest.yml 安装包路径不安全")
    root = artifact_root.resolve(strict=True)
    installer = artifact_root / relative_path
    if _unsafe_path(installer):
        raise ValueError("latest.yml 安装包不存在或为重解析点")
    try:
        installer.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError("latest.yml 安装包逃逸产物目录") from exc
    actual = base64.b64encode(hashlib.sha512(installer.read_bytes()).digest()).decode("ascii")
    if not expected_sha512 or actual != expected_sha512:
        raise ValueError("latest.yml SHA-512 与安装包不匹配")
    return {"version": version, "path": relative_path, "sha512": actual}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("metadata", type=Path)
    parser.add_argument("artifact_root", type=Path)
    arguments = parser.parse_args()
    print(json.dumps(verify_latest_yml(arguments.metadata, arguments.artifact_root), ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
