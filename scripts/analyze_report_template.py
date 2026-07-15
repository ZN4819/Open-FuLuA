"""输出不含路径、正文、批注作者和批注原文的模板取证 JSON。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.report_templates import analyze_report_template  # noqa: E402
from _safe_output import ensure_distinct_paths  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="安全分析完整报告 DOCX 模板")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument(
        "--source-role",
        required=True,
        choices=("base_template", "customer_sample", "synthetic_fixture"),
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output:
        ensure_distinct_paths(args.source, args.output)
    result = analyze_report_template(args.source, source_role=args.source_role)
    payload = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
