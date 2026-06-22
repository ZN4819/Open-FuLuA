from __future__ import annotations

from dataclasses import dataclass, field


DOCX_IMPORT_STATUSES = {
    "uploaded",
    "parsing",
    "preview_ready",
    "importing",
    "succeeded",
    "failed",
}


@dataclass(frozen=True)
class DocxImportIssueModel:
    severity: str
    code: str
    message: str
    section_code: str | None = None
    target: str | None = None


@dataclass(frozen=True)
class DocxImportSectionPreviewModel:
    code: str
    title: str
    table_title: str
    table_type: str
    row_count: int = 0
    image_count: int = 0
    reference_count: int = 0


@dataclass(frozen=True)
class DocxTableCandidate:
    body_index: int
    table_index: int
    section_code: str
    table_type: str
    row_count: int
    column_count: int
    data_row_count: int
    confidence: float


@dataclass(frozen=True)
class DocxStructureScan:
    suggested_project_name: str
    has_appendix_title: bool
    sections: list[DocxImportSectionPreviewModel] = field(default_factory=list)
    table_candidates: list[DocxTableCandidate] = field(default_factory=list)
    issues: list[DocxImportIssueModel] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class DocxImportParsedProject:
    suggested_project_name: str
    sections: list[DocxImportSectionPreviewModel] = field(default_factory=list)
    issues: list[DocxImportIssueModel] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)
