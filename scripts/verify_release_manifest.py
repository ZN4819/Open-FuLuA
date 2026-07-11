"""校验 electron-builder 更新元数据与安装包 SHA-512。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from pathlib import Path
import yaml


def _unsafe_path(path: Path) -> bool:
    try:
        stat = path.lstat()
    except OSError:
        return True
    return path.is_symlink() or bool(getattr(stat, "st_file_attributes", 0) & 0x400)


def verify_latest_yml(metadata_path: Path, artifact_root: Path) -> dict[str, str]:
    root = artifact_root.resolve(strict=True)
    if _unsafe_path(artifact_root) or _unsafe_path(metadata_path):
        raise ValueError("latest.yml 或产物目录为重解析点")
    try:
        metadata_path.resolve(strict=True).relative_to(root)
        value = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ValueError("latest.yml 无法安全解析") from exc
    if not isinstance(value, dict):
        raise ValueError("latest.yml 顶层必须是映射")
    version = value.get("version")
    if not isinstance(version, str):
        raise ValueError("latest.yml 缺少 version")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version):
        raise ValueError("latest.yml 版本不是语义版本")
    relative_path = value.get("path")
    expected_sha512 = value.get("sha512")
    files = value.get("files")
    if not isinstance(relative_path, str) or not isinstance(expected_sha512, str) or not isinstance(files, list):
        raise ValueError("latest.yml 缺少 path、sha512 或 files")
    setup_entries = [
        item for item in files
        if isinstance(item, dict) and isinstance(item.get("url"), str)
        and str(item["url"]).lower().endswith(".exe") and "setup" in str(item["url"]).lower()
    ]
    if len(setup_entries) != 1:
        raise ValueError("latest.yml files 必须包含唯一 Setup 条目")
    setup_entry = setup_entries[0]
    if setup_entry.get("url") != relative_path or setup_entry.get("sha512") != expected_sha512:
        raise ValueError("latest.yml files Setup 路径或 SHA-512 与顶层不一致")
    expected_size = setup_entry.get("size")
    if isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size < 0:
        raise ValueError("latest.yml Setup size 无效")
    if Path(relative_path).name != relative_path or not relative_path.lower().endswith(".exe") or "setup" not in relative_path.lower():
        raise ValueError("latest.yml 安装包路径不安全")
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
    if installer.stat().st_size != expected_size:
        raise ValueError("latest.yml Setup size 与安装包大小不匹配")
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
