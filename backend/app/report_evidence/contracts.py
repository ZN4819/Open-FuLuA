"""Stable R5 Appendix B category and subtype contracts."""

from __future__ import annotations

from typing import Final


APPENDIX_B_CATEGORIES: Final[tuple[dict[str, object], ...]] = (
    {"code": "B-1", "category_code": "engagement_proof", "title": "委托证明文件", "order": 1},
    {"code": "B-2", "category_code": "travel_accommodation", "title": "差旅住宿等票证", "order": 2},
    {"code": "B-3", "category_code": "onsite_process", "title": "进场记录", "order": 3},
    {"code": "B-4", "category_code": "authorization_notice", "title": "现场测评授权及风险告知", "order": 4},
    {"code": "B-5", "category_code": "plan_review", "title": "测评方案评审", "order": 5},
    {"code": "B-6", "category_code": "report_review", "title": "密评报告评审", "order": 6},
    {"code": "B-7", "category_code": "assessor_roster", "title": "密评人员资格情况", "order": 7},
    {"code": "B-8", "category_code": "assessor_exam_proof", "title": "密评人员考核成绩证明", "order": 8},
    {"code": "B-9", "category_code": "grading_filing", "title": "系统定级备案证明", "order": 9},
)

APPENDIX_B_CATEGORY_CODES: Final[tuple[str, ...]] = tuple(
    str(item["category_code"]) for item in APPENDIX_B_CATEGORIES
)

CATEGORY_BY_CODE: Final[dict[str, dict[str, object]]] = {
    str(item["category_code"]): dict(item) for item in APPENDIX_B_CATEGORIES
}

SINGLE_RECORD_CATEGORIES: Final[frozenset[str]] = frozenset(
    {"engagement_proof", "plan_review", "report_review", "grading_filing"}
)

ALLOWED_RECORD_SUBTYPES: Final[dict[str, frozenset[str]]] = {
    "engagement_proof": frozenset({"engagement"}),
    "travel_accommodation": frozenset({"travel"}),
    "onsite_process": frozenset({"visit"}),
    "authorization_notice": frozenset({"authorization", "risk_notice"}),
    "plan_review": frozenset({"plan_review"}),
    "report_review": frozenset({"report_review"}),
    "assessor_roster": frozenset({"member"}),
    "assessor_exam_proof": frozenset({"exam_proof"}),
    "grading_filing": frozenset({"filing"}),
}

ALLOWED_IMAGE_SUBTYPES: Final[dict[str, frozenset[str]]] = {
    "engagement_proof": frozenset({"engagement_document"}),
    "travel_accommodation": frozenset({"travel_ticket", "accommodation_bill", "accommodation_invoice"}),
    "onsite_process": frozenset({"sign_in", "onsite_photo", "handover_record", "room_access_record"}),
    "authorization_notice": frozenset({"authorization", "risk_notice"}),
    "plan_review": frozenset({"review", "confirmation"}),
    "report_review": frozenset({"review"}),
    "assessor_roster": frozenset(),
    "assessor_exam_proof": frozenset({"exam_proof"}),
    "grading_filing": frozenset({"filing_proof"}),
}

PERSONNEL_ROLES: Final[frozenset[str]] = frozenset(
    {"member", "compiler", "reviewer", "approver"}
)
