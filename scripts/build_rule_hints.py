"""从批准源模板生成脱敏的批注追踪清单，不写出作者、时间或原文。"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from lxml import etree

APPROVED_SOURCE_SHA256 = "b3957fd1da3bf19c31ac515fbdc6bf989fd7df033ca4d179c4b6e9567247fcf8"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W_ID = f"{{{W}}}id"

CATEGORIES = {
    "history": "26,43,46,50,52,53,76,145,221,226,314,315,327,606,621,654,673,676",
    "layout": "1,49,304,587,624,630,640,644,647,655",
    "field_source": "2,42,45,47,296,311,367",
    "consistency": "41,74,77,79,105,293,297,299,352,386,466,472,476,479,588,589,607,608",
    "conditional": "4,48,51,54,73,124,237,308,326,354,356,627,628,637,638,642,650,651,660",
    "evidence": "613,623,657,661,662,663,664,665,667,668,669,670,671",
    "authoring_help": "3,75,80,122,142,174,175,200,220,224,234,290,313,369,378,382,385,445,446,447,467,473,603,605,610,619,620,622,629,632,633,634,635,636,643,674",
}
CATEGORY_SUMMARIES = {
    "history": "模板修订历史提示，待业务负责人裁决，仅归档不运行。",
    "layout": "版式或排版提示，待批准进入 manifest 前仅作为警告。",
    "field_source": "字段来源或证明材料提示，待业务负责人确认。",
    "consistency": "跨章节、表格或附件的一致性核对提示。",
    "conditional": "条件显示或条件必填提示，待业务负责人确认。",
    "evidence": "证据类型、图片或附件完整性提示。",
    "authoring_help": "报告编写方法或对象选择帮助。",
}


def _category_map() -> dict[int, str]:
    result: dict[int, str] = {}
    for category, values in CATEGORIES.items():
        for value in values.split(","):
            comment_id = int(value)
            if comment_id in result:
                raise ValueError("COMMENT_CATEGORY_DUPLICATE")
            result[comment_id] = category
    if len(result) != 121:
        raise ValueError("COMMENT_CATEGORY_COVERAGE_INVALID")
    return result


def _anchor_hashes(document: etree._Element) -> dict[int, str]:
    active: list[int] = []
    text: dict[int, list[str]] = {}
    for node in document.iter():
        local = etree.QName(node).localname
        if local == "commentRangeStart":
            comment_id = int(node.get(W_ID))
            active.append(comment_id)
            text.setdefault(comment_id, [])
        elif local == "commentRangeEnd":
            comment_id = int(node.get(W_ID))
            if comment_id in active:
                active.remove(comment_id)
        elif local == "t" and node.text:
            for comment_id in active:
                text.setdefault(comment_id, []).append(node.text)
    return {
        comment_id: hashlib.sha256("".join(values).encode("utf-8")).hexdigest()
        for comment_id, values in text.items()
    }


def build(source: Path) -> dict[str, object]:
    raw = source.read_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    if fingerprint != APPROVED_SOURCE_SHA256:
        raise ValueError("SOURCE_FINGERPRINT_NOT_APPROVED")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    with zipfile.ZipFile(source) as package:
        document = etree.fromstring(package.read("word/document.xml"), parser=parser)
        comments = etree.fromstring(package.read("word/comments.xml"), parser=parser)
    comment_ids = {int(node.get(W_ID)) for node in comments.findall(f"{{{W}}}comment")}
    categories = _category_map()
    if comment_ids != set(categories):
        raise ValueError("SOURCE_COMMENT_SET_CHANGED")
    anchors = _anchor_hashes(document)
    rules = []
    for comment_id in sorted(comment_ids):
        category = categories[comment_id]
        rules.append({
            "rule_id": f"hint_{comment_id:03d}",
            "source_comment_id": comment_id,
            "source_fingerprint": fingerprint,
            "anchor_hash": anchors.get(comment_id, hashlib.sha256(b"").hexdigest()),
            "category": category,
            "sanitized_summary": CATEGORY_SUMMARIES[category],
            "target_fields": [],
            "approval_status": "pending",
            "runtime_behavior": "none" if category == "history" else ("help" if category == "authoring_help" else "warning"),
        })
    return {"schema_version": "1.0", "rules": rules}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    payload = build(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
