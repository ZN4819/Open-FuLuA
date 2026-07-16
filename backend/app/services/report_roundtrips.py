"""R7 controlled Word upload, three-way diff, resolution and atomic commit."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import stat
import threading
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from fastapi import UploadFile

from .. import database
from ..config import settings
from ..report_derived.rules import canonical_json
from ..report_roundtrip.contracts import (
    normalize_value,
    opaque_token,
    validate_slots_against_current_policy,
    value_hash,
)
from ..report_roundtrip.key_store import load_signing_key
from ..report_roundtrip.keys import RoundtripKeyError
from ..report_roundtrip.manifest import (
    ManifestSecurityError,
    canonical_json_bytes,
    compute_writable_contract_hash,
    verify_signed_manifest,
)
from ..report_roundtrip.package import (
    OpcSecurityError,
    extract_manifest,
    read_safe_opc,
    validate_roundtrip_opc,
)
from ..report_roundtrip.schemas import (
    ReportRoundtripCommitWrite,
    ReportRoundtripResolutionWrite,
)
from ..report_roundtrip.structure import (
    StructureSecurityError,
    extract_roundtrip_structure,
    is_comment_part,
    readonly_document_hash,
)
from .appendix_scoring import AppendixScoringError, recalculate_appendix_scores_locked
from .report_domain.errors import ReportDomainError
from .report_generation import regenerate_after_roundtrip_locked


MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MAX_PRIVATE_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_RETAINED_FAILED_UPLOADS = 8
PRIVATE_ROOT = Path("private") / "roundtrip" / "imports"
_UPLOAD_LOCK = threading.Lock()
REVIEW_STATUSES = {"diff_ready", "conflicts_pending", "ready_to_commit"}
TERMINAL_STATUSES = {"invalid", "succeeded", "failed", "stale"}
SECTION_TITLES = {
    "A-1": "物理和环境安全",
    "A-2": "网络和通信安全",
    "A-3": "设备和计算安全",
    "A-4": "应用和数据安全",
    "A-5": "管理制度",
    "A-6": "人员管理",
    "A-7": "建设运行",
    "A-8": "应急处置",
}
FIELD_LABELS = {
    "report.identity.number": "报告编号",
    "report.organization.assessed_name": "被测单位",
    "report.system.name": "系统名称",
    "report.system.overview": "系统简介",
    "report.distribution.regulator_copies": "主管部门分发份数",
    "report.distribution.client_copies": "委托/被测单位分发份数",
    "report.distribution.assessment_copies": "密评机构留存份数",
    "object_name": "测评对象",
    "record_text": "测评结果记录",
    "d": "D",
    "a": "A",
    "k": "K",
    "compliance": "符合情况",
}
SECURITY_MESSAGES = {
    "WORD_TRACKED_CHANGES_NOT_ACCEPTED": "文档仍包含未接受或未拒绝的修订。",
    "MANIFEST_SIGNATURE_INVALID": "文档签名校验失败，不能确认其来自本机工具。",
    "MANIFEST_SIGNING_KEY_UNAVAILABLE": "本机签名密钥不可用，旧草稿不能验证。",
    "CUSTOM_XML_PART_SET_INVALID": "受控清单部件缺失、重复或被 Word 以外的工具改写。",
    "SDT_CONTROLLED_TAG_DUPLICATE": "受控字段或业务行被复制，结构不再唯一。",
    "SDT_ROW_CONTENT_INVALID": "附录 A 业务行结构被拆分或改写。",
    "EXTERNAL_RELATIONSHIP_PRESENT": "文档包含外部链接或外部关系。",
    "OPC_DANGEROUS_PART": "文档包含不允许的嵌入对象或主动内容。",
    "WORD_FIELD_INSTRUCTION_FORBIDDEN": "文档包含不允许执行的 Word 字段指令。",
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_json(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(str(value)) if value not in (None, "") else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _private_root() -> Path:
    return (settings.database_path.parent / PRIVATE_ROOT).resolve()


def _relative_private(path: Path) -> str:
    root = settings.database_path.parent.resolve()
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ReportDomainError(
            "ROUNDTRIP_PRIVATE_PATH_INVALID",
            "受控文档隔离路径异常。",
            status_code=500,
        )
    return resolved.relative_to(root).as_posix()


def _resolve_private(relative: str) -> Path:
    root = settings.database_path.parent.resolve()
    path = (root / relative).resolve()
    private = _private_root()
    if private != path and private not in path.parents:
        raise ReportDomainError(
            "ROUNDTRIP_PRIVATE_PATH_INVALID",
            "受控文档隔离路径异常。",
            status_code=500,
        )
    return path


def _safe_original_name(value: str | None) -> str:
    name = re.split(r"[\\/]", value or "")[-1].strip()
    name = "".join(char for char in name if char >= " " and char not in '<>:"|?*')
    if not name.lower().endswith(".docx"):
        raise ReportDomainError(
            "ROUNDTRIP_FILE_TYPE_INVALID",
            "仅接受由本工具生成的 DOCX 可回收草稿。",
            status_code=422,
            field="file",
        )
    return name[:240] or "可回收草稿.docx"


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = int(getattr(path.lstat(), "st_file_attributes", 0))
    except OSError:
        return False
    return bool(attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)))


def _private_tree_size() -> int:
    root = _private_root()
    if not root.exists():
        return 0
    if _is_reparse_point(root):
        raise ReportDomainError(
            "ROUNDTRIP_PRIVATE_STORAGE_UNSAFE",
            "Word 回收隔离目录不是安全的本地目录。",
            status_code=500,
        )
    total = 0
    for directory, names, files in os.walk(root, followlinks=False):
        base = Path(directory)
        safe_names: list[str] = []
        for name in names:
            child = base / name
            if _is_reparse_point(child) or child.is_symlink():
                raise ReportDomainError(
                    "ROUNDTRIP_PRIVATE_STORAGE_UNSAFE",
                    "Word 回收隔离目录包含不安全的重解析点。",
                    status_code=500,
                )
            safe_names.append(name)
        names[:] = safe_names
        for name in files:
            child = base / name
            if _is_reparse_point(child) or child.is_symlink():
                raise ReportDomainError(
                    "ROUNDTRIP_PRIVATE_STORAGE_UNSAFE",
                    "Word 回收隔离目录包含不安全的重解析点。",
                    status_code=500,
                )
            total += child.stat().st_size
    return total


def _remove_private_job_dir(job_id: int) -> None:
    root = _private_root()
    target = (root / str(int(job_id))).resolve()
    if target.parent != root or not target.exists():
        return
    if _is_reparse_point(target) or target.is_symlink():
        raise ReportDomainError(
            "ROUNDTRIP_PRIVATE_STORAGE_UNSAFE",
            "拒绝清理不安全的 Word 回收隔离目录。",
            status_code=500,
        )
    shutil.rmtree(target)


def remove_roundtrip_job_files(job_ids: Iterable[int]) -> None:
    # Serialize deletion with create_roundtrip_job's insert/write window.  A
    # project can be deleted after the DB job row is committed but before the
    # source file is published; without this lock cleanup could run too early
    # and the uploader would then create an unreachable private orphan.
    with _UPLOAD_LOCK:
        for job_id in job_ids:
            _remove_private_job_dir(int(job_id))


def _prune_failed_roundtrip_uploads() -> None:
    with database.connect() as db:
        rows = db.execute(
            """
            SELECT id FROM report_import_jobs
            WHERE mode='roundtrip' AND roundtrip_status IN ('invalid','failed')
              AND NOT EXISTS (SELECT 1 FROM report_import_audits a WHERE a.job_id=report_import_jobs.id)
            ORDER BY created_at DESC, id DESC
            LIMIT -1 OFFSET ?
            """,
            (MAX_RETAINED_FAILED_UPLOADS,),
        ).fetchall()
    for row in rows:
        job_id = int(row["id"])
        _remove_private_job_dir(job_id)
        with database.connect() as db:
            db.execute(
                """
                UPDATE report_import_jobs
                SET source_docx_path='', archived_relative_path=NULL,
                    summary_json=json_set(COALESCE(summary_json, '{}'), '$.source_pruned', json('true'))
                WHERE id=? AND roundtrip_status IN ('invalid','failed')
                """,
                (job_id,),
            )


def _write_upload(file: UploadFile, target: Path, *, existing_private_bytes: int) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".uploading")
    digest = hashlib.sha256()
    total = 0
    try:
        with temporary.open("xb") as output:
            while chunk := file.file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise ReportDomainError(
                        "ROUNDTRIP_FILE_TOO_LARGE",
                        "DOCX 文件超过 64 MB 限制。",
                        status_code=413,
                        field="file",
                    )
                if existing_private_bytes + total > MAX_PRIVATE_TOTAL_BYTES:
                    raise ReportDomainError(
                        "ROUNDTRIP_PRIVATE_QUOTA_EXCEEDED",
                        "Word 回收隔离区已达到 1 GB 上限，请清理失效任务后重试。",
                        status_code=507,
                        field="file",
                    )
                digest.update(chunk)
                output.write(chunk)
        if total == 0:
            raise ReportDomainError(
                "ROUNDTRIP_FILE_EMPTY", "DOCX 文件为空。", status_code=422, field="file"
            )
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return digest.hexdigest()


def _state_revision(db: sqlite3.Connection, project_id: int) -> int:
    row = db.execute(
        "SELECT project_revision FROM report_generation_state WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if row is None:
        raise ReportDomainError(
            "REPORT_GENERATION_STATE_MISSING",
            "项目缺少派生状态，不能执行 Word 回收。",
            status_code=409,
        )
    return int(row["project_revision"])


def _job_row(db: sqlite3.Connection, job_id: int) -> sqlite3.Row:
    row = db.execute(
        """
        SELECT j.*, p.project_uuid
        FROM report_import_jobs j
        JOIN projects p ON p.id = j.project_id
        WHERE j.id = ? AND j.mode = 'roundtrip'
        """,
        (job_id,),
    ).fetchone()
    if row is None:
        raise ReportDomainError(
            "REPORT_ROUNDTRIP_JOB_NOT_FOUND",
            "Word 回收任务不存在。",
            status_code=404,
        )
    return row


def _job_result(db: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    manifest_row = None
    if row["document_instance_id"]:
        manifest_row = db.execute(
            "SELECT baseline_hash, manifest_json FROM report_roundtrip_manifests WHERE document_instance_id = ?",
            (row["document_instance_id"],),
        ).fetchone()
    manifest = _load_json(manifest_row["manifest_json"], {}) if manifest_row else {}
    summary = _load_json(row["summary_json"], {})
    return {
        "id": int(row["id"]),
        "project_uuid": str(row["project_uuid"]),
        "mode": "roundtrip",
        "status": str(row["roundtrip_status"] or "failed"),
        "original_name": str(row["original_name"] or ""),
        "base_project_revision": int(row["base_project_revision"] or 0),
        "observed_project_revision": int(row["observed_project_revision"] or 0),
        "source_snapshot_id": row["source_snapshot_uuid"],
        "source_docx_hash": row["source_sha256"],
        "manifest_hash": row["manifest_hash"],
        "source_snapshot_hash": manifest.get("snapshot_hash"),
        "writable_contract_hash": manifest.get("writable_contract_hash"),
        "diff_hash": row["diff_hash"],
        "resolution_hash": row["resolution_hash"],
        "error_code": row["error_code"],
        "error_message": row["error_message"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "committed_at": summary.get("committed_at"),
    }


def _issue(
    db: sqlite3.Connection,
    job_id: int,
    *,
    code: str,
    message: str,
    severity: str = "error",
    phase: str = "validation",
    blocks: bool = True,
    field_id: str | None = None,
    field_path: str = "",
    row_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    timestamp = database.utc_now()
    payload = {"message": message, "phase": phase, "row_id": row_id, **(details or {})}
    db.execute(
        """
        INSERT INTO report_import_issues (
            job_id, revision, code, severity, authority_field_id, field_path,
            source_locator, original_text, candidate_value_json, confidence,
            status, needs_confirmation, blocks_confirmation,
            blocks_final_export, created_at, updated_at
        ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, 'exact', 'open', 0, ?, ?, ?, ?)
        """,
        (
            job_id, code, severity, field_id, field_path, phase, message,
            _json(payload), int(blocks), int(blocks), timestamp, timestamp,
        ),
    )


def _mark_invalid(job_id: int, code: str, message: str, *, phase: str = "validation") -> None:
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM report_import_issues WHERE job_id = ?", (job_id,))
        _issue(db, job_id, code=code, message=message, phase=phase)
        db.execute(
            """
            UPDATE report_import_jobs
            SET status='failed', roundtrip_status='invalid', error_code=?, error_message=?,
                finished_at=?, job_revision=job_revision+1
            WHERE id=? AND mode='roundtrip'
            """,
            (code, message, database.utc_now(), job_id),
        )


def _clear_abandoned_partial_uploads() -> None:
    root = _private_root()
    if not root.exists():
        return
    # Validate the entire private tree first.  Recovery must never traverse a
    # reparse point or symlink supplied outside the controlled job layout.
    _private_tree_size()
    for job_dir in root.iterdir():
        if not job_dir.name.isdecimal() or not job_dir.is_dir():
            continue
        partial = job_dir / "source.uploading"
        if partial.exists():
            if partial.is_symlink() or _is_reparse_point(partial):
                raise ReportDomainError(
                    "ROUNDTRIP_PRIVATE_STORAGE_UNSAFE",
                    "拒绝清理不安全的 Word 回收临时文件。",
                    status_code=500,
                )
            partial.unlink()
        try:
            job_dir.rmdir()
        except OSError:
            # A complete source.docx is retained under the bounded failed-job
            # policy for diagnostics; only truly empty job folders disappear.
            pass


def recover_abandoned_roundtrip_jobs() -> int:
    """Fail closed after a process interruption during upload/validation."""

    code = "ROUNDTRIP_PROCESS_INTERRUPTED"
    message = "上次 Word 回收在上传或验证期间中断，请重新上传可回收草稿。"
    with _UPLOAD_LOCK:
        _clear_abandoned_partial_uploads()
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                """
                SELECT id FROM report_import_jobs
                WHERE mode='roundtrip' AND roundtrip_status IN ('uploaded','validating')
                ORDER BY id
                """
            ).fetchall()
            for row in rows:
                job_id = int(row["id"])
                db.execute("DELETE FROM report_import_issues WHERE job_id=?", (job_id,))
                _issue(
                    db,
                    job_id,
                    code=code,
                    message=message,
                    phase="startup_recovery",
                )
                db.execute(
                    """
                    UPDATE report_import_jobs
                    SET status='failed', roundtrip_status='invalid',
                        error_code=?, error_message=?, finished_at=?,
                        job_revision=job_revision+1
                    WHERE id=? AND mode='roundtrip'
                    """,
                    (code, message, database.utc_now(), job_id),
                )
        _prune_failed_roundtrip_uploads()
    return len(rows)


def _security_message(code: str) -> str:
    return SECURITY_MESSAGES.get(
        code,
        "文档未通过受控来源、结构或主动内容检查，不能回写项目。",
    )


def create_roundtrip_job(project_uuid: str, file: UploadFile) -> dict[str, Any]:
    original_name = _safe_original_name(file.filename)
    with _UPLOAD_LOCK:
        _prune_failed_roundtrip_uploads()
        existing_private_bytes = _private_tree_size()
        if existing_private_bytes >= MAX_PRIVATE_TOTAL_BYTES:
            raise ReportDomainError(
                "ROUNDTRIP_PRIVATE_QUOTA_EXCEEDED",
                "Word 回收隔离区已达到 1 GB 上限，请清理失效任务后重试。",
                status_code=507,
                field="file",
            )
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            project = database.get_project_by_uuid(project_uuid, db)
            if project is None or project["project_type"] != "full_report":
                raise ReportDomainError(
                    "REPORT_PROJECT_NOT_FOUND",
                    "完整报告项目不存在。",
                    status_code=404,
                    project_uuid=project_uuid,
                )
            project_id = int(project["id"])
            revision = _state_revision(db, project_id)
            timestamp = database.utc_now()
            cursor = db.execute(
                """
                INSERT INTO report_import_jobs (
                    mode, status, job_revision, original_name, source_docx_path,
                    fingerprint_json, summary_json, created_at, project_id,
                    roundtrip_status, base_project_revision, observed_project_revision,
                    diff_json, resolution_json
                ) VALUES ('roundtrip', 'uploaded', 1, ?, '', '{}', '{}', ?, ?,
                          'uploaded', ?, ?, '{}', '{}')
                """,
                (original_name, timestamp, project_id, revision, revision),
            )
            job_id = int(cursor.lastrowid)

        target = _private_root() / str(job_id) / "source.docx"
        try:
            source_hash = _write_upload(
                file,
                target,
                existing_private_bytes=existing_private_bytes,
            )
            with database.connect() as db:
                db.execute(
                    "UPDATE report_import_jobs SET source_docx_path=?, source_sha256=? WHERE id=?",
                    (_relative_private(target), source_hash, job_id),
                )
        except ReportDomainError as exc:
            _mark_invalid(job_id, exc.code, exc.message, phase="upload")
            return get_roundtrip_job(job_id)
        except Exception as exc:  # noqa: BLE001 - boundary intentionally redacts source/path
            _mark_invalid(
                job_id,
                "ROUNDTRIP_UPLOAD_FAILED",
                f"可回收草稿上传失败（{type(exc).__name__}）。",
                phase="upload",
            )
            return get_roundtrip_job(job_id)
    _process_roundtrip_job(job_id)
    return get_roundtrip_job(job_id)


def _authoritative_contract(
    db: sqlite3.Connection,
    project_id: int,
    manifest: dict[str, Any],
) -> tuple[sqlite3.Row, dict[str, Any]]:
    stored = db.execute(
        """
        SELECT m.*, s.context_hash AS snapshot_hash, s.project_revision AS snapshot_revision
        FROM report_roundtrip_manifests m
        JOIN report_export_snapshots s ON s.snapshot_uuid = m.snapshot_uuid
        WHERE m.document_instance_id = ? AND m.project_id = ?
        """,
        (manifest["document_instance_id"], project_id),
    ).fetchone()
    if stored is None:
        raise ReportDomainError(
            "ROUNDTRIP_MANIFEST_NOT_ISSUED",
            "本机没有该可回收草稿的签发记录。",
            status_code=422,
        )
    stored_manifest = _load_json(stored["manifest_json"], {})
    if not hmac.compare_digest(
        hashlib.sha256(canonical_json_bytes(manifest)).hexdigest(),
        hashlib.sha256(canonical_json_bytes(stored_manifest)).hexdigest(),
    ):
        raise ReportDomainError(
            "ROUNDTRIP_MANIFEST_MISMATCH",
            "文档清单与本机签发记录不一致。",
            status_code=422,
        )
    baseline = _load_json(stored["baseline_json"], {})
    checks = (
        (manifest["project_revision"] == stored["snapshot_revision"], "ROUNDTRIP_SNAPSHOT_REVISION_MISMATCH"),
        (manifest["snapshot_hash"] == stored["snapshot_hash"], "ROUNDTRIP_SNAPSHOT_HASH_MISMATCH"),
        (manifest["manifest_hash"] == stored["manifest_hash"], "ROUNDTRIP_MANIFEST_HASH_MISMATCH"),
        (baseline.get("baseline_hash") == stored["baseline_hash"], "ROUNDTRIP_BASELINE_HASH_MISMATCH"),
        (
            baseline.get("structure_contract_hash") == stored["structure_contract_hash"],
            "ROUNDTRIP_STRUCTURE_CONTRACT_MISMATCH",
        ),
        (
            baseline.get("writable_contract_hash") == manifest["writable_contract_hash"],
            "ROUNDTRIP_WRITABLE_CONTRACT_MISMATCH",
        ),
    )
    for valid, code in checks:
        if not valid:
            raise ReportDomainError(code, "可回收草稿的本机权威基线不一致。", status_code=500)
    slots = baseline.get("slots") or []
    rows = baseline.get("rows") or []
    try:
        validate_slots_against_current_policy(slots)
    except ValueError as exc:
        raise ReportDomainError(
            "ROUNDTRIP_CURRENT_POLICY_REVOKED",
            "当前字段矩阵已收窄 Word 回收权限，请重新生成可回收草稿。",
            status_code=422,
        ) from exc
    fields = [
        {
            key: item[key]
            for key in (
                "slot_id", "authority_field_id", "entity_path", "value_type",
                "normalizer_id", "projection_group",
            )
        }
        for item in slots
    ]
    baseline_hashes = {str(item["slot_id"]): str(item["value_hash"]) for item in slots}
    if compute_writable_contract_hash(fields, rows, baseline_hashes) != manifest["writable_contract_hash"]:
        raise ReportDomainError(
            "ROUNDTRIP_BASELINE_CONTRACT_INVALID",
            "本机权威基线的可写契约已损坏。",
            status_code=500,
        )
    return stored, baseline


def _validate_structure(
    parts: dict[str, bytes],
    baseline: dict[str, Any],
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    contract = baseline["structure_contract"]
    if not hmac.compare_digest(
        readonly_document_hash(parts), str(contract["readonly_document_hash"])
    ):
        raise ReportDomainError(
            "ROUNDTRIP_READONLY_CONTENT_CHANGED",
            "只读正文、表格拓扑或受控结构已变化，不能回写。",
            status_code=422,
        )
    structure = extract_roundtrip_structure(parts)
    expected_rows = {str(item["row_token"]): item for item in baseline["rows"]}
    actual_rows = {item.token: item for item in structure.rows}
    if set(expected_rows) != set(actual_rows):
        raise ReportDomainError(
            "ROUNDTRIP_ROW_SET_CHANGED",
            "附录 A 业务行被新增、删除或复制。",
            status_code=422,
        )
    for token, expected in expected_rows.items():
        actual = actual_rows[token]
        if (
            actual.block_token != expected["block_token"]
            or actual.sort_order != int(expected["sort_order"])
            or actual.slot_tokens != tuple(expected["writable_slot_ids"])
            or actual.geometry_hash != expected["geometry_hash"]
        ):
            raise ReportDomainError(
                "ROUNDTRIP_ROW_STRUCTURE_CHANGED",
                "附录 A 行顺序、单元格合并或白名单字段结构已变化。",
                status_code=422,
            )
    expected_blocks: dict[str, list[str]] = defaultdict(list)
    for row in baseline["rows"]:
        expected_blocks[str(row["block_token"])].append(str(row["row_token"]))
    actual_blocks = {item.token: list(item.row_tokens) for item in structure.blocks}
    if dict(expected_blocks) != actual_blocks:
        raise ReportDomainError(
            "ROUNDTRIP_BLOCK_STRUCTURE_CHANGED",
            "附录 A 表格或业务行顺序已变化。",
            status_code=422,
        )
    slot_values = {item.token: item.value for item in structure.slots}
    expected_slots = {str(item["slot_id"]) for item in baseline["slots"]}
    if set(slot_values) != expected_slots:
        raise ReportDomainError(
            "ROUNDTRIP_SLOT_SET_CHANGED",
            "可回收字段被新增、删除或复制。",
            status_code=422,
        )
    expected_media = dict(contract.get("media_hashes") or {})
    actual_media = {
        name: hashlib.sha256(data).hexdigest()
        for name, data in sorted(parts.items())
        if name.startswith("word/media/")
    }
    ignored: list[dict[str, Any]] = []
    if expected_media != actual_media:
        ignored.append(
            {
                "id": opaque_token("ignored", "media"),
                "field_path": "document.media",
                "field_label": "图片和媒体",
                "field_type": "media",
                "base_value": len(expected_media),
                "database_value": len(expected_media),
                "word_value": len(actual_media),
                "disposition": "ignored",
                "ignored_reason": "图片变化不会从 Word 回收，请在工具内维护证据图片。",
            }
        )
    expected_comments = dict(contract.get("comment_hashes") or {})
    actual_comments = {
        name: hashlib.sha256(data).hexdigest()
        for name, data in sorted(parts.items())
        if is_comment_part(name)
    }
    if expected_comments != actual_comments:
        ignored.append(
            {
                "id": opaque_token("ignored", "comments"),
                "field_path": "document.comments",
                "field_label": "Word 批注",
                "field_type": "comments",
                "base_value": len(expected_comments),
                "database_value": len(expected_comments),
                "word_value": len(actual_comments),
                "disposition": "ignored",
                "ignored_reason": "批注不会作为业务数据回收。",
            }
        )
    return slot_values, ignored


def _row_for_slot(
    db: sqlite3.Connection,
    project_id: int,
    row_uuid: str,
) -> sqlite3.Row:
    row = db.execute(
        """
        SELECT r.*, s.code AS section_code, m.d, m.a, m.k, m.ra, m.rk,
               m.object_score, m.unit_score, m.compliance
        FROM assessment_rows r
        JOIN appendix_sections s ON s.id = r.section_id
        LEFT JOIN metric_results m ON m.row_id = r.id
        WHERE s.project_id = ? AND r.row_uuid = ?
        """,
        (project_id, row_uuid),
    ).fetchone()
    if row is None:
        raise ReportDomainError(
            "ROUNDTRIP_DATABASE_ROW_MISSING",
            "签发后的附录 A 业务行已不存在。",
            status_code=409,
        )
    return row


def _database_value(db: sqlite3.Connection, project_id: int, slot: dict[str, Any]) -> Any:
    kind = str(slot["binding_kind"])
    key = str(slot["binding_key"])
    authority = str(slot["authority_field_id"])
    if kind == "scalar":
        scalar_queries = {
            "report.identity.number": ("SELECT report_number AS value FROM report_metadata WHERE project_id=?", (project_id,)),
            "report.organization.assessed_name": (
                "SELECT name AS value FROM report_organizations WHERE project_id=? AND organization_type='assessed' AND active=1 ORDER BY sort_order,id LIMIT 1",
                (project_id,),
            ),
            "report.system.name": ("SELECT system_name AS value FROM system_profiles WHERE project_id=?", (project_id,)),
            "report.system.overview": ("SELECT system_summary AS value FROM system_profiles WHERE project_id=?", (project_id,)),
            "report.distribution.regulator_copies": ("SELECT regulator_copies AS value FROM report_distribution WHERE project_id=?", (project_id,)),
            "report.distribution.client_copies": ("SELECT client_copies AS value FROM report_distribution WHERE project_id=?", (project_id,)),
            "report.distribution.assessment_copies": ("SELECT assessment_organization_copies AS value FROM report_distribution WHERE project_id=?", (project_id,)),
        }
        query = scalar_queries.get(authority)
        if query is None:
            raise ReportDomainError("ROUNDTRIP_FIELD_NOT_WRITABLE", "字段不在回收白名单内。", status_code=422)
        row = db.execute(query[0], query[1]).fetchone()
        if row is None:
            raise ReportDomainError("ROUNDTRIP_DATABASE_FIELD_MISSING", "项目字段不存在。", status_code=409)
        return row["value"]
    if kind == "object_name":
        row = db.execute(
            "SELECT name_snapshot AS value FROM assessment_objects WHERE project_id=? AND object_uuid=? AND active=1",
            (project_id, key),
        ).fetchone()
        if row is None:
            raise ReportDomainError("ROUNDTRIP_DATABASE_OBJECT_MISSING", "测评对象已不存在。", status_code=409)
        return row["value"]
    if kind == "assessment_row":
        row = _row_for_slot(db, project_id, key)
        column = str(slot.get("column_id") or "")
        if column == "record_text":
            return row["record_text"]
        if column in {"d", "a", "k", "compliance"}:
            return row[column]
    raise ReportDomainError("ROUNDTRIP_FIELD_NOT_WRITABLE", "字段不在回收白名单内。", status_code=422)


def _coerce_value(slot: dict[str, Any], value: Any) -> str:
    try:
        normalized = normalize_value(
            value,
            str(slot["normalizer_id"]),
            options=list(slot.get("options") or []) or None,
        )
    except ValueError as exc:
        raise ReportDomainError(
            "ROUNDTRIP_VALUE_INVALID",
            "Word 中的白名单字段值不符合允许格式。",
            status_code=422,
            field=str(slot.get("column_id") or slot.get("authority_field_id") or ""),
        ) from exc
    authority = str(slot["authority_field_id"])
    column = str(slot.get("column_id") or "")
    limits = {
        "report.identity.number": 120,
        "report.organization.assessed_name": 200,
        "report.system.name": 300,
        "report.system.overview": 20_000,
        "object_name": 500,
        "record_text": 100_000,
    }
    limit = limits.get(column, limits.get(authority))
    if limit is not None and len(normalized) > limit:
        raise ReportDomainError(
            "ROUNDTRIP_VALUE_TOO_LONG",
            "Word 中的字段内容超过工具允许长度。",
            status_code=422,
        )
    if column == "record_text" and "[[FIG:" in normalized:
        raise ReportDomainError(
            "ROUNDTRIP_IMAGE_REFERENCE_NOT_WRITABLE",
            "图片引用必须在工具内维护，不能从 Word 新增。",
            status_code=422,
        )
    if authority.startswith("report.distribution."):
        if not normalized.isdigit() or not 0 <= int(normalized) <= 100:
            raise ReportDomainError(
                "ROUNDTRIP_DISTRIBUTION_INVALID",
                "报告分发份数必须是 0 至 100 的整数。",
                status_code=422,
            )
    return normalized


def _item_context(db: sqlite3.Connection, project_id: int, slot: dict[str, Any]) -> dict[str, Any]:
    section = slot.get("section_code")
    row_id = slot.get("row_uuid")
    object_name = None
    entity_uuid = None
    if row_id:
        row = _row_for_slot(db, project_id, str(row_id))
        object_name = str(row["object_name"] or "")
        entity_uuid = str(row["assessment_object_uuid"] or "") or None
    elif slot.get("binding_kind") == "object_name":
        entity_uuid = str(slot["binding_key"])
        object_name = str(_database_value(db, project_id, slot) or "")
    return {
        "section_code": section,
        "section_title": SECTION_TITLES.get(str(section)) if section else None,
        "row_id": row_id,
        "entity_uuid": entity_uuid,
        "object_name": object_name,
    }


def _build_diff(
    db: sqlite3.Connection,
    job: sqlite3.Row,
    baseline: dict[str, Any],
    slot_values: dict[str, str],
    ignored: list[dict[str, Any]],
) -> dict[str, Any]:
    project_id = int(job["project_id"])
    grouped_slots: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for slot in baseline["slots"]:
        if value_hash(slot, slot["value"]) != slot["value_hash"]:
            raise ReportDomainError(
                "ROUNDTRIP_BASELINE_VALUE_HASH_INVALID",
                "本机权威基线值校验失败。",
                status_code=500,
            )
        grouped_slots[str(slot["projection_group"])].append(slot)
    items: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for projection, slots in sorted(grouped_slots.items()):
        base_values = {_coerce_value(slot, slot["value"]) for slot in slots}
        word_values = {_coerce_value(slot, slot_values[str(slot["slot_id"])]) for slot in slots}
        database_values = {
            _coerce_value(slot, _database_value(db, project_id, slot)) for slot in slots
        }
        if len(base_values) != 1:
            raise ReportDomainError(
                "ROUNDTRIP_BASELINE_PROJECTION_INCONSISTENT",
                "同一权威字段的签发基线不一致。",
                status_code=500,
            )
        if len(word_values) != 1:
            raise ReportDomainError(
                "ROUNDTRIP_WORD_PROJECTION_INCONSISTENT",
                "同一权威字段在 Word 多处投影中的值不一致。",
                status_code=422,
            )
        if len(database_values) != 1:
            raise ReportDomainError(
                "ROUNDTRIP_DATABASE_PROJECTION_INCONSISTENT",
                "同一权威字段在数据库中的投影不一致。",
                status_code=409,
            )
        base = next(iter(base_values))
        word = next(iter(word_values))
        current = next(iter(database_values))
        if base == current == word:
            disposition = "unchanged"
        elif current == base and word != base:
            disposition = "apply_word"
        elif word == base and current != base:
            disposition = "keep_database"
        elif word == current and current != base:
            disposition = "already_equal"
        else:
            disposition = "conflict"
        slot = slots[0]
        item_id = opaque_token("diff", projection)
        conflict_id = opaque_token("conflict", projection) if disposition == "conflict" else None
        context = _item_context(db, project_id, slot)
        column = str(slot.get("column_id") or "")
        label_key = column or str(slot["authority_field_id"])
        item = {
            "id": item_id,
            "conflict_id": conflict_id,
            "field_path": str(slot["entity_path"]),
            "field_label": FIELD_LABELS.get(label_key, label_key),
            "field_type": str(slot["value_type"]),
            **context,
            "base_value": base,
            "database_value": current,
            "word_value": word,
            "disposition": disposition,
            "resolution": None,
            "ignored_reason": None,
            "slot_ids": [str(item["slot_id"]) for item in slots],
            "binding_kind": str(slot["binding_kind"]),
            "binding_key": str(slot["binding_key"]),
            "authority_field_id": str(slot["authority_field_id"]),
            "column_id": column or None,
            "normalizer_id": str(slot["normalizer_id"]),
            "options": list(slot.get("options") or []),
            "projection_group": projection,
        }
        items.append(item)
        if conflict_id:
            conflicts.append(item)
    summary = Counter(str(item["disposition"]) for item in items)
    response_summary = {
        "total": len(items),
        "unchanged": summary["unchanged"],
        "keep_database": summary["keep_database"],
        "apply_word": summary["apply_word"],
        "already_equal": summary["already_equal"],
        "conflicts": summary["conflict"],
        "ignored": len(ignored),
    }
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        key = (str(item.get("section_code") or "report"), str(item.get("object_name") or ""))
        groups[key].append(item)
    group_items = [
        {
            "group_key": f"{key[0]}:{opaque_token('group', *key)}",
            "section_code": None if key[0] == "report" else key[0],
            "section_title": "项目字段" if key[0] == "report" else SECTION_TITLES.get(key[0]),
            "object_name": key[1] or None,
            "items": values,
        }
        for key, values in sorted(groups.items())
    ]
    payload = {
        "schema_version": "1.0",
        "job_id": int(job["id"]),
        "project_uuid": str(job["project_uuid"]),
        "document_instance_id": str(job["document_instance_id"]),
        "base_project_revision": int(job["base_project_revision"]),
        "observed_project_revision": int(job["observed_project_revision"]),
        "summary": response_summary,
        "groups": group_items,
        "items": items,
        "ignored_changes": ignored,
    }
    payload["diff_hash"] = _hash(payload)
    return payload


def _replace_conflicts(db: sqlite3.Connection, job_id: int, diff: dict[str, Any]) -> None:
    db.execute("DELETE FROM report_sync_conflicts WHERE job_id = ?", (job_id,))
    timestamp = database.utc_now()
    for item in diff["items"]:
        if item["disposition"] != "conflict":
            continue
        db.execute(
            """
            INSERT INTO report_sync_conflicts (
                job_id, conflict_id, field_id, field_path, row_uuid,
                base_value_json, database_value_json, word_value_json,
                conflict_kind, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'three_way', ?, ?)
            """,
            (
                job_id, item["conflict_id"], item["authority_field_id"], item["field_path"],
                item.get("row_id"), _json(item["base_value"]), _json(item["database_value"]),
                _json(item["word_value"]), timestamp, timestamp,
            ),
        )


def _automatic_resolution(diff: dict[str, Any]) -> dict[str, Any]:
    value = {
        "schema_version": "1.0",
        "diff_hash": diff["diff_hash"],
        "expected_project_revision": diff["observed_project_revision"],
        "resolutions": {},
    }
    value["resolution_hash"] = _hash(value)
    return value


def _process_roundtrip_job(job_id: int) -> None:
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        job = _job_row(db, job_id)
        db.execute(
            """
            UPDATE report_import_jobs
            SET status='parsing', roundtrip_status='validating', started_at=?,
                error_code=NULL, error_message=NULL, job_revision=job_revision+1
            WHERE id=? AND roundtrip_status='uploaded'
            """,
            (database.utc_now(), job_id),
        )
        job = _job_row(db, job_id)
        source = _resolve_private(str(job["source_docx_path"]))
    try:
        package = read_safe_opc(source)
        validate_roundtrip_opc(package)
        raw_manifest = extract_manifest(package.parts)
        key = load_signing_key()
        manifest = verify_signed_manifest(raw_manifest, {key.key_id: key})
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = _job_row(db, job_id)
            if manifest["project_uuid"] != job["project_uuid"]:
                raise ReportDomainError(
                    "ROUNDTRIP_PROJECT_MISMATCH",
                    "草稿不属于当前项目。",
                    status_code=422,
                )
            stored, baseline = _authoritative_contract(db, int(job["project_id"]), manifest)
            if package.source_sha256 != job["source_sha256"]:
                raise ReportDomainError(
                    "ROUNDTRIP_SOURCE_CHANGED_AFTER_UPLOAD",
                    "隔离区文档在校验期间发生变化。",
                    status_code=500,
                )
            slot_values, ignored = _validate_structure(package.parts, baseline)
            current_revision = _state_revision(db, int(job["project_id"]))
            db.execute(
                """
                UPDATE report_import_jobs
                SET document_instance_id=?, source_snapshot_uuid=?, base_project_revision=?,
                    observed_project_revision=?, manifest_hash=?, structure_contract_hash=?,
                    baseline_hash=?, archived_relative_path=?, archived_hash=?
                WHERE id=?
                """,
                (
                    manifest["document_instance_id"], stored["snapshot_uuid"],
                    int(manifest["project_revision"]), current_revision, manifest["manifest_hash"],
                    manifest["structure_contract_hash"], stored["baseline_hash"],
                    job["source_docx_path"], package.source_sha256, job_id,
                ),
            )
            job = _job_row(db, job_id)
            diff = _build_diff(db, job, baseline, slot_values, ignored)
            _replace_conflicts(db, job_id, diff)
            conflicts = int(diff["summary"]["conflicts"])
            resolution = {} if conflicts else _automatic_resolution(diff)
            status = "conflicts_pending" if conflicts else "ready_to_commit"
            db.execute("DELETE FROM report_import_issues WHERE job_id = ?", (job_id,))
            if any(item["field_path"] == "document.media" for item in ignored):
                _issue(
                    db,
                    job_id,
                    code="ROUNDTRIP_MEDIA_CHANGE_IGNORED",
                    message="检测到图片变化；图片不会从 Word 回收，请在工具内维护。",
                    severity="warning",
                    phase="diff",
                    blocks=False,
                )
            if any(item["field_path"] == "document.comments" for item in ignored):
                _issue(
                    db,
                    job_id,
                    code="ROUNDTRIP_COMMENT_CHANGE_IGNORED",
                    message="检测到 Word 批注变化；批注不会作为业务数据回收。",
                    severity="warning",
                    phase="diff",
                    blocks=False,
                )
            db.execute(
                """
                UPDATE report_import_jobs
                SET status='preview_ready', roundtrip_status=?, diff_json=?, diff_hash=?,
                    resolution_json=?, resolution_hash=?, summary_json=?, error_code=NULL,
                    error_message=NULL, finished_at=?, job_revision=job_revision+1
                WHERE id=? AND roundtrip_status='validating'
                """,
                (
                    status, _json(diff), diff["diff_hash"], _json(resolution),
                    resolution.get("resolution_hash"), _json({"diff_summary": diff["summary"]}),
                    database.utc_now(), job_id,
                ),
            )
    except (OpcSecurityError, ManifestSecurityError, StructureSecurityError, RoundtripKeyError) as exc:
        code = str(getattr(exc, "code", type(exc).__name__))
        _mark_invalid(job_id, code, _security_message(code))
    except ReportDomainError as exc:
        _mark_invalid(job_id, exc.code, exc.message)
    except Exception as exc:  # noqa: BLE001 - redact document contents and paths
        _mark_invalid(
            job_id,
            "ROUNDTRIP_VALIDATION_FAILED",
            f"受控文档校验失败（{type(exc).__name__}）。",
        )


def _refresh_stale_locked(db: sqlite3.Connection, row: sqlite3.Row) -> sqlite3.Row:
    if str(row["roundtrip_status"]) not in REVIEW_STATUSES:
        return row
    current = _state_revision(db, int(row["project_id"]))
    if current == int(row["observed_project_revision"]):
        return row
    db.execute(
        """
        UPDATE report_import_jobs
        SET status='failed', roundtrip_status='stale', resolution_json='{}',
            resolution_hash=NULL, error_code='ROUNDTRIP_PROJECT_REVISION_STALE',
            error_message='项目数据已变化，请重新上传可回收草稿。',
            finished_at=?, job_revision=job_revision+1
        WHERE id=?
        """,
        (database.utc_now(), int(row["id"])),
    )
    return _job_row(db, int(row["id"]))


def _raise_persisted_stale(db: sqlite3.Connection) -> None:
    # ``database.connect`` rolls back when an exception escapes.  A stale
    # transition is itself authoritative task state, so commit that transition
    # before returning the 409 response.
    db.commit()
    raise ReportDomainError(
        "ROUNDTRIP_PROJECT_REVISION_STALE",
        "项目数据已变化，请重新上传可回收草稿。",
        status_code=409,
    )


def get_roundtrip_job(job_id: int) -> dict[str, Any]:
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _refresh_stale_locked(db, _job_row(db, job_id))
        return _job_result(db, row)


def get_roundtrip_issues(job_id: int) -> dict[str, Any]:
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        job = _refresh_stale_locked(db, _job_row(db, job_id))
        items: list[dict[str, Any]] = []
        for row in db.execute(
            "SELECT * FROM report_import_issues WHERE job_id=? ORDER BY id", (job_id,)
        ).fetchall():
            details = _load_json(row["candidate_value_json"], {})
            items.append(
                {
                    "severity": row["severity"],
                    "code": row["code"],
                    "message": details.get("message") or row["original_text"],
                    "blocks_progress": bool(row["blocks_confirmation"]),
                    "phase": details.get("phase") or row["source_locator"],
                    "field_id": row["authority_field_id"],
                    "field_path": row["field_path"] or None,
                    "row_id": details.get("row_id"),
                    "section_code": details.get("section_code"),
                    "object_name": details.get("object_name"),
                    "remediation": details.get("remediation"),
                    "three_way_summary": details.get("three_way_summary"),
                }
            )
        status = str(job["roundtrip_status"])
        return {
            "job_id": job_id,
            "status": status,
            "errors": [item for item in items if item["severity"] == "error"],
            "warnings": [item for item in items if item["severity"] == "warning"],
            "info": [item for item in items if item["severity"] == "info"],
        }


def _public_diff(diff: dict[str, Any], resolutions: dict[str, str]) -> dict[str, Any]:
    allowed = {
        "id", "conflict_id", "field_path", "field_label", "field_type", "section_code",
        "section_title", "entity_uuid", "object_name", "row_id", "base_value",
        "database_value", "word_value", "disposition", "resolution", "ignored_reason",
    }
    def clean(item: dict[str, Any]) -> dict[str, Any]:
        result = {key: item.get(key) for key in allowed}
        if item.get("conflict_id"):
            result["resolution"] = resolutions.get(str(item["conflict_id"]))
        return result
    items = [clean(item) for item in diff.get("items", [])]
    groups = [
        {
            "group_key": group["group_key"],
            "section_code": group.get("section_code"),
            "section_title": group.get("section_title"),
            "object_name": group.get("object_name"),
            "items": [clean(item) for item in group.get("items", [])],
        }
        for group in diff.get("groups", [])
    ]
    return {
        "job_id": int(diff["job_id"]),
        "status": diff.get("status"),
        "diff_hash": diff["diff_hash"],
        "base_project_revision": int(diff["base_project_revision"]),
        "observed_project_revision": int(diff["observed_project_revision"]),
        "summary": diff.get("summary") or {},
        "groups": groups,
        "items": items,
        "ignored_changes": [clean(item) for item in diff.get("ignored_changes", [])],
    }


def get_roundtrip_diff(job_id: int) -> dict[str, Any]:
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        job = _refresh_stale_locked(db, _job_row(db, job_id))
        if str(job["roundtrip_status"]) == "stale":
            _raise_persisted_stale(db)
        if str(job["roundtrip_status"]) not in REVIEW_STATUSES | {"succeeded"}:
            raise ReportDomainError(
                "ROUNDTRIP_DIFF_NOT_READY",
                "三方差异尚未生成。",
                status_code=409,
            )
        diff = _load_json(job["diff_json"], {})
        diff["status"] = str(job["roundtrip_status"])
        resolution = _load_json(job["resolution_json"], {})
        return _public_diff(diff, dict(resolution.get("resolutions") or {}))


def resolve_roundtrip_conflicts(
    job_id: int,
    payload: ReportRoundtripResolutionWrite,
) -> dict[str, Any]:
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        job = _refresh_stale_locked(db, _job_row(db, job_id))
        if str(job["roundtrip_status"]) == "stale":
            _raise_persisted_stale(db)
        if str(job["roundtrip_status"]) not in {"diff_ready", "conflicts_pending"}:
            raise ReportDomainError(
                "ROUNDTRIP_RESOLUTION_STATE_INVALID",
                "当前任务不能保存冲突决议。",
                status_code=409,
            )
        current_revision = _state_revision(db, int(job["project_id"]))
        if current_revision != int(job["observed_project_revision"]):
            _refresh_stale_locked(db, job)
            _raise_persisted_stale(db)
        if payload.expected_project_revision != current_revision:
            raise ReportDomainError(
                "ROUNDTRIP_EXPECTED_REVISION_MISMATCH",
                "请求中的项目版本不正确，请刷新任务后重试。",
                status_code=409,
            )
        if not hmac.compare_digest(payload.diff_hash, str(job["diff_hash"] or "")):
            raise ReportDomainError(
                "ROUNDTRIP_DIFF_HASH_MISMATCH",
                "差异摘要已变化，请刷新任务。",
                status_code=409,
            )
        expected = {
            str(row["conflict_id"])
            for row in db.execute(
                "SELECT conflict_id FROM report_sync_conflicts WHERE job_id=?", (job_id,)
            ).fetchall()
        }
        submitted = {item.conflict_id: item.action for item in payload.resolutions}
        if set(submitted) != expected:
            raise ReportDomainError(
                "ROUNDTRIP_RESOLUTION_SET_INCOMPLETE",
                "必须一次性处理全部冲突，且不能提交未知冲突。",
                status_code=422,
            )
        resolution = {
            "schema_version": "1.0",
            "diff_hash": payload.diff_hash,
            "expected_project_revision": current_revision,
            "resolutions": dict(sorted(submitted.items())),
        }
        resolution["resolution_hash"] = _hash(resolution)
        timestamp = database.utc_now()
        for conflict_id, action in submitted.items():
            db.execute(
                """
                UPDATE report_sync_conflicts
                SET resolution_action=?, updated_at=?
                WHERE job_id=? AND conflict_id=?
                """,
                (action, timestamp, job_id, conflict_id),
            )
        db.execute(
            """
            UPDATE report_import_jobs
            SET roundtrip_status='ready_to_commit', resolution_json=?, resolution_hash=?,
                job_revision=job_revision+1
            WHERE id=?
            """,
            (_json(resolution), resolution["resolution_hash"], job_id),
        )
        return {
            "job_id": job_id,
            "status": "ready_to_commit",
            "diff_hash": payload.diff_hash,
            "resolution_hash": resolution["resolution_hash"],
            "expected_project_revision": current_revision,
            "resolved_conflicts": len(submitted),
        }


def _write_value(
    db: sqlite3.Connection,
    project_id: int,
    item: dict[str, Any],
    value: str,
    timestamp: str,
) -> None:
    authority = str(item["authority_field_id"])
    kind = str(item["binding_kind"])
    key = str(item["binding_key"])
    column = str(item.get("column_id") or "")
    if kind == "scalar":
        if authority == "report.identity.number":
            db.execute("UPDATE report_metadata SET report_number=?,revision=revision+1,updated_at=? WHERE project_id=?", (value, timestamp, project_id))
        elif authority == "report.organization.assessed_name":
            cursor = db.execute(
                """
                UPDATE report_organizations SET name=?,revision=revision+1,updated_at=?
                WHERE id=(SELECT id FROM report_organizations WHERE project_id=? AND organization_type='assessed' AND active=1 ORDER BY sort_order,id LIMIT 1)
                """,
                (value, timestamp, project_id),
            )
            if cursor.rowcount != 1:
                raise ReportDomainError("ROUNDTRIP_DATABASE_FIELD_MISSING", "被测单位不存在。", status_code=409)
        elif authority == "report.system.name":
            db.execute("UPDATE system_profiles SET system_name=?,revision=revision+1,updated_at=? WHERE project_id=?", (value, timestamp, project_id))
        elif authority == "report.system.overview":
            db.execute("UPDATE system_profiles SET system_summary=?,revision=revision+1,updated_at=? WHERE project_id=?", (value, timestamp, project_id))
        elif authority.startswith("report.distribution."):
            db_column = {
                "report.distribution.regulator_copies": "regulator_copies",
                "report.distribution.client_copies": "client_copies",
                "report.distribution.assessment_copies": "assessment_organization_copies",
            }.get(authority)
            if db_column is None:
                raise ReportDomainError("ROUNDTRIP_FIELD_NOT_WRITABLE", "字段不在回收白名单内。", status_code=422)
            db.execute(
                f"UPDATE report_distribution SET {db_column}=?,revision=revision+1,updated_at=? WHERE project_id=?",
                (int(value), timestamp, project_id),
            )
        else:
            raise ReportDomainError("ROUNDTRIP_FIELD_NOT_WRITABLE", "字段不在回收白名单内。", status_code=422)
        return
    if kind == "object_name":
        cursor = db.execute(
            """
            UPDATE assessment_objects SET name_snapshot=?,revision=revision+1,updated_at=?
            WHERE project_id=? AND object_uuid=? AND active=1
            """,
            (value, timestamp, project_id, key),
        )
        if cursor.rowcount != 1:
            raise ReportDomainError("ROUNDTRIP_DATABASE_OBJECT_MISSING", "测评对象已不存在。", status_code=409)
        db.execute(
            """
            UPDATE assessment_rows SET object_name=?,updated_at=?
            WHERE assessment_object_uuid=? AND section_id IN
                  (SELECT id FROM appendix_sections WHERE project_id=?)
            """,
            (value, timestamp, key, project_id),
        )
        return
    if kind == "assessment_row":
        row = _row_for_slot(db, project_id, key)
        row_id = int(row["id"])
        if column == "record_text":
            db.execute(
                "UPDATE assessment_rows SET record_text=?,updated_at=? WHERE id=?",
                (value, timestamp, row_id),
            )
            return
        if column in {"d", "a", "k", "compliance"}:
            db.execute(f"UPDATE metric_results SET {column}=? WHERE row_id=?", (value, row_id))
            return
    raise ReportDomainError("ROUNDTRIP_FIELD_NOT_WRITABLE", "字段不在回收白名单内。", status_code=422)


def _resolved_action(item: dict[str, Any], resolutions: dict[str, str]) -> str:
    disposition = str(item["disposition"])
    if disposition == "apply_word":
        return "apply_word"
    if disposition == "conflict":
        return str(resolutions.get(str(item["conflict_id"])) or "")
    return "keep_database"


def _audit_snapshot(items: Iterable[dict[str, Any]], *, value_key: str) -> str:
    return _hash(
        [
            {"id": item["id"], "field_path": item["field_path"], "value": item[value_key]}
            for item in sorted(items, key=lambda candidate: str(candidate["id"]))
        ]
    )


def _committed_result(db: sqlite3.Connection, job: sqlite3.Row) -> dict[str, Any]:
    audit = db.execute("SELECT * FROM report_import_audits WHERE job_id=?", (int(job["id"]),)).fetchone()
    if audit is None:
        raise ReportDomainError("ROUNDTRIP_AUDIT_MISSING", "回写审计记录缺失。", status_code=500)
    changed = _load_json(audit["changed_fields_json"], [])
    diff = _load_json(job["diff_json"], {})
    resolution = _load_json(job["resolution_json"], {})
    resolutions = dict(resolution.get("resolutions") or {})
    kept = sum(_resolved_action(item, resolutions) != "apply_word" for item in diff.get("items", []))
    return {
        "job_id": int(job["id"]),
        "status": "succeeded",
        "project_uuid": str(job["project_uuid"]),
        "before_revision": int(audit["base_project_revision"]),
        "after_revision": int(audit["committed_project_revision"]),
        "resolution_hash": str(job["resolution_hash"]),
        "applied_fields": len(changed),
        "kept_fields": kept,
        "ignored_changes": len(diff.get("ignored_changes", [])),
        "error_code": None,
        "error_message": None,
    }


def commit_roundtrip_job(
    job_id: int,
    payload: ReportRoundtripCommitWrite,
) -> dict[str, Any]:
    commit_started = False
    try:
        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = _job_row(db, job_id)
            if str(job["roundtrip_status"]) == "succeeded":
                if not hmac.compare_digest(payload.resolution_hash, str(job["resolution_hash"] or "")):
                    raise ReportDomainError("ROUNDTRIP_RESOLUTION_HASH_MISMATCH", "冲突决议摘要不一致。", status_code=409)
                return _committed_result(db, job)
            job = _refresh_stale_locked(db, job)
            if str(job["roundtrip_status"]) == "stale":
                raise ReportDomainError(
                    "ROUNDTRIP_PROJECT_REVISION_STALE",
                    "项目数据已变化，请重新上传可回收草稿。",
                    status_code=409,
                )
            if str(job["roundtrip_status"]) != "ready_to_commit":
                raise ReportDomainError("ROUNDTRIP_COMMIT_STATE_INVALID", "当前任务不能提交回写。", status_code=409)
            current_revision = _state_revision(db, int(job["project_id"]))
            if current_revision != int(job["observed_project_revision"]):
                raise ReportDomainError(
                    "ROUNDTRIP_PROJECT_REVISION_STALE",
                    "项目数据已变化，请重新上传可回收草稿。",
                    status_code=409,
                )
            if payload.expected_project_revision != current_revision:
                raise ReportDomainError(
                    "ROUNDTRIP_EXPECTED_REVISION_MISMATCH",
                    "请求中的项目版本不正确，请刷新任务后重试。",
                    status_code=409,
                )
            if not hmac.compare_digest(payload.resolution_hash, str(job["resolution_hash"] or "")):
                raise ReportDomainError(
                    "ROUNDTRIP_RESOLUTION_HASH_MISMATCH",
                    "冲突决议摘要已变化，请刷新任务。",
                    status_code=409,
                )
            diff = _load_json(job["diff_json"], {})
            resolution = _load_json(job["resolution_json"], {})
            if (
                not hmac.compare_digest(str(resolution.get("diff_hash") or ""), str(job["diff_hash"] or ""))
                or int(resolution.get("expected_project_revision") or 0) != current_revision
                or not hmac.compare_digest(_hash({k: v for k, v in resolution.items() if k != "resolution_hash"}), payload.resolution_hash)
            ):
                raise ReportDomainError(
                    "ROUNDTRIP_RESOLUTION_CONTRACT_INVALID",
                    "冲突决议契约无效，请重新生成差异。",
                    status_code=409,
                )
            resolutions = dict(resolution.get("resolutions") or {})
            project_id = int(job["project_id"])
            # Defence in depth: every database value observed during diff must
            # still match even if an older writer failed to advance revision.
            for item in diff.get("items", []):
                slot = {
                    "binding_kind": item["binding_kind"],
                    "binding_key": item["binding_key"],
                    "authority_field_id": item["authority_field_id"],
                    "column_id": item.get("column_id"),
                    "normalizer_id": item["normalizer_id"],
                    "options": item.get("options") or [],
                }
                current = _coerce_value(slot, _database_value(db, project_id, slot))
                if current != item["database_value"]:
                    raise ReportDomainError(
                        "ROUNDTRIP_DATABASE_VALUE_STALE",
                        "项目字段在生成差异后已变化，请重新上传草稿。",
                        status_code=409,
                    )
            before_hash = _audit_snapshot(diff.get("items", []), value_key="database_value")
            timestamp = database.utc_now()
            commit_started = True
            db.execute(
                "UPDATE report_import_jobs SET roundtrip_status='committing' WHERE id=?",
                (job_id,),
            )
            changed: list[dict[str, Any]] = []
            for item in diff.get("items", []):
                if _resolved_action(item, resolutions) != "apply_word":
                    continue
                slot = {
                    "binding_kind": item["binding_kind"],
                    "binding_key": item["binding_key"],
                    "authority_field_id": item["authority_field_id"],
                    "column_id": item.get("column_id"),
                    "normalizer_id": item["normalizer_id"],
                    "options": item.get("options") or [],
                }
                value = _coerce_value(slot, item["word_value"])
                _write_value(db, project_id, item, value, timestamp)
                changed.append(
                    {
                        "id": item["id"],
                        "field_path": item["field_path"],
                        "before_hash": hashlib.sha256(str(item["database_value"]).encode("utf-8")).hexdigest(),
                        "after_hash": hashlib.sha256(value.encode("utf-8")).hexdigest(),
                    }
                )
            try:
                recalculate_appendix_scores_locked(db, project_id, strict=True)
            except AppendixScoringError as exc:
                raise ReportDomainError(
                    "ROUNDTRIP_SCORE_RECALCULATION_FAILED",
                    "Word 修改导致附录 A 无法按权威规则重算，项目数据未写入。",
                    status_code=422,
                    details={"reason": str(exc)},
                ) from exc
            run = regenerate_after_roundtrip_locked(
                db, str(job["project_uuid"]), current_revision
            )
            after_revision = int(run["project_revision"])
            db.execute(
                "UPDATE projects SET workflow_status='draft', updated_at=? WHERE id=?",
                (timestamp, project_id),
            )
            # Build the post-write snapshot from the actual database, not the
            # requested Word values, before recording the immutable audit.
            after_items: list[dict[str, Any]] = []
            for item in diff.get("items", []):
                slot = {
                    "binding_kind": item["binding_kind"],
                    "binding_key": item["binding_key"],
                    "authority_field_id": item["authority_field_id"],
                    "column_id": item.get("column_id"),
                    "normalizer_id": item["normalizer_id"],
                    "options": item.get("options") or [],
                }
                after_items.append({**item, "after_value": _coerce_value(slot, _database_value(db, project_id, slot))})
            after_hash = _audit_snapshot(after_items, value_key="after_value")
            audit_uuid = str(uuid.uuid4())
            db.execute(
                """
                INSERT INTO report_import_audits (
                    audit_uuid, job_id, project_id, document_instance_id,
                    source_sha256, manifest_hash, diff_hash, resolution_hash,
                    before_hash, after_hash, changed_fields_json,
                    base_project_revision, committed_project_revision, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_uuid, job_id, project_id, job["document_instance_id"],
                    job["source_sha256"], job["manifest_hash"], job["diff_hash"],
                    payload.resolution_hash, before_hash, after_hash, _json(changed),
                    current_revision, after_revision, timestamp,
                ),
            )
            summary = _load_json(job["summary_json"], {})
            summary.update(
                {
                    "committed_at": timestamp,
                    "before_revision": current_revision,
                    "after_revision": after_revision,
                    "applied_fields": len(changed),
                }
            )
            db.execute(
                """
                UPDATE report_import_jobs
                SET status='succeeded', roundtrip_status='succeeded', summary_json=?,
                    error_code=NULL, error_message=NULL, finished_at=?,
                    job_revision=job_revision+1
                WHERE id=?
                """,
                (_json(summary), timestamp, job_id),
            )
            return _committed_result(db, _job_row(db, job_id))
    except ReportDomainError as exc:
        stale = exc.code in {
            "ROUNDTRIP_PROJECT_REVISION_STALE", "ROUNDTRIP_DATABASE_VALUE_STALE"
        }
        if stale or commit_started:
            status = "stale" if stale else "failed"
            with database.connect() as recovery:
                recovery.execute("BEGIN IMMEDIATE")
                row = recovery.execute(
                    "SELECT roundtrip_status FROM report_import_jobs WHERE id=? AND mode='roundtrip'",
                    (job_id,),
                ).fetchone()
                if row is not None and row["roundtrip_status"] != "succeeded":
                    recovery.execute(
                        """
                        UPDATE report_import_jobs
                        SET status='failed', roundtrip_status=?, error_code=?, error_message=?,
                            finished_at=?, job_revision=job_revision+1
                        WHERE id=?
                        """,
                        (status, exc.code, exc.message, database.utc_now(), job_id),
                    )
                    _issue(
                        recovery,
                        job_id,
                        code=exc.code,
                        message=exc.message,
                        phase="commit",
                    )
        raise
