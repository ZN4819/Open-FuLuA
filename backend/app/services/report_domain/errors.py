from __future__ import annotations

from typing import Any


class ReportDomainError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        project_uuid: str | None = None,
        entity_type: str | None = None,
        entity_uuid: str | None = None,
        field: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.project_uuid = project_uuid
        self.entity_type = entity_type
        self.entity_uuid = entity_uuid
        self.field = field
        self.details = details or {}

    def detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "project_uuid": self.project_uuid,
            "entity_type": self.entity_type,
            "entity_uuid": self.entity_uuid,
            "field": self.field,
            "details": self.details,
        }
