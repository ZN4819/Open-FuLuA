"""校验 R0 六项受信资产及打包侧车与源码完全一致。"""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.report_templates.registry import (  # noqa: E402
    EXPECTED_ASSETS,
    EXPECTED_FREEZE_RECORD,
    TRUSTED_ASSET_HASHES_SHA256,
)
from app.services.report_templates.models import EXPECTED_WORD_ACCEPTANCE_EVIDENCE_SHA256  # noqa: E402

SOURCE_ASSET_DIR = ROOT / "templates" / "report" / "2023-2025.12.08"
WORD_ACCEPTANCE_EVIDENCE = ROOT / "docs" / "report-tool" / "evidence" / "r0-word-acceptance.json"
ALL_TRUSTED_ASSETS = (*EXPECTED_ASSETS, "asset_hashes.json")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_reparse_or_symlink(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def verify_asset_dir(asset_dir: Path, *, require_trust_root: bool) -> dict[str, str]:
    root = asset_dir.resolve(strict=True)
    if _is_reparse_or_symlink(asset_dir):
        raise ValueError("REPORT_TEMPLATE_ASSET_ROOT_UNTRUSTED")
    hash_path = root / "asset_hashes.json"
    if _is_reparse_or_symlink(hash_path):
        raise ValueError("REPORT_TEMPLATE_ASSET_PATH_UNTRUSTED")
    trust_hash = _sha256(hash_path)
    if require_trust_root and trust_hash != TRUSTED_ASSET_HASHES_SHA256:
        raise ValueError("REPORT_TEMPLATE_TRUST_ROOT_MISMATCH")
    manifest = json.loads(hash_path.read_text(encoding="utf-8"))
    if (
        manifest.get("schema_version") != "2.0"
        or manifest.get("freeze_record") != EXPECTED_FREEZE_RECORD
        or set(manifest.get("assets", {})) != set(EXPECTED_ASSETS)
    ):
        raise ValueError("REPORT_TEMPLATE_FREEZE_RECORD_MISMATCH")
    result: dict[str, str] = {}
    for name in ALL_TRUSTED_ASSETS:
        candidate = root / name
        if _is_reparse_or_symlink(candidate) or candidate.resolve().parent != root:
            raise ValueError("REPORT_TEMPLATE_ASSET_PATH_UNTRUSTED")
        digest = _sha256(candidate)
        if name != "asset_hashes.json" and digest != manifest["assets"].get(name):
            raise ValueError("REPORT_TEMPLATE_ASSET_HASH_MISMATCH")
        result[name] = digest
    return result


def verify_packaged_assets(packaged_asset_dir: Path) -> None:
    source = verify_asset_dir(SOURCE_ASSET_DIR, require_trust_root=True)
    packaged = verify_asset_dir(packaged_asset_dir, require_trust_root=True)
    if source != packaged:
        raise ValueError("REPORT_TEMPLATE_PACKAGED_ASSET_MISMATCH")


def verify_word_acceptance_evidence() -> None:
    evidence = json.loads(WORD_ACCEPTANCE_EVIDENCE.read_text(encoding="utf-8"))
    if (
        _sha256(WORD_ACCEPTANCE_EVIDENCE) != EXPECTED_WORD_ACCEPTANCE_EVIDENCE_SHA256
        or evidence.get("runtime_template_sha256") != _sha256(SOURCE_ASSET_DIR / "runtime_template.docx")
        or evidence.get("open_method") != "OpenNoRepairDialog"
        or evidence.get("display_alerts") != "all"
        or evidence.get("roundtrip_saved_and_reopened") is not True
        or evidence.get("section_count") != EXPECTED_FREEZE_RECORD["section_count"]
        or evidence.get("table_count") != EXPECTED_FREEZE_RECORD["table_count"]
        or evidence.get("content_control_count") != EXPECTED_FREEZE_RECORD["word_content_control_count"]
    ):
        raise ValueError("REPORT_TEMPLATE_WORD_ACCEPTANCE_EVIDENCE_MISMATCH")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packaged-asset-dir", type=Path)
    args = parser.parse_args()
    verify_word_acceptance_evidence()
    if args.packaged_asset_dir is None:
        verify_asset_dir(SOURCE_ASSET_DIR, require_trust_root=True)
        print("PASS: R0 source freeze contract is valid.")
    else:
        verify_packaged_assets(args.packaged_asset_dir)
        print("PASS: R0 source and packaged six-asset sets are identical.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
