"""Pydantic request contracts for the R5 Appendix B evidence API."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .contracts import PERSONNEL_ROLES


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyMetadata(EvidenceModel):
    note: str = Field(default="", max_length=2000)


class EngagementMetadata(EvidenceModel):
    file_type: str = Field(default="", max_length=200)
    amount: str = Field(default="", max_length=40)
    unit_price: str = Field(default="", max_length=40)

    @field_validator("amount", "unit_price")
    @classmethod
    def valid_nonnegative_decimal(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            return ""
        try:
            number = Decimal(normalized)
        except InvalidOperation as exc:
            raise ValueError("金额应为非负数字") from exc
        if number < 0:
            raise ValueError("金额应为非负数字")
        return normalized


class TravelMetadata(EvidenceModel):
    is_local: bool = False


class PlanReviewMetadata(EvidenceModel):
    plan_name: str = Field(default="", max_length=300)


class RosterMetadata(EvidenceModel):
    role: Literal["member", "compiler", "reviewer", "approver"] = "member"


class FilingMetadata(EvidenceModel):
    filing_system_same: bool | None = None
    filing_system_name: str = Field(default="", max_length=300)
    difference: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_different_system(self) -> "FilingMetadata":
        if self.filing_system_same is False:
            if not self.filing_system_name.strip():
                raise ValueError("备案系统与被测系统不同时必须填写备案系统名称")
            if not self.difference.strip():
                raise ValueError("备案系统与被测系统不同时必须填写差异说明")
        return self


METADATA_MODELS: dict[str, type[EvidenceModel]] = {
    "engagement_proof": EngagementMetadata,
    "travel_accommodation": TravelMetadata,
    "onsite_process": EmptyMetadata,
    "authorization_notice": EmptyMetadata,
    "plan_review": PlanReviewMetadata,
    "report_review": EmptyMetadata,
    "assessor_roster": RosterMetadata,
    "assessor_exam_proof": EmptyMetadata,
    "grading_filing": FilingMetadata,
}


def validate_category_metadata(category_code: str, value: dict[str, Any]) -> dict[str, Any]:
    model = METADATA_MODELS[category_code]
    return model.model_validate(value).model_dump(mode="json")


class EvidenceCategoryUpdate(EvidenceModel):
    expected_project_revision: int = Field(ge=1)
    expected_revision: int = Field(ge=1)
    is_not_applicable: bool = False
    not_applicable_reason: str = Field(default="", max_length=2000)
    acknowledge_warning: bool = False

    @model_validator(mode="after")
    def require_reason(self) -> "EvidenceCategoryUpdate":
        if self.is_not_applicable and not self.not_applicable_reason.strip():
            raise ValueError("标记不适用时必须填写原因")
        return self


class EvidenceItemWrite(EvidenceModel):
    expected_project_revision: int = Field(ge=1)
    subtype: str = Field(min_length=1, max_length=80)
    title: str = Field(default="", max_length=300)
    starts_on: str | None = Field(default=None, max_length=40)
    ends_on: str | None = Field(default=None, max_length=40)
    organization_uuid: str | None = Field(default=None, max_length=36)
    location: str = Field(default="", max_length=500)
    sort_order: int = Field(default=0, ge=0, le=100000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    member_uuids: list[str] = Field(default_factory=list, max_length=100)
    related_item_uuids: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("member_uuids", "related_item_uuids")
    @classmethod
    def unique_references(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            normalized = value.strip()
            if normalized and normalized not in cleaned:
                cleaned.append(normalized)
        return cleaned


class EvidenceItemUpdate(EvidenceItemWrite):
    expected_revision: int = Field(ge=1)


class EvidenceImageUpdate(EvidenceModel):
    expected_project_revision: int = Field(ge=1)
    expected_revision: int = Field(ge=1)
    subtype: str = Field(min_length=1, max_length=80)
    caption: str = Field(default="", max_length=1000)
    alt_text: str = Field(default="", max_length=1000)
    sort_order: int = Field(default=0, ge=0, le=100000)


class EvidenceReorderWrite(EvidenceModel):
    expected_project_revision: int = Field(ge=1)
    item_uuids: list[str] = Field(min_length=1, max_length=1000)

    @field_validator("item_uuids")
    @classmethod
    def unique_items(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("排序列表包含重复记录")
        return values


class EvidenceValidationWrite(EvidenceModel):
    expected_project_revision: int = Field(ge=1)


def role_is_valid(value: str) -> bool:
    return value in PERSONNEL_ROLES
