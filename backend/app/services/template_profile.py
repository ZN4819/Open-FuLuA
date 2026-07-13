from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..resource_paths import resolve_resource_path


class TemplateProfileError(RuntimeError):
    """模板 profile 无法读取或结构不符合预期。"""


@lru_cache(maxsize=1)
def load_template_profile() -> dict[str, Any]:
    profile_path = resolve_resource_path("templates", "appendix_a", "template_profile.json")
    if not profile_path.exists():
        raise TemplateProfileError(f"模板 profile 不存在：{profile_path}")

    with profile_path.open("r", encoding="utf-8") as profile_file:
        profile = json.load(profile_file)

    validate_template_profile(profile)
    return profile


def validate_template_profile(profile: dict[str, Any]) -> None:
    sections = profile.get("sections")
    tables = profile.get("tables")
    content_controls = profile.get("content_controls")

    if not isinstance(sections, list) or len(sections) != 8:
        raise TemplateProfileError("模板 profile 必须包含 8 个章节。")
    if not isinstance(tables, dict):
        raise TemplateProfileError("模板 profile 缺少表格配置。")
    if not isinstance(content_controls, dict):
        raise TemplateProfileError("模板 profile 缺少内容控件配置。")

    section_codes = [section.get("code") for section in sections]
    if section_codes != [f"A-{index}" for index in range(1, 9)]:
        raise TemplateProfileError("章节 code 必须按 A-1 至 A-8 排列。")

    for section in sections:
        fixed_object_names = section.get("fixed_object_names", [])
        if not isinstance(fixed_object_names, list) or any(
            not isinstance(name, str) or not name.strip() for name in fixed_object_names
        ):
            raise TemplateProfileError(f"{section.get('code', '未知章节')} 的固定测评对象配置无效。")
        if len(fixed_object_names) != len(set(fixed_object_names)):
            raise TemplateProfileError(f"{section.get('code', '未知章节')} 的固定测评对象不能重复。")
        if section.get("table_type") == "management" and not fixed_object_names:
            raise TemplateProfileError(f"{section.get('code', '未知章节')} 必须配置固定测评对象。")

    technical = tables.get("technical", {})
    management = tables.get("management", {})
    if len(technical.get("columns", [])) != 8:
        raise TemplateProfileError("技术测评表必须包含 8 列。")
    if len(management.get("columns", [])) != 5:
        raise TemplateProfileError("管理测评表必须包含 5 列。")

    technical_options = content_controls.get("technical_metric", {}).get("options")
    management_options = content_controls.get("management_compliance", {}).get("options")
    if technical_options != ["√", "×", "/"]:
        raise TemplateProfileError("技术指标下拉选项不符合预期。")
    if management_options != ["符合", "部分符合", "不符合", "不适用"]:
        raise TemplateProfileError("管理符合情况下拉选项不符合预期。")
