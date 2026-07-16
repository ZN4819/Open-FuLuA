"""SQLite schema for the R2 structured report domain.

The migration deliberately executes one statement at a time.  ``executescript``
would issue an implicit commit in sqlite3 and would break the all-or-nothing
upgrade guarantee used by :func:`app.database.init_db`.
"""

from __future__ import annotations

import sqlite3

from .contracts import REPORT_CORE_AUXILIARY_TABLES, REPORT_CORE_ENTITY_TABLES


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS report_organizations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_uuid TEXT NOT NULL UNIQUE CHECK(TRIM(organization_uuid) <> ''),
        project_id INTEGER NOT NULL,
        organization_type TEXT NOT NULL
            CHECK(organization_type IN ('assessed', 'client', 'vendor', 'other')),
        name TEXT NOT NULL DEFAULT '',
        address TEXT NOT NULL DEFAULT '',
        postal_code TEXT NOT NULL DEFAULT '',
        contact_name TEXT NOT NULL DEFAULT '',
        contact_title TEXT NOT NULL DEFAULT '',
        contact_department TEXT NOT NULL DEFAULT '',
        office_phone TEXT NOT NULL DEFAULT '',
        mobile_phone TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
        sort_order INTEGER NOT NULL DEFAULT 0 CHECK(sort_order >= 0),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(organization_uuid, project_id),
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS report_members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        member_uuid TEXT NOT NULL UNIQUE CHECK(TRIM(member_uuid) <> ''),
        project_id INTEGER NOT NULL,
        organization_uuid TEXT,
        name TEXT NOT NULL DEFAULT '',
        team_role TEXT NOT NULL DEFAULT '组员',
        is_project_leader INTEGER NOT NULL DEFAULT 0 CHECK(is_project_leader IN (0, 1)),
        qualification_passed_at TEXT,
        title TEXT NOT NULL DEFAULT '',
        department TEXT NOT NULL DEFAULT '',
        certificate_number TEXT NOT NULL DEFAULT '',
        office_phone TEXT NOT NULL DEFAULT '',
        mobile_phone TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
        sort_order INTEGER NOT NULL DEFAULT 0 CHECK(sort_order >= 0),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(member_uuid, project_id),
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY(organization_uuid, project_id)
            REFERENCES report_organizations(organization_uuid, project_id)
            ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS report_metadata (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        metadata_uuid TEXT NOT NULL UNIQUE CHECK(TRIM(metadata_uuid) <> ''),
        project_id INTEGER NOT NULL UNIQUE,
        report_number TEXT NOT NULL DEFAULT '',
        default_export_version TEXT NOT NULL DEFAULT 'V1.0',
        classification_level TEXT NOT NULL DEFAULT '',
        confidentiality_level TEXT NOT NULL DEFAULT '',
        compiler_member_uuid TEXT,
        reviewer_member_uuid TEXT,
        approver_member_uuid TEXT,
        extension_json TEXT NOT NULL DEFAULT '{}'
            CHECK(json_valid(extension_json) AND json_type(extension_json) = 'object'),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY(compiler_member_uuid, project_id)
            REFERENCES report_members(member_uuid, project_id)
            ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY(reviewer_member_uuid, project_id)
            REFERENCES report_members(member_uuid, project_id)
            ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY(approver_member_uuid, project_id)
            REFERENCES report_members(member_uuid, project_id)
            ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS report_phase_dates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phase_dates_uuid TEXT NOT NULL UNIQUE CHECK(TRIM(phase_dates_uuid) <> ''),
        project_id INTEGER NOT NULL UNIQUE,
        preparation_start TEXT,
        preparation_end TEXT,
        scheme_start TEXT,
        scheme_end TEXT,
        fieldwork_start TEXT,
        fieldwork_end TEXT,
        analysis_start TEXT,
        analysis_end TEXT,
        travel_records_json TEXT NOT NULL DEFAULT '[]'
            CHECK(json_valid(travel_records_json) AND json_type(travel_records_json) = 'array'),
        site_visit_records_json TEXT NOT NULL DEFAULT '[]'
            CHECK(json_valid(site_visit_records_json) AND json_type(site_visit_records_json) = 'array'),
        scheme_review_at TEXT,
        report_review_at TEXT,
        approved_at TEXT,
        local_travel_not_applicable INTEGER NOT NULL DEFAULT 0
            CHECK(local_travel_not_applicable IN (0, 1)),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS report_distribution (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        distribution_uuid TEXT NOT NULL UNIQUE CHECK(TRIM(distribution_uuid) <> ''),
        project_id INTEGER NOT NULL UNIQUE,
        regulator_copies INTEGER NOT NULL DEFAULT 0 CHECK(regulator_copies >= 0),
        client_copies INTEGER NOT NULL DEFAULT 0 CHECK(client_copies >= 0),
        assessment_organization_copies INTEGER NOT NULL DEFAULT 0
            CHECK(assessment_organization_copies >= 0),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS system_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_uuid TEXT NOT NULL UNIQUE CHECK(TRIM(profile_uuid) <> ''),
        project_id INTEGER NOT NULL UNIQUE,
        system_name TEXT NOT NULL DEFAULT '',
        system_summary TEXT NOT NULL DEFAULT '',
        critical_infrastructure_status TEXT NOT NULL DEFAULT '',
        critical_infrastructure_department TEXT NOT NULL DEFAULT '',
        level_filing_status TEXT NOT NULL DEFAULT '',
        level_filing_s TEXT NOT NULL DEFAULT '',
        level_filing_a TEXT NOT NULL DEFAULT '',
        level_filing_g TEXT NOT NULL DEFAULT '',
        level_filing_number TEXT NOT NULL DEFAULT '',
        level_filing_consistent TEXT NOT NULL DEFAULT '',
        level_filing_difference TEXT NOT NULL DEFAULT '',
        level_assessment_status TEXT NOT NULL DEFAULT '',
        level_assessment_organization TEXT NOT NULL DEFAULT '',
        level_assessment_period TEXT NOT NULL DEFAULT '',
        level_assessment_conclusion TEXT NOT NULL DEFAULT '',
        service_scope_json TEXT NOT NULL DEFAULT '{}'
            CHECK(json_valid(service_scope_json) AND json_type(service_scope_json) = 'object'),
        platform_json TEXT NOT NULL DEFAULT '{}'
            CHECK(json_valid(platform_json) AND json_type(platform_json) = 'object'),
        operation_json TEXT NOT NULL DEFAULT '{}'
            CHECK(json_valid(operation_json) AND json_type(operation_json) = 'object'),
        interconnection_json TEXT NOT NULL DEFAULT '{}'
            CHECK(json_valid(interconnection_json) AND json_type(interconnection_json) = 'object'),
        cloud_platform_json TEXT NOT NULL DEFAULT '{}'
            CHECK(json_valid(cloud_platform_json) AND json_type(cloud_platform_json) = 'object'),
        crypto_plan_json TEXT NOT NULL DEFAULT '{}'
            CHECK(json_valid(crypto_plan_json) AND json_type(crypto_plan_json) = 'object'),
        no_crypto_products INTEGER NOT NULL DEFAULT 0 CHECK(no_crypto_products IN (0, 1)),
        selected_algorithms_json TEXT NOT NULL DEFAULT '[]'
            CHECK(json_valid(selected_algorithms_json) AND json_type(selected_algorithms_json) = 'array'),
        level_match_evidence_json TEXT NOT NULL DEFAULT '{}'
            CHECK(json_valid(level_match_evidence_json) AND json_type(level_match_evidence_json) = 'object'),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS system_crypto_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_uuid TEXT NOT NULL UNIQUE CHECK(TRIM(product_uuid) <> ''),
        project_id INTEGER NOT NULL,
        product_name TEXT NOT NULL DEFAULT '',
        manufacturer TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        certificate_number TEXT NOT NULL DEFAULT '',
        deployment_location TEXT NOT NULL DEFAULT '',
        purpose TEXT NOT NULL DEFAULT '',
        quantity_text TEXT NOT NULL DEFAULT '',
        normalized_quantity INTEGER NOT NULL DEFAULT 0 CHECK(normalized_quantity >= 0),
        use_mode TEXT NOT NULL DEFAULT 'exclusive' CHECK(use_mode IN ('exclusive', 'shared')),
        classification TEXT NOT NULL DEFAULT 'certified'
            CHECK(classification IN ('certified', 'uncertified_domestic', 'foreign')),
        sort_order INTEGER NOT NULL DEFAULT 0 CHECK(sort_order >= 0),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS report_standards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        standard_uuid TEXT NOT NULL UNIQUE CHECK(TRIM(standard_uuid) <> ''),
        project_id INTEGER NOT NULL,
        standard_kind TEXT NOT NULL CHECK(standard_kind IN ('template_constant', 'manual')),
        standard_code TEXT NOT NULL DEFAULT '',
        standard_name TEXT NOT NULL DEFAULT '',
        source_reference TEXT NOT NULL DEFAULT '',
        sort_order INTEGER NOT NULL DEFAULT 0 CHECK(sort_order >= 0),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(standard_uuid, project_id),
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS special_indicators (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        indicator_uuid TEXT NOT NULL UNIQUE CHECK(TRIM(indicator_uuid) <> ''),
        project_id INTEGER NOT NULL,
        manual_standard_uuid TEXT NOT NULL,
        indicator_code TEXT NOT NULL DEFAULT '',
        indicator_name TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        sort_order INTEGER NOT NULL DEFAULT 0 CHECK(sort_order >= 0),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY(manual_standard_uuid, project_id)
            REFERENCES report_standards(standard_uuid, project_id)
            ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS assessment_objects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        object_uuid TEXT NOT NULL UNIQUE CHECK(TRIM(object_uuid) <> ''),
        project_id INTEGER NOT NULL,
        object_type TEXT NOT NULL DEFAULT 'other',
        name_snapshot TEXT NOT NULL DEFAULT '',
        source_section_code TEXT,
        source_row_id INTEGER,
        properties_json TEXT NOT NULL DEFAULT '{}'
            CHECK(json_valid(properties_json) AND json_type(properties_json) = 'object'),
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(object_uuid, project_id),
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY(source_row_id) REFERENCES assessment_rows(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS assessment_object_subsystems (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        binding_uuid TEXT NOT NULL UNIQUE CHECK(TRIM(binding_uuid) <> ''),
        project_id INTEGER NOT NULL,
        object_uuid TEXT NOT NULL UNIQUE,
        subsystem_name TEXT NOT NULL DEFAULT '',
        assessment_methods_json TEXT NOT NULL DEFAULT '[]'
            CHECK(json_valid(assessment_methods_json) AND json_type(assessment_methods_json) = 'array'),
        remark TEXT NOT NULL DEFAULT '',
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY(object_uuid, project_id)
            REFERENCES assessment_objects(object_uuid, project_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS object_relations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        relation_uuid TEXT NOT NULL UNIQUE CHECK(TRIM(relation_uuid) <> ''),
        project_id INTEGER NOT NULL,
        source_object_uuid TEXT NOT NULL,
        target_object_uuid TEXT NOT NULL,
        relation_type TEXT NOT NULL CHECK(TRIM(relation_type) <> ''),
        properties_json TEXT NOT NULL DEFAULT '{}'
            CHECK(json_valid(properties_json) AND json_type(properties_json) = 'object'),
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK(source_object_uuid <> target_object_uuid),
        UNIQUE(project_id, source_object_uuid, target_object_uuid, relation_type),
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY(source_object_uuid, project_id)
            REFERENCES assessment_objects(object_uuid, project_id)
            ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY(target_object_uuid, project_id)
            REFERENCES assessment_objects(object_uuid, project_id)
            ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS result_correction_relations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        correction_uuid TEXT NOT NULL UNIQUE CHECK(TRIM(correction_uuid) <> ''),
        project_id INTEGER NOT NULL,
        a2_object_uuid TEXT NOT NULL,
        a2_metric_code TEXT NOT NULL CHECK(TRIM(a2_metric_code) <> ''),
        a4_object_uuid TEXT NOT NULL,
        a4_metric_code TEXT NOT NULL CHECK(TRIM(a4_metric_code) <> ''),
        correction_kind TEXT NOT NULL CHECK(correction_kind IN ('confidentiality', 'integrity')),
        original_references_json TEXT NOT NULL DEFAULT '{}'
            CHECK(json_valid(original_references_json) AND json_type(original_references_json) = 'object'),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(project_id, a4_object_uuid, correction_kind),
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY(a2_object_uuid, project_id)
            REFERENCES assessment_objects(object_uuid, project_id)
            ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY(a4_object_uuid, project_id)
            REFERENCES assessment_objects(object_uuid, project_id)
            ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS report_sections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        section_uuid TEXT NOT NULL UNIQUE CHECK(TRIM(section_uuid) <> ''),
        project_id INTEGER NOT NULL,
        section_key TEXT NOT NULL CHECK(TRIM(section_key) <> ''),
        parent_section_id INTEGER,
        title TEXT NOT NULL,
        level INTEGER NOT NULL CHECK(level BETWEEN 1 AND 8),
        sort_order INTEGER NOT NULL CHECK(sort_order >= 0),
        section_type TEXT NOT NULL
            CHECK(section_type IN ('form', 'blocks', 'generated', 'appendix_a', 'appendix_b')),
        edit_policy TEXT NOT NULL
            CHECK(edit_policy IN ('editable', 'overrideable', 'readonly')),
        completion_status TEXT NOT NULL DEFAULT 'not_started'
            CHECK(completion_status IN ('not_started', 'in_progress', 'complete')),
        template_package_id TEXT NOT NULL,
        template_edition TEXT NOT NULL,
        template_revision TEXT NOT NULL,
        template_asset_set_hash TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(project_id, section_key),
        UNIQUE(id, project_id),
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY(parent_section_id, project_id)
            REFERENCES report_sections(id, project_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS report_blocks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        block_uuid TEXT NOT NULL UNIQUE CHECK(TRIM(block_uuid) <> ''),
        project_id INTEGER NOT NULL,
        section_id INTEGER NOT NULL,
        block_key TEXT NOT NULL CHECK(TRIM(block_key) <> ''),
        block_type TEXT NOT NULL CHECK(block_type IN (
            'paragraph', 'bullet_list', 'numbered_list', 'key_value_table',
            'data_table', 'figure', 'reference', 'generated'
        )),
        payload_json TEXT NOT NULL DEFAULT '{}'
            CHECK(json_valid(payload_json) AND json_type(payload_json) = 'object'),
        source_kind TEXT NOT NULL
            CHECK(source_kind IN ('manual', 'imported', 'derived', 'template_constant')),
        edit_policy TEXT NOT NULL
            CHECK(edit_policy IN ('editable', 'overrideable', 'readonly')),
        baseline_kind TEXT CHECK(baseline_kind IS NULL OR baseline_kind = 'template_default'),
        baseline_json TEXT CHECK(
            baseline_json IS NULL OR (json_valid(baseline_json) AND json_type(baseline_json) = 'object')
        ),
        baseline_hash TEXT,
        override_json TEXT CHECK(
            override_json IS NULL OR (json_valid(override_json) AND json_type(override_json) = 'object')
        ),
        source_hash TEXT,
        generation_status TEXT NOT NULL DEFAULT 'not_generated'
            CHECK(generation_status IN ('current', 'stale', 'not_generated')),
        confirmation_status TEXT NOT NULL DEFAULT 'unconfirmed'
            CHECK(confirmation_status IN ('unconfirmed', 'confirmed')),
        sort_order INTEGER NOT NULL CHECK(sort_order >= 0),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(project_id, block_key),
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY(section_id, project_id)
            REFERENCES report_sections(id, project_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS report_warning_confirmations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        relation_id TEXT NOT NULL CHECK(TRIM(relation_id) <> ''),
        entity_path TEXT NOT NULL CHECK(TRIM(entity_path) <> ''),
        warning_code TEXT NOT NULL CHECK(TRIM(warning_code) <> ''),
        source_hash TEXT NOT NULL CHECK(TRIM(source_hash) <> ''),
        confirmed_at TEXT NOT NULL,
        UNIQUE(project_id, relation_id, entity_path, warning_code),
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
    )
    """,
)


_INDEX_DDL: tuple[str, ...] = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_report_org_single_assessed "
    "ON report_organizations(project_id) WHERE organization_type = 'assessed'",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_report_org_single_client "
    "ON report_organizations(project_id) WHERE organization_type = 'client'",
    "CREATE INDEX IF NOT EXISTS idx_report_organizations_project "
    "ON report_organizations(project_id, organization_type, sort_order)",
    "CREATE INDEX IF NOT EXISTS idx_report_members_project "
    "ON report_members(project_id, active, sort_order)",
    "CREATE INDEX IF NOT EXISTS idx_report_metadata_number "
    "ON report_metadata(report_number) WHERE TRIM(report_number) <> ''",
    "CREATE INDEX IF NOT EXISTS idx_crypto_products_project "
    "ON system_crypto_products(project_id, sort_order)",
    "CREATE INDEX IF NOT EXISTS idx_report_standards_project "
    "ON report_standards(project_id, standard_kind, sort_order)",
    "CREATE INDEX IF NOT EXISTS idx_special_indicators_project "
    "ON special_indicators(project_id, sort_order)",
    "CREATE INDEX IF NOT EXISTS idx_assessment_objects_project "
    "ON assessment_objects(project_id, object_type, active)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_assessment_objects_source_row "
    "ON assessment_objects(project_id, source_row_id) WHERE source_row_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_object_relations_project "
    "ON object_relations(project_id, relation_type, active)",
    "CREATE INDEX IF NOT EXISTS idx_correction_relations_project "
    "ON result_correction_relations(project_id, correction_kind)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_report_sections_sibling_order "
    "ON report_sections(project_id, COALESCE(parent_section_id, 0), sort_order)",
    "CREATE INDEX IF NOT EXISTS idx_report_sections_tree "
    "ON report_sections(project_id, parent_section_id, sort_order)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_report_blocks_section_order "
    "ON report_blocks(section_id, sort_order)",
    "CREATE INDEX IF NOT EXISTS idx_report_blocks_project "
    "ON report_blocks(project_id, section_id, sort_order)",
    "CREATE INDEX IF NOT EXISTS idx_report_warning_confirmations_project "
    "ON report_warning_confirmations(project_id, relation_id)",
    "CREATE INDEX IF NOT EXISTS idx_assessment_rows_object_uuid "
    "ON assessment_rows(assessment_object_uuid) WHERE assessment_object_uuid IS NOT NULL",
)


_TRIGGER_DDL: tuple[str, ...] = (
    """
    CREATE TRIGGER IF NOT EXISTS special_indicators_manual_standard_insert_guard
    BEFORE INSERT ON special_indicators
    BEGIN
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1 FROM report_standards
            WHERE standard_uuid = NEW.manual_standard_uuid
              AND project_id = NEW.project_id
              AND standard_kind = 'manual'
        ) THEN RAISE(ABORT, 'SPECIAL_INDICATOR_MANUAL_STANDARD_REQUIRED') END;
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS special_indicators_manual_standard_update_guard
    BEFORE UPDATE OF manual_standard_uuid, project_id ON special_indicators
    BEGIN
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1 FROM report_standards
            WHERE standard_uuid = NEW.manual_standard_uuid
              AND project_id = NEW.project_id
              AND standard_kind = 'manual'
        ) THEN RAISE(ABORT, 'SPECIAL_INDICATOR_MANUAL_STANDARD_REQUIRED') END;
    END
    """,
)


_REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "report_metadata": frozenset({"metadata_uuid", "project_id", "report_number", "revision"}),
    "report_organizations": frozenset({"organization_uuid", "project_id", "organization_type", "revision"}),
    "report_members": frozenset({"member_uuid", "project_id", "organization_uuid", "revision"}),
    "report_phase_dates": frozenset({"phase_dates_uuid", "project_id", "preparation_start", "analysis_end", "revision"}),
    "report_distribution": frozenset({"distribution_uuid", "project_id", "regulator_copies", "revision"}),
    "system_profiles": frozenset({"profile_uuid", "project_id", "system_name", "revision"}),
    "system_crypto_products": frozenset({"product_uuid", "project_id", "quantity_text", "normalized_quantity", "revision"}),
    "report_standards": frozenset({"standard_uuid", "project_id", "standard_kind", "revision"}),
    "special_indicators": frozenset({"indicator_uuid", "project_id", "manual_standard_uuid", "revision"}),
    "assessment_objects": frozenset({"object_uuid", "project_id", "source_row_id", "revision"}),
    "assessment_object_subsystems": frozenset({"binding_uuid", "project_id", "object_uuid", "revision"}),
    "object_relations": frozenset({"relation_uuid", "project_id", "source_object_uuid", "target_object_uuid", "revision"}),
    "result_correction_relations": frozenset({"correction_uuid", "project_id", "a2_object_uuid", "a4_object_uuid", "revision"}),
    "report_sections": frozenset({"section_uuid", "project_id", "section_key", "template_asset_set_hash", "revision"}),
    "report_blocks": frozenset({"block_uuid", "project_id", "section_id", "block_key", "payload_json", "revision"}),
    "report_warning_confirmations": frozenset({
        "project_id", "relation_id", "entity_path", "warning_code", "source_hash", "confirmed_at"
    }),
}

_REQUIRED_SCHEMA_OBJECTS = frozenset({
    "idx_report_org_single_assessed",
    "idx_report_org_single_client",
    "idx_report_metadata_number",
    "idx_report_sections_sibling_order",
    "idx_report_blocks_section_order",
    "idx_assessment_rows_object_uuid",
    "idx_report_warning_confirmations_project",
    "special_indicators_manual_standard_insert_guard",
    "special_indicators_manual_standard_update_guard",
})


def _ensure_assessment_object_column(db: sqlite3.Connection) -> None:
    columns = {row["name"] for row in db.execute("PRAGMA table_info(assessment_rows)")}
    if "assessment_object_uuid" not in columns:
        db.execute("ALTER TABLE assessment_rows ADD COLUMN assessment_object_uuid TEXT")


def ensure_report_core_schema(db: sqlite3.Connection) -> None:
    """Create the R2 tables inside the caller's active migration transaction."""

    _ensure_assessment_object_column(db)
    for statement in _DDL:
        db.execute(statement)
    for statement in _INDEX_DDL:
        db.execute(statement)
    for statement in _TRIGGER_DDL:
        db.execute(statement)


def audit_report_core_schema(db: sqlite3.Connection) -> None:
    """Reject a database claiming schema 5 without the complete R2 contract."""

    actual_tables = {
        str(row["name"])
        for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    missing_tables = (
        set(REPORT_CORE_ENTITY_TABLES) | set(REPORT_CORE_AUXILIARY_TABLES)
    ) - actual_tables
    if missing_tables:
        raise RuntimeError("REPORT_CORE_SCHEMA_INCOMPLETE")
    for table_name, required in _REQUIRED_COLUMNS.items():
        columns = {str(row["name"]) for row in db.execute(f"PRAGMA table_info({table_name})")}
        if not required <= columns:
            raise RuntimeError("REPORT_CORE_SCHEMA_INCOMPLETE")
    assessment_columns = {
        str(row["name"]) for row in db.execute("PRAGMA table_info(assessment_rows)")
    }
    if "assessment_object_uuid" not in assessment_columns:
        raise RuntimeError("REPORT_CORE_SCHEMA_INCOMPLETE")
    schema_objects = {
        str(row["name"])
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('index', 'trigger')"
        )
    }
    if not _REQUIRED_SCHEMA_OBJECTS <= schema_objects:
        raise RuntimeError("REPORT_CORE_SCHEMA_INCOMPLETE")
