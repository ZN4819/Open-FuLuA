from __future__ import annotations

import hashlib
import logging
import re
import shutil
import sqlite3
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .. import database
from ..config import settings
from ..contracts import (
    FULL_REPORT_TEMPLATE_EDITION,
    FULL_REPORT_TEMPLATE_PACKAGE_ID,
    FULL_REPORT_TEMPLATE_REVISION,
)
from .report_templates.registry import ReportTemplateUnavailable, report_template_registry


logger = logging.getLogger(__name__)
UPGRADE_LEASE_TIMEOUT = timedelta(minutes=30)
UPGRADE_PROJECT_UUID_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL,
    "https://github.com/ZN4819/Open-FuLuA/project-upgrades",
)


class ProjectServiceError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        project_uuid: str | None = None,
        field: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.project_uuid = project_uuid
        self.field = field
        self.details = details or {}

    def detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "project_uuid": self.project_uuid,
            "field": self.field,
            "details": self.details,
        }


@dataclass(frozen=True)
class TemplateBinding:
    package_id: str
    edition: str
    revision: str
    asset_set_hash: str


def create_typed_project(
    name: str,
    *,
    project_type: str,
    template_package_id: str | None = None,
    template_edition: str | None = None,
    template_revision: str | None = None,
) -> sqlite3.Row:
    normalized_name = name.strip()
    if not normalized_name:
        raise ProjectServiceError(
            "PROJECT_NAME_REQUIRED",
            "项目名称不能为空。",
            status_code=422,
            field="name",
        )
    if project_type == "appendix_a":
        if any((template_package_id, template_edition, template_revision)):
            raise ProjectServiceError(
                "APPENDIX_A_TEMPLATE_BINDING_FORBIDDEN",
                "仅附录 A 项目不能绑定完整报告模板。",
                status_code=422,
                field="template_package_id",
            )
        return database.create_project(normalized_name, project_type="appendix_a")
    if project_type != "full_report":
        raise ProjectServiceError(
            "PROJECT_TYPE_INVALID",
            "项目类型不受支持。",
            status_code=422,
            field="project_type",
        )

    binding = _trusted_template_binding(
        template_package_id,
        template_edition,
        template_revision,
    )
    return database.create_project(
        normalized_name,
        project_type="full_report",
        workflow_status="draft",
        template_package_id=binding.package_id,
        template_edition=binding.edition,
        template_revision=binding.revision,
        template_asset_set_hash=binding.asset_set_hash,
    )


def upgrade_project_copy(
    source_project_uuid: str,
    *,
    name: str,
    template_package_id: str,
    template_edition: str,
    template_revision: str,
    idempotency_key: str,
) -> sqlite3.Row:
    normalized_name = name.strip()
    if not normalized_name:
        raise ProjectServiceError(
            "PROJECT_NAME_REQUIRED",
            "项目名称不能为空。",
            status_code=422,
            project_uuid=source_project_uuid,
            field="name",
        )
    binding = _trusted_template_binding(
        template_package_id,
        template_edition,
        template_revision,
    )
    try:
        normalized_source_uuid = str(uuid.UUID(source_project_uuid))
        normalized_idempotency_key = str(uuid.UUID(idempotency_key))
    except ValueError as exc:
        raise ProjectServiceError(
            "PROJECT_UUID_INVALID",
            "项目标识或幂等键格式无效。",
            status_code=422,
            project_uuid=source_project_uuid,
        ) from exc

    target_project_id: int | None = None
    target_project_uuid = _upgrade_target_uuid(
        normalized_source_uuid,
        normalized_idempotency_key,
    )
    lease_id: str | None = None
    operation_started = False
    target_files_may_exist = False
    staging_dir: Path | None = None
    try:
        existing_target, lease_id = _begin_upgrade_operation(
            normalized_source_uuid,
            normalized_idempotency_key,
        )
        if existing_target is not None:
            return existing_target
        operation_started = True
        staging_dir = _project_upgrade_staging_dir(
            normalized_source_uuid,
            normalized_idempotency_key,
            lease_id,
        )
        with database.connect() as db:
            source = database.get_project_by_uuid(normalized_source_uuid, db)
            if source is None or source["project_type"] != "appendix_a":
                raise ProjectServiceError(
                    "PROJECT_NOT_FOUND",
                    "源项目不存在或已不可升级。",
                    status_code=404,
                    project_uuid=normalized_source_uuid,
                )
            source_project_id = int(source["id"])
            source_images = db.execute(
                """
                SELECT * FROM evidence_images
                WHERE project_id = ?
                ORDER BY section_code, sort_order, id
                """,
                (source_project_id,),
            ).fetchall()
        staged_files = _stage_evidence_files(source_images, staging_dir)
        _touch_upgrade_operation(
            normalized_source_uuid,
            normalized_idempotency_key,
            lease_id,
        )

        with database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            source = database.get_project_by_uuid(normalized_source_uuid, db)
            if source is None:
                raise ProjectServiceError(
                    "PROJECT_NOT_FOUND",
                    "源项目不存在。",
                    status_code=404,
                    project_uuid=normalized_source_uuid,
                )
            if source["project_type"] != "appendix_a":
                raise ProjectServiceError(
                    "PROJECT_TYPE_NOT_UPGRADABLE",
                    "只有附录 A 项目可以复制为完整报告。",
                    project_uuid=normalized_source_uuid,
                )

            current_source_images = db.execute(
                """
                SELECT * FROM evidence_images
                WHERE project_id = ?
                ORDER BY section_code, sort_order, id
                """,
                (int(source["id"]),),
            ).fetchall()
            if _image_record_signature(current_source_images) != _image_record_signature(source_images):
                raise ProjectServiceError(
                    "SOURCE_PROJECT_CHANGED",
                    "复制图片期间源项目发生变化，请重新发起升级。",
                    project_uuid=normalized_source_uuid,
                )

            operation = db.execute(
                """
                SELECT * FROM project_upgrade_operations
                WHERE source_project_uuid = ? AND idempotency_key = ?
                """,
                (normalized_source_uuid, normalized_idempotency_key),
            ).fetchone()
            if (
                operation is None
                or operation["status"] != "pending"
                or operation["lease_id"] != lease_id
            ):
                raise ProjectServiceError(
                    "UPGRADE_OPERATION_STATE_INVALID",
                    "升级操作状态已变化，请刷新项目列表。",
                    project_uuid=normalized_source_uuid,
                )
            timestamp = database.utc_now()

            target = database._insert_project(
                db,
                name=normalized_name,
                project_type="full_report",
                workflow_status="draft",
                template_package_id=binding.package_id,
                template_edition=binding.edition,
                template_revision=binding.revision,
                template_asset_set_hash=binding.asset_set_hash,
                source_project_uuid=normalized_source_uuid,
                created_by_operation="upgrade_copy",
                project_uuid=target_project_uuid,
            )
            target_project_id = int(target["id"])
            db.execute(
                """
                UPDATE project_upgrade_operations
                SET target_project_id = ?, updated_at = ?
                WHERE source_project_uuid = ? AND idempotency_key = ?
                """,
                (
                    target_project_id,
                    timestamp,
                    normalized_source_uuid,
                    normalized_idempotency_key,
                ),
            )

            target_files_may_exist = True
            _clone_appendix_a_domain(
                db,
                source_project_id=int(source["id"]),
                target_project_id=target_project_id,
                source_images=current_source_images,
                staged_files=staged_files,
                timestamp=timestamp,
                target_project_uuid=target_project_uuid,
            )
            db.execute(
                """
                UPDATE project_upgrade_operations
                SET status = 'completed', lease_id = NULL, error_code = NULL, updated_at = ?
                WHERE source_project_uuid = ? AND idempotency_key = ?
                """,
                (timestamp, normalized_source_uuid, normalized_idempotency_key),
            )
            completed = database.get_project_by_id(target_project_id, db)
            if completed is None:
                raise RuntimeError("UPGRADE_TARGET_NOT_FOUND")
            return completed
    except ProjectServiceError as exc:
        cleanup_failed = _cleanup_upgrade_paths(
            target_project_uuid if target_files_may_exist else None,
            staging_dir,
        )
        if operation_started and exc.code not in {"UPGRADE_OPERATION_IN_PROGRESS", "IDEMPOTENT_RESULT_GONE"}:
            _record_upgrade_failure(
                normalized_source_uuid,
                normalized_idempotency_key,
                exc.code,
                cleanup_failed=cleanup_failed,
            )
        raise
    except Exception as exc:
        cleanup_failed = _cleanup_upgrade_paths(
            target_project_uuid if target_files_may_exist else None,
            staging_dir,
        )
        if operation_started:
            _record_upgrade_failure(
                normalized_source_uuid,
                normalized_idempotency_key,
                "UPGRADE_COPY_FAILED",
                cleanup_failed=cleanup_failed,
            )
        logger.exception("附录 A 项目复制升级失败，source=%s", normalized_source_uuid)
        raise ProjectServiceError(
            "UPGRADE_COPY_FAILED",
            "复制升级失败，源项目未被修改。",
            project_uuid=normalized_source_uuid,
            details={"cleanup_failed": cleanup_failed},
        ) from exc
    finally:
        if staging_dir is not None and staging_dir.exists():
            try:
                _safe_remove_tree(staging_dir, settings.runtime_paths.migration_path)
            except Exception:
                logger.exception("最终清理项目复制升级暂存目录失败")


def _begin_upgrade_operation(
    source_project_uuid: str,
    idempotency_key: str,
) -> tuple[sqlite3.Row | None, str | None]:
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        source = database.get_project_by_uuid(source_project_uuid, db)
        if source is None:
            raise ProjectServiceError(
                "PROJECT_NOT_FOUND",
                "源项目不存在。",
                status_code=404,
                project_uuid=source_project_uuid,
            )
        if source["project_type"] != "appendix_a":
            raise ProjectServiceError(
                "PROJECT_TYPE_NOT_UPGRADABLE",
                "只有附录 A 项目可以复制为完整报告。",
                project_uuid=source_project_uuid,
            )
        operation = db.execute(
            """
            SELECT * FROM project_upgrade_operations
            WHERE source_project_uuid = ? AND idempotency_key = ?
            """,
            (source_project_uuid, idempotency_key),
        ).fetchone()
        if operation is not None and operation["status"] == "completed":
            if operation["target_project_id"] is None:
                raise ProjectServiceError(
                    "IDEMPOTENT_RESULT_GONE",
                    "该升级请求曾经成功，但目标项目已经被删除。",
                    project_uuid=source_project_uuid,
                )
            existing_target = database.get_project_by_id(int(operation["target_project_id"]), db)
            if existing_target is None:
                raise ProjectServiceError(
                    "IDEMPOTENT_RESULT_GONE",
                    "该升级请求曾经成功，但目标项目已经被删除。",
                    project_uuid=source_project_uuid,
                )
            return existing_target, None
        if operation is not None and operation["status"] in {"pending", "failed_cleanup"}:
            if operation["status"] == "pending" and not _upgrade_operation_is_stale(operation):
                raise ProjectServiceError(
                    "UPGRADE_OPERATION_IN_PROGRESS",
                    "该升级请求仍在处理，请稍后重试。",
                    project_uuid=source_project_uuid,
                )
            if not _recover_upgrade_operation_locked(
                db,
                operation,
                source_project_uuid=source_project_uuid,
                idempotency_key=idempotency_key,
            ):
                raise ProjectServiceError(
                    "UPGRADE_OPERATION_RECOVERY_FAILED",
                    "上次中断的升级操作未能安全清理，请查看日志后重试。",
                    project_uuid=source_project_uuid,
                )
        timestamp = database.utc_now()
        lease_id = str(uuid.uuid4())
        if operation is None:
            db.execute(
                """
                INSERT INTO project_upgrade_operations (
                    source_project_uuid, idempotency_key, target_project_id,
                    status, lease_id, error_code, created_at, updated_at
                )
                VALUES (?, ?, NULL, 'pending', ?, NULL, ?, ?)
                """,
                (source_project_uuid, idempotency_key, lease_id, timestamp, timestamp),
            )
        else:
            db.execute(
                """
                UPDATE project_upgrade_operations
                SET target_project_id = NULL, status = 'pending', lease_id = ?,
                    error_code = NULL, updated_at = ?
                WHERE id = ?
                """,
                (lease_id, timestamp, int(operation["id"])),
            )
    return None, lease_id


def _touch_upgrade_operation(
    source_project_uuid: str,
    idempotency_key: str,
    lease_id: str,
) -> None:
    with database.connect() as db:
        cursor = db.execute(
            """
            UPDATE project_upgrade_operations
            SET updated_at = ?
            WHERE source_project_uuid = ? AND idempotency_key = ?
              AND status = 'pending' AND lease_id = ?
            """,
            (database.utc_now(), source_project_uuid, idempotency_key, lease_id),
        )
        if cursor.rowcount != 1:
            raise ProjectServiceError(
                "UPGRADE_OPERATION_LEASE_LOST",
                "升级操作所有权已失效，请重新发起升级。",
                project_uuid=source_project_uuid,
            )


def recover_abandoned_upgrade_operations() -> int:
    """进程启动时回收不可能仍由当前进程持有的复制升级租约。"""
    recovered = 0
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        operations = db.execute(
            """
            SELECT * FROM project_upgrade_operations
            WHERE status IN ('pending', 'failed_cleanup')
            ORDER BY id
            """
        ).fetchall()
        for operation in operations:
            if _recover_upgrade_operation_locked(
                db,
                operation,
                source_project_uuid=str(operation["source_project_uuid"]),
                idempotency_key=str(operation["idempotency_key"]),
            ):
                recovered += 1
    return recovered


def _upgrade_operation_is_stale(operation: sqlite3.Row) -> bool:
    lease_id = str(operation["lease_id"] or "").strip()
    if not lease_id:
        return True
    try:
        updated_at = datetime.fromisoformat(str(operation["updated_at"]))
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True
    return datetime.now(timezone.utc) - updated_at.astimezone(timezone.utc) >= UPGRADE_LEASE_TIMEOUT


def _recover_upgrade_operation_locked(
    db: sqlite3.Connection,
    operation: sqlite3.Row,
    *,
    source_project_uuid: str,
    idempotency_key: str,
) -> bool:
    target_project_uuid = _upgrade_target_uuid(source_project_uuid, idempotency_key)
    target = database.get_project_by_uuid(target_project_uuid, db)
    if target is not None:
        owned = (
            target["project_type"] == "full_report"
            and target["created_by_operation"] == "upgrade_copy"
            and target["source_project_uuid"] == source_project_uuid
            and operation["target_project_id"] == target["id"]
        )
        if not owned:
            _mark_upgrade_cleanup_failed_locked(db, operation)
            return False

    cleanup_failed = _cleanup_upgrade_paths(
        target_project_uuid,
        _project_upgrade_operation_dir(source_project_uuid, idempotency_key),
    )
    if cleanup_failed:
        _mark_upgrade_cleanup_failed_locked(db, operation)
        return False
    if target is not None:
        database.delete_project(int(target["id"]), db)
    db.execute(
        """
        UPDATE project_upgrade_operations
        SET target_project_id = NULL, status = 'failed', lease_id = NULL,
            error_code = 'UPGRADE_OPERATION_ABANDONED', updated_at = ?
        WHERE id = ?
        """,
        (database.utc_now(), int(operation["id"])),
    )
    return True


def _mark_upgrade_cleanup_failed_locked(
    db: sqlite3.Connection,
    operation: sqlite3.Row,
) -> None:
    db.execute(
        """
        UPDATE project_upgrade_operations
        SET status = 'failed_cleanup', lease_id = NULL,
            error_code = 'UPGRADE_CLEANUP_FAILED', updated_at = ?
        WHERE id = ?
        """,
        (database.utc_now(), int(operation["id"])),
    )


def transition_workflow(project_uuid: str, action: str) -> sqlite3.Row:
    project = database.get_project_by_uuid(project_uuid)
    if project is None:
        raise ProjectServiceError(
            "PROJECT_NOT_FOUND",
            "项目不存在。",
            status_code=404,
            project_uuid=project_uuid,
        )
    if action in {"ready-for-review", "confirm"}:
        raise ProjectServiceError(
            "REPORT_VALIDATION_NOT_AVAILABLE",
            "完整报告校验将在下一阶段提供，当前项目只能保持草稿状态。",
            project_uuid=project_uuid,
        )
    if action != "reopen":
        raise ProjectServiceError(
            "WORKFLOW_ACTION_INVALID",
            "工作流操作不受支持。",
            status_code=422,
            project_uuid=project_uuid,
        )
    updated = database.update_project_workflow(project_uuid, "draft")
    if updated is None:
        raise ProjectServiceError(
            "PROJECT_NOT_FOUND",
            "项目不存在。",
            status_code=404,
            project_uuid=project_uuid,
        )
    return updated


def remove_project_runtime_files(
    project_id: int,
    project_uuid: str | None = None,
) -> None:
    project_keys = {str(project_id)} if project_id >= 0 else set()
    if project_uuid:
        project_keys.add(project_uuid)
    for relative_path in tuple(
        Path(category) / key
        for category in ("uploads", "exports", "previews", "projects")
        for key in project_keys
    ):
        _remove_storage_child(relative_path)


def _trusted_template_binding(
    package_id: str | None,
    edition: str | None,
    revision: str | None,
) -> TemplateBinding:
    if (
        package_id != FULL_REPORT_TEMPLATE_PACKAGE_ID
        or edition != FULL_REPORT_TEMPLATE_EDITION
        or revision != FULL_REPORT_TEMPLATE_REVISION
    ):
        raise ProjectServiceError(
            "UNSUPPORTED_TEMPLATE_IDENTITY",
            "完整报告模板身份不受支持。",
            status_code=422,
            field="template_package_id",
            details={
                "expected_package_id": FULL_REPORT_TEMPLATE_PACKAGE_ID,
                "expected_edition": FULL_REPORT_TEMPLATE_EDITION,
                "expected_revision": FULL_REPORT_TEMPLATE_REVISION,
            },
        )
    try:
        package = report_template_registry.load(force=True)
    except ReportTemplateUnavailable as exc:
        raise ProjectServiceError(
            "TEMPLATE_PACKAGE_UNTRUSTED",
            "完整报告模板包未通过完整性校验。",
            status_code=503,
            field="template_package_id",
            details={"template_error_code": exc.code, "asset": exc.asset},
        ) from exc
    return TemplateBinding(
        package_id=package.package_id,
        edition=package.template_edition,
        revision=package.template_revision,
        asset_set_hash=package.asset_set_hash,
    )


def _clone_appendix_a_domain(
    db: sqlite3.Connection,
    *,
    source_project_id: int,
    target_project_id: int,
    source_images: list[sqlite3.Row],
    staged_files: dict[int, Path],
    timestamp: str,
    target_project_uuid: str,
) -> None:
    source_sections = db.execute(
        "SELECT * FROM appendix_sections WHERE project_id = ? ORDER BY sort_order, id",
        (source_project_id,),
    ).fetchall()
    expected_codes = {item[0] for item in database.SECTION_SEED}
    if {str(row["code"]) for row in source_sections} != expected_codes:
        raise ProjectServiceError(
            "SOURCE_APPENDIX_STRUCTURE_INVALID",
            "源项目的附录 A 章节结构不完整。",
        )
    target_sections = {
        str(row["code"]): row
        for row in db.execute(
            "SELECT * FROM appendix_sections WHERE project_id = ?",
            (target_project_id,),
        ).fetchall()
    }
    section_id_map: dict[int, int] = {}
    for source_section in source_sections:
        code = str(source_section["code"])
        target_section = target_sections[code]
        target_section_id = int(target_section["id"])
        section_id_map[int(source_section["id"])] = target_section_id
        db.execute(
            """
            UPDATE appendix_sections
            SET title = ?, table_title = ?, sort_order = ?
            WHERE id = ?
            """,
            (
                source_section["title"],
                source_section["table_title"],
                source_section["sort_order"],
                target_section_id,
            ),
        )

    for subsystem in db.execute(
        """
        SELECT * FROM section_subsystems
        WHERE project_id = ?
        ORDER BY section_code, sort_order, id
        """,
        (source_project_id,),
    ).fetchall():
        db.execute(
            """
            INSERT INTO section_subsystems (
                project_id, section_code, name, sort_order, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                target_project_id,
                subsystem["section_code"],
                subsystem["name"],
                subsystem["sort_order"],
                timestamp,
                timestamp,
            ),
        )

    image_id_map: dict[int, int] = {}
    for image in source_images:
        source_image_id = int(image["id"])
        target_relative_path = _publish_staged_image(
            staged_files[source_image_id],
            target_project_uuid,
            str(image["section_code"]),
        )
        cursor = db.execute(
            """
            INSERT INTO evidence_images (
                project_id,
                section_code,
                file_path,
                original_name,
                caption,
                alt_text,
                sort_order,
                pixel_width,
                pixel_height,
                dpi_x,
                dpi_y,
                display_width_in,
                display_height_in,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                target_project_id,
                image["section_code"],
                target_relative_path,
                image["original_name"],
                image["caption"],
                image["alt_text"],
                image["sort_order"],
                image["pixel_width"],
                image["pixel_height"],
                image["dpi_x"],
                image["dpi_y"],
                image["display_width_in"],
                image["display_height_in"],
                timestamp,
                timestamp,
            ),
        )
        image_id_map[source_image_id] = int(cursor.lastrowid)

    row_id_map: dict[int, int] = {}
    for source_section in source_sections:
        source_rows = db.execute(
            """
            SELECT r.*, m.id AS metric_id, m.d, m.a, m.k, m.ra, m.rk,
                   m.object_score, m.unit_score, m.compliance
            FROM assessment_rows r
            LEFT JOIN metric_results m ON m.row_id = r.id
            WHERE r.section_id = ?
            ORDER BY r.sort_order, r.id
            """,
            (int(source_section["id"]),),
        ).fetchall()
        for row in source_rows:
            record_text = _remap_figure_tokens(str(row["record_text"] or ""), image_id_map)
            cursor = db.execute(
                """
                INSERT INTO assessment_rows (
                    section_id, unit, object_name, subsystem, record_text,
                    sort_order, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    section_id_map[int(source_section["id"])],
                    row["unit"],
                    row["object_name"],
                    row["subsystem"],
                    record_text,
                    row["sort_order"],
                    timestamp,
                    timestamp,
                ),
            )
            target_row_id = int(cursor.lastrowid)
            row_id_map[int(row["id"])] = target_row_id
            if row["metric_id"] is not None:
                db.execute(
                    """
                    INSERT INTO metric_results (
                        row_id, d, a, k, ra, rk, object_score, unit_score, compliance
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        target_row_id,
                        row["d"],
                        row["a"],
                        row["k"],
                        row["ra"],
                        row["rk"],
                        row["object_score"],
                        row["unit_score"],
                        row["compliance"],
                    ),
                )

    references = db.execute(
        """
        SELECT c.*
        FROM cross_references c
        JOIN assessment_rows r ON r.id = c.source_row_id
        JOIN appendix_sections s ON s.id = r.section_id
        WHERE s.project_id = ?
        ORDER BY c.id
        """,
        (source_project_id,),
    ).fetchall()
    for reference in references:
        target_image_id = None
        if reference["target_image_id"] is not None:
            source_image_id = int(reference["target_image_id"])
            if source_image_id not in image_id_map:
                raise ProjectServiceError(
                    "SOURCE_REFERENCE_TARGET_INVALID",
                    "源项目存在无法映射的图片引用。",
                )
            target_image_id = image_id_map[source_image_id]
        db.execute(
            """
            INSERT INTO cross_references (
                source_row_id, target_image_id, token, display_text
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                row_id_map[int(reference["source_row_id"])],
                target_image_id,
                _remap_figure_tokens(str(reference["token"] or ""), image_id_map),
                reference["display_text"],
            ),
        )


def _stage_evidence_files(images: list[sqlite3.Row], staging_dir: Path) -> dict[int, Path]:
    if staging_dir.exists():
        _safe_remove_tree(staging_dir, settings.runtime_paths.migration_path)
    staging_dir.mkdir(parents=True, exist_ok=False)
    resolved_sources: list[tuple[sqlite3.Row, Path]] = []
    total_size = 0
    for image in images:
        source = _resolve_storage_source(str(image["file_path"] or ""))
        total_size += source.stat().st_size
        resolved_sources.append((image, source))
    if shutil.disk_usage(staging_dir.parent).free < total_size:
        raise ProjectServiceError(
            "UPGRADE_DISK_SPACE_INSUFFICIENT",
            "可用磁盘空间不足，无法复制项目图片。",
        )

    staged: dict[int, Path] = {}
    for image, source in resolved_sources:
        source_id = int(image["id"])
        destination = staging_dir / f"{source_id}{source.suffix or '.png'}"
        source_hash = _sha256_file(source)
        shutil.copy2(source, destination)
        if _sha256_file(destination) != source_hash:
            raise ProjectServiceError(
                "UPGRADE_FILE_HASH_MISMATCH",
                "复制项目图片时完整性校验失败。",
            )
        staged[source_id] = destination
    return staged


def _image_record_signature(images: list[sqlite3.Row]) -> tuple[tuple[Any, ...], ...]:
    return tuple(tuple(image[key] for key in image.keys()) for image in images)


def _publish_staged_image(staged: Path, project_uuid: str, section_code: str) -> str:
    safe_section = re.sub(r"[^A-Za-z0-9_-]+", "-", section_code)
    relative_dir = Path("uploads") / project_uuid / safe_section
    destination_dir = (settings.storage_path / relative_dir).resolve()
    storage_root = settings.storage_path.resolve()
    if not _is_relative_to(destination_dir, storage_root):
        raise ProjectServiceError(
            "UPGRADE_TARGET_PATH_UNSAFE",
            "目标图片目录不安全。",
        )
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{uuid.uuid4().hex}{staged.suffix or '.png'}"
    shutil.move(str(staged), destination)
    return (relative_dir / destination.name).as_posix()


def _resolve_storage_source(relative_value: str) -> Path:
    relative = Path(relative_value)
    if relative.is_absolute() or not relative_value.strip():
        raise ProjectServiceError(
            "SOURCE_EVIDENCE_PATH_UNSAFE",
            "源项目图片路径不安全。",
        )
    storage_root = settings.storage_path.resolve()
    try:
        candidate = (storage_root / relative).resolve(strict=True)
    except FileNotFoundError as exc:
        raise ProjectServiceError(
            "SOURCE_EVIDENCE_MISSING",
            "源项目图片文件不存在。",
            details={"file_path": relative.as_posix()},
        ) from exc
    if not candidate.is_file() or not _is_relative_to(candidate, storage_root):
        raise ProjectServiceError(
            "SOURCE_EVIDENCE_PATH_UNSAFE",
            "源项目图片路径不安全。",
        )
    current = storage_root
    for part in relative.parts:
        current = current / part
        attributes = getattr(current.lstat(), "st_file_attributes", 0)
        if current.is_symlink() or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
            raise ProjectServiceError(
                "SOURCE_EVIDENCE_PATH_UNSAFE",
                "源项目图片路径包含不受信任的重解析点。",
            )
    return candidate


def _project_upgrade_staging_dir(
    source_project_uuid: str,
    idempotency_key: str,
    lease_id: str,
) -> Path:
    return _project_upgrade_operation_dir(source_project_uuid, idempotency_key) / lease_id


def _project_upgrade_operation_dir(
    source_project_uuid: str,
    idempotency_key: str,
) -> Path:
    return settings.runtime_paths.migration_path / "project-upgrades" / source_project_uuid / idempotency_key


def _upgrade_target_uuid(source_project_uuid: str, idempotency_key: str) -> str:
    return str(
        uuid.uuid5(
            UPGRADE_PROJECT_UUID_NAMESPACE,
            f"{source_project_uuid}:{idempotency_key}",
        )
    )


def _record_upgrade_failure(
    source_project_uuid: str,
    idempotency_key: str,
    error_code: str,
    *,
    cleanup_failed: bool,
) -> None:
    status = "failed_cleanup" if cleanup_failed else "failed"
    timestamp = database.utc_now()
    try:
        with database.connect() as db:
            db.execute(
                """
                INSERT INTO project_upgrade_operations (
                    source_project_uuid,
                    idempotency_key,
                    target_project_id,
                    status,
                    lease_id,
                    error_code,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, NULL, ?, NULL, ?, ?, ?)
                ON CONFLICT(source_project_uuid, idempotency_key)
                DO UPDATE SET
                    target_project_id = NULL,
                    status = excluded.status,
                    lease_id = NULL,
                    error_code = excluded.error_code,
                    updated_at = excluded.updated_at
                WHERE project_upgrade_operations.status <> 'completed'
                """,
                (
                    source_project_uuid,
                    idempotency_key,
                    status,
                    error_code,
                    timestamp,
                    timestamp,
                ),
            )
    except Exception:
        logger.exception("记录项目复制升级失败状态时发生异常")


def _cleanup_upgrade_paths(target_project_uuid: str | None, staging_dir: Path | None) -> bool:
    cleanup_failed = False
    try:
        if staging_dir is not None and staging_dir.exists():
            _safe_remove_tree(staging_dir, settings.runtime_paths.migration_path)
    except Exception:
        cleanup_failed = True
        logger.exception("清理项目复制升级暂存目录失败")
    try:
        if target_project_uuid is not None:
            remove_project_runtime_files(-1, target_project_uuid)
    except Exception:
        cleanup_failed = True
        logger.exception("清理项目复制升级目标目录失败")
    return cleanup_failed


def _remap_figure_tokens(value: str, image_id_map: dict[int, int]) -> str:
    def replace(match: re.Match[str]) -> str:
        source_image_id = int(match.group(1))
        target_image_id = image_id_map.get(source_image_id)
        return match.group(0) if target_image_id is None else f"[[FIG:{target_image_id}]]"

    return database.FIG_TOKEN_RE.sub(replace, value or "")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_remove_tree(path: Path, allowed_root: Path) -> None:
    resolved_root = allowed_root.resolve()
    resolved_path = path.resolve()
    if not _is_relative_to(resolved_path, resolved_root) or resolved_path == resolved_root:
        raise RuntimeError("PROJECT_UPGRADE_CLEANUP_PATH_UNSAFE")
    shutil.rmtree(resolved_path)


def _remove_storage_child(relative_path: Path) -> None:
    storage_root = settings.storage_path.resolve()
    target = (settings.storage_path / relative_path).resolve()
    if not target.exists() or not _is_relative_to(target, storage_root):
        return
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
