"""Idempotent, template-bound initializer for ``full_report`` projects."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..contracts import (
    FULL_REPORT_TEMPLATE_ASSET_SET_HASH,
    FULL_REPORT_TEMPLATE_EDITION,
    FULL_REPORT_TEMPLATE_PACKAGE_ID,
    FULL_REPORT_TEMPLATE_REVISION,
)
from ..services.report_templates.registry import (
    ReportTemplatePackage,
    ReportTemplateRegistry,
    ReportTemplateUnavailable,
    report_template_registry,
)
from .contracts import (
    R2_TEMPLATE_MANIFEST_SHA256,
    REPORT_EDIT_POLICIES,
    REPORT_SECTION_TYPES,
    ReportDomainInitializationError,
)


R2_TEMPLATE_MANIFEST_PATH = (
    Path(__file__).resolve().parent
    / "manifests"
    / "report-2023-2025.12.08.json"
)
REPORT_DOMAIN_UUID_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL,
    "https://github.com/ZN4819/Open-FuLuA/report-domain/v1",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_uuid(project_uuid: str, key: str) -> str:
    return str(uuid.uuid5(REPORT_DOMAIN_UUID_NAMESPACE, f"{project_uuid}:{key}"))


def _bound_identity(row: sqlite3.Row | dict[str, Any]) -> dict[str, str]:
    return {
        "package_id": str(row["template_package_id"] or ""),
        "template_edition": str(row["template_edition"] or ""),
        "template_revision": str(row["template_revision"] or ""),
        "template_asset_set_hash": str(row["template_asset_set_hash"] or ""),
    }


def _expected_identity() -> dict[str, str]:
    return {
        "package_id": FULL_REPORT_TEMPLATE_PACKAGE_ID,
        "template_edition": FULL_REPORT_TEMPLATE_EDITION,
        "template_revision": FULL_REPORT_TEMPLATE_REVISION,
        "template_asset_set_hash": FULL_REPORT_TEMPLATE_ASSET_SET_HASH,
    }


def _load_r2_template_manifest(path: Path = R2_TEMPLATE_MANIFEST_PATH) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ReportDomainInitializationError(
            "BOUND_TEMPLATE_UNAVAILABLE",
            details={"reason": "R2_TEMPLATE_MANIFEST_MISSING"},
        ) from exc
    # 该 R2 语义清单不是 R0 冻结资产，但仍以版本内摘要防止运行时被替换。
    # 摘要统一按 LF 计算，避免 Windows checkout 的 CRLF 改写造成误报。
    normalized = data.replace(b"\r\n", b"\n")
    if hashlib.sha256(normalized).hexdigest() != R2_TEMPLATE_MANIFEST_SHA256:
        raise ReportDomainInitializationError(
            "BOUND_TEMPLATE_UNAVAILABLE",
            details={"reason": "R2_TEMPLATE_MANIFEST_UNTRUSTED"},
        )
    try:
        manifest = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReportDomainInitializationError(
            "BOUND_TEMPLATE_UNAVAILABLE",
            details={"reason": "R2_TEMPLATE_MANIFEST_INVALID"},
        ) from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "1.0":
        raise ReportDomainInitializationError(
            "BOUND_TEMPLATE_UNAVAILABLE",
            details={"reason": "R2_TEMPLATE_MANIFEST_INCOMPATIBLE"},
        )
    return manifest


def _validate_manifest(
    manifest: dict[str, Any],
    package: ReportTemplatePackage,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    expected = _expected_identity()
    package_identity = {
        "package_id": package.package_id,
        "template_edition": package.template_edition,
        "template_revision": package.template_revision,
        "template_asset_set_hash": package.asset_set_hash,
    }
    if manifest.get("binding") != expected or package_identity != expected:
        raise ReportDomainInitializationError(
            "BOUND_TEMPLATE_UNAVAILABLE",
            details={"reason": "R2_TEMPLATE_BINDING_MISMATCH"},
        )

    sections = manifest.get("sections")
    blocks = manifest.get("blocks")
    standards = manifest.get("template_standards")
    if not isinstance(sections, list) or not sections or not isinstance(blocks, list):
        raise ReportDomainInitializationError(
            "BOUND_TEMPLATE_UNAVAILABLE",
            details={"reason": "R2_TEMPLATE_TREE_INVALID"},
        )
    if not isinstance(standards, list) or len(standards) != 5:
        raise ReportDomainInitializationError(
            "BOUND_TEMPLATE_UNAVAILABLE",
            details={"reason": "R2_TEMPLATE_STANDARD_SET_INVALID"},
        )

    section_by_key: dict[str, dict[str, Any]] = {}
    sibling_orders: set[tuple[str | None, int]] = set()
    for section in sections:
        if not isinstance(section, dict):
            raise ReportDomainInitializationError("BOUND_TEMPLATE_UNAVAILABLE")
        key = section.get("section_key")
        parent_key = section.get("parent_key")
        level = section.get("level")
        order = section.get("sort_order")
        if (
            not isinstance(key, str)
            or not key
            or key in section_by_key
            or (parent_key is not None and parent_key not in section_by_key)
            or not isinstance(level, int)
            or level < 1
            or level > 8
            or not isinstance(order, int)
            or order < 0
            or (parent_key, order) in sibling_orders
            or section.get("section_type") not in REPORT_SECTION_TYPES
            or section.get("edit_policy") not in REPORT_EDIT_POLICIES
            or not isinstance(section.get("title"), str)
            or not section["title"]
        ):
            raise ReportDomainInitializationError(
                "BOUND_TEMPLATE_UNAVAILABLE",
                details={"reason": "R2_TEMPLATE_TREE_INVALID"},
            )
        expected_level = 1 if parent_key is None else int(section_by_key[parent_key]["level"]) + 1
        if level != expected_level:
            raise ReportDomainInitializationError(
                "BOUND_TEMPLATE_UNAVAILABLE",
                details={"reason": "R2_TEMPLATE_TREE_INVALID"},
            )
        sibling_orders.add((parent_key, order))
        section_by_key[key] = section

    package_blocks = {
        str(item.get("block_id")): item
        for item in package.manifest.get("blocks", [])
        if isinstance(item, dict)
    }
    block_ids: set[str] = set()
    block_orders: set[tuple[str, int]] = set()
    for block in blocks:
        if not isinstance(block, dict):
            raise ReportDomainInitializationError("BOUND_TEMPLATE_UNAVAILABLE")
        block_id = block.get("manifest_block_id")
        section_key = block.get("section_key")
        order = block.get("sort_order")
        if (
            not isinstance(block_id, str)
            or block_id in block_ids
            or block_id not in package_blocks
            or section_key not in section_by_key
            or not isinstance(order, int)
            or order < 0
            or (section_key, order) in block_orders
        ):
            raise ReportDomainInitializationError(
                "BOUND_TEMPLATE_UNAVAILABLE",
                details={"reason": "R2_TEMPLATE_BLOCK_MAP_INVALID"},
            )
        block_ids.add(block_id)
        block_orders.add((section_key, order))
    if block_ids != set(package_blocks):
        raise ReportDomainInitializationError(
            "BOUND_TEMPLATE_UNAVAILABLE",
            details={"reason": "R2_TEMPLATE_BLOCK_SET_MISMATCH"},
        )

    standard_orders: set[int] = set()
    for standard in standards:
        if (
            not isinstance(standard, dict)
            or not isinstance(standard.get("standard_code"), str)
            or not isinstance(standard.get("standard_name"), str)
            or not standard["standard_name"]
            or not isinstance(standard.get("sort_order"), int)
            or standard["sort_order"] in standard_orders
        ):
            raise ReportDomainInitializationError(
                "BOUND_TEMPLATE_UNAVAILABLE",
                details={"reason": "R2_TEMPLATE_STANDARD_SET_INVALID"},
            )
        standard_orders.add(standard["sort_order"])
    return sections, blocks, standards


def _load_bound_package(registry: ReportTemplateRegistry) -> ReportTemplatePackage:
    try:
        return registry.load()
    except ReportTemplateUnavailable as exc:
        raise ReportDomainInitializationError(
            "BOUND_TEMPLATE_UNAVAILABLE",
            details={"reason": exc.code, "asset": exc.asset},
        ) from exc


def _insert_singletons(db: sqlite3.Connection, project_id: int, project_uuid: str) -> None:
    timestamp = _utc_now()
    db.execute(
        """
        INSERT INTO report_metadata (
            metadata_uuid, project_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(project_id) DO NOTHING
        """,
        (_stable_uuid(project_uuid, "metadata"), project_id, timestamp, timestamp),
    )
    db.execute(
        """
        INSERT INTO report_phase_dates (
            phase_dates_uuid, project_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(project_id) DO NOTHING
        """,
        (_stable_uuid(project_uuid, "phase-dates"), project_id, timestamp, timestamp),
    )
    db.execute(
        """
        INSERT INTO report_distribution (
            distribution_uuid, project_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(project_id) DO NOTHING
        """,
        (_stable_uuid(project_uuid, "distribution"), project_id, timestamp, timestamp),
    )
    db.execute(
        """
        INSERT INTO system_profiles (
            profile_uuid, project_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(project_id) DO NOTHING
        """,
        (_stable_uuid(project_uuid, "system-profile"), project_id, timestamp, timestamp),
    )
    db.execute(
        """
        INSERT INTO report_organizations (
            organization_uuid, project_id, organization_type, sort_order,
            created_at, updated_at
        ) VALUES (?, ?, 'assessed', 1, ?, ?)
        ON CONFLICT DO NOTHING
        """,
        (_stable_uuid(project_uuid, "organization:assessed"), project_id, timestamp, timestamp),
    )


def _insert_template_standards(
    db: sqlite3.Connection,
    project_id: int,
    project_uuid: str,
    standards: list[dict[str, Any]],
) -> None:
    timestamp = _utc_now()
    for standard in standards:
        order = int(standard["sort_order"])
        db.execute(
            """
            INSERT INTO report_standards (
                standard_uuid, project_id, standard_kind, standard_code,
                standard_name, source_reference, sort_order, created_at, updated_at
            ) VALUES (?, ?, 'template_constant', ?, ?, ?, ?, ?, ?)
            ON CONFLICT(standard_uuid) DO NOTHING
            """,
            (
                _stable_uuid(project_uuid, f"template-standard:{order}"),
                project_id,
                standard["standard_code"],
                standard["standard_name"],
                "report-2023-2025.12.08",
                order,
                timestamp,
                timestamp,
            ),
        )
        stored = db.execute(
            "SELECT * FROM report_standards WHERE standard_uuid = ?",
            (_stable_uuid(project_uuid, f"template-standard:{order}"),),
        ).fetchone()
        actual = tuple(
            stored[name]
            for name in (
                "project_id",
                "standard_kind",
                "standard_code",
                "standard_name",
                "source_reference",
                "sort_order",
            )
        )
        expected = (
            project_id,
            "template_constant",
            standard["standard_code"],
            standard["standard_name"],
            "report-2023-2025.12.08",
            order,
        )
        if actual != expected:
            raise ReportDomainInitializationError(
                "BOUND_TEMPLATE_UNAVAILABLE",
                project_uuid=project_uuid,
                details={
                    "reason": "REPORT_STANDARD_TEMPLATE_DRIFT",
                    "sort_order": order,
                },
            )


def _insert_sections_and_blocks(
    db: sqlite3.Connection,
    project: sqlite3.Row,
    package: ReportTemplatePackage,
    sections: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
) -> None:
    project_id = int(project["id"])
    project_uuid = str(project["project_uuid"])
    timestamp = _utc_now()
    section_ids: dict[str, int] = {}
    for section in sections:
        parent_key = section["parent_key"]
        parent_id = section_ids.get(parent_key) if parent_key is not None else None
        db.execute(
            """
            INSERT INTO report_sections (
                section_uuid, project_id, section_key, parent_section_id, title,
                level, sort_order, section_type, edit_policy,
                template_package_id, template_edition, template_revision,
                template_asset_set_hash, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, section_key) DO NOTHING
            """,
            (
                _stable_uuid(project_uuid, f"section:{section['section_key']}"),
                project_id,
                section["section_key"],
                parent_id,
                section["title"],
                section["level"],
                section["sort_order"],
                section["section_type"],
                section["edit_policy"],
                project["template_package_id"],
                project["template_edition"],
                project["template_revision"],
                project["template_asset_set_hash"],
                timestamp,
                timestamp,
            ),
        )
        stored = db.execute(
            "SELECT * FROM report_sections WHERE project_id = ? AND section_key = ?",
            (project_id, section["section_key"]),
        ).fetchone()
        expected = (
            parent_id,
            section["title"],
            section["level"],
            section["sort_order"],
            section["section_type"],
            section["edit_policy"],
            project["template_package_id"],
            project["template_edition"],
            project["template_revision"],
            project["template_asset_set_hash"],
        )
        actual = tuple(
            stored[name]
            for name in (
                "parent_section_id",
                "title",
                "level",
                "sort_order",
                "section_type",
                "edit_policy",
                "template_package_id",
                "template_edition",
                "template_revision",
                "template_asset_set_hash",
            )
        )
        if actual != expected:
            raise ReportDomainInitializationError(
                "BOUND_TEMPLATE_UNAVAILABLE",
                project_uuid=project_uuid,
                details={"reason": "REPORT_SECTION_TEMPLATE_DRIFT", "section_key": section["section_key"]},
            )
        section_ids[section["section_key"]] = int(stored["id"])

    package_blocks = {
        str(item["block_id"]): item for item in package.manifest["blocks"]
    }
    tables = {
        str(item["owner_block"]): item for item in package.manifest["tables"]
    }
    for block in blocks:
        block_id = str(block["manifest_block_id"])
        package_block = package_blocks[block_id]
        table = tables[block_id]
        payload = {
            "manifest_block_id": block_id,
            "table_anchor": table["table_anchor"],
            "start_anchor": package_block["start_anchor"],
            "end_anchor": package_block["end_anchor"],
        }
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        section_id = section_ids[str(block["section_key"])]
        db.execute(
            """
            INSERT INTO report_blocks (
                block_uuid, project_id, section_id, block_key, block_type,
                payload_json, source_kind, edit_policy, baseline_kind,
                baseline_json, baseline_hash, generation_status,
                confirmation_status, sort_order, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'generated', ?, 'template_constant', 'readonly',
                      'template_default', ?, ?, 'not_generated', 'unconfirmed', ?, ?, ?)
            ON CONFLICT(project_id, block_key) DO NOTHING
            """,
            (
                _stable_uuid(project_uuid, f"block:{block_id}"),
                project_id,
                section_id,
                block_id,
                payload_json,
                payload_json,
                payload_hash,
                int(block["sort_order"]),
                timestamp,
                timestamp,
            ),
        )
        stored = db.execute(
            "SELECT * FROM report_blocks WHERE project_id = ? AND block_key = ?",
            (project_id, block_id),
        ).fetchone()
        expected = (
            section_id,
            "generated",
            "template_constant",
            "readonly",
            int(block["sort_order"]),
        )
        actual = tuple(
            stored[name]
            for name in ("section_id", "block_type", "source_kind", "edit_policy", "sort_order")
        )
        if actual != expected:
            raise ReportDomainInitializationError(
                "BOUND_TEMPLATE_UNAVAILABLE",
                project_uuid=project_uuid,
                details={"reason": "REPORT_BLOCK_TEMPLATE_DRIFT", "block_key": block_id},
            )


def _verify_initialized_counts(
    db: sqlite3.Connection,
    project_id: int,
    project_uuid: str,
    section_count: int,
    block_count: int,
    standard_count: int,
) -> None:
    for table_name in (
        "report_metadata",
        "report_phase_dates",
        "report_distribution",
        "system_profiles",
    ):
        count = int(
            db.execute(
                f"SELECT COUNT(*) FROM {table_name} WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
        )
        if count != 1:
            raise ReportDomainInitializationError(
                "BOUND_TEMPLATE_UNAVAILABLE",
                project_uuid=project_uuid,
                details={"reason": "REPORT_SINGLETON_CARDINALITY_INVALID", "entity": table_name},
            )
    expected_counts = {
        "report_sections": (
            "SELECT COUNT(*) FROM report_sections WHERE project_id = ?",
            section_count,
        ),
        "report_blocks": (
            "SELECT COUNT(*) FROM report_blocks "
            "WHERE project_id = ? AND source_kind = 'template_constant'",
            block_count,
        ),
        "report_standards": (
            "SELECT COUNT(*) FROM report_standards "
            "WHERE project_id = ? AND standard_kind = 'template_constant'",
            standard_count,
        ),
    }
    for table_name, (query, expected) in expected_counts.items():
        actual = int(
            db.execute(query, (project_id,)).fetchone()[0]
        )
        if actual != expected:
            raise ReportDomainInitializationError(
                "BOUND_TEMPLATE_UNAVAILABLE",
                project_uuid=project_uuid,
                details={"reason": "REPORT_TEMPLATE_CARDINALITY_INVALID", "entity": table_name},
            )


def initialize_report_domain(
    db: sqlite3.Connection,
    *,
    project_id: int | None = None,
    project_uuid: str | None = None,
    registry: ReportTemplateRegistry | None = None,
) -> dict[str, int]:
    """Initialize one full-report project in an atomic SQLite savepoint.

    The caller may already own a larger transaction (schema migration or project
    creation).  A savepoint preserves both use cases without committing either.
    """

    if (project_id is None) == (project_uuid is None):
        raise ValueError("project_id 与 project_uuid 必须且只能提供一个")
    if project_id is not None:
        project = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    else:
        project = db.execute(
            "SELECT * FROM projects WHERE project_uuid = ?", (project_uuid,)
        ).fetchone()
    if project is None:
        raise ReportDomainInitializationError("PROJECT_NOT_FOUND", project_uuid=project_uuid)
    resolved_uuid = str(project["project_uuid"])
    if project["project_type"] != "full_report":
        raise ReportDomainInitializationError(
            "REPORT_DOMAIN_NOT_AVAILABLE",
            project_uuid=resolved_uuid,
        )
    if _bound_identity(project) != _expected_identity():
        raise ReportDomainInitializationError(
            "BOUND_TEMPLATE_UNAVAILABLE",
            project_uuid=resolved_uuid,
            details={"reason": "PROJECT_TEMPLATE_BINDING_MISMATCH"},
        )

    db.execute("SAVEPOINT initialize_report_domain")
    try:
        package = _load_bound_package(registry or report_template_registry)
        manifest = _load_r2_template_manifest()
        sections, blocks, standards = _validate_manifest(manifest, package)
        _insert_singletons(db, int(project["id"]), resolved_uuid)
        _insert_template_standards(db, int(project["id"]), resolved_uuid, standards)
        _insert_sections_and_blocks(db, project, package, sections, blocks)
        _verify_initialized_counts(
            db,
            int(project["id"]),
            resolved_uuid,
            len(sections),
            len(blocks),
            len(standards),
        )
    except BaseException:
        db.execute("ROLLBACK TO SAVEPOINT initialize_report_domain")
        db.execute("RELEASE SAVEPOINT initialize_report_domain")
        raise
    db.execute("RELEASE SAVEPOINT initialize_report_domain")
    return {
        "sections": len(sections),
        "blocks": len(blocks),
        "template_standards": len(standards),
    }


def initialize_existing_full_report_projects(
    db: sqlite3.Connection,
    *,
    registry: ReportTemplateRegistry | None = None,
) -> int:
    projects = db.execute(
        "SELECT id FROM projects WHERE project_type = 'full_report' ORDER BY id"
    ).fetchall()
    for project in projects:
        initialize_report_domain(db, project_id=int(project["id"]), registry=registry)
    return len(projects)
