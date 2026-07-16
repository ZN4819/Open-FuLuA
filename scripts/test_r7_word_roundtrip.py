"""Run the R7 acceptance path against installed Microsoft Word.

This is intentionally an opt-in developer check.  It builds an isolated full
report fixture, asks the production export service to refresh and save the
document through Microsoft Word, then uploads that exact Word-saved DOCX to the
R7 importer.  It never uses LibreOffice and never writes to the user's live
database or storage tree.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from fastapi import UploadFile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.report_schemas import ReportExportJobWrite  # noqa: E402
from app.services import report_exports  # noqa: E402
from app.services.report_roundtrips import create_roundtrip_job, get_roundtrip_diff  # noqa: E402
from tests.test_r7_report_roundtrip import R7ReportRoundtripTests  # noqa: E402


def main() -> int:
    fixture = R7ReportRoundtripTests(
        methodName="test_openapi_exposes_roundtrip_routes"
    )
    fixture.setUp()
    try:
        project, revision = fixture._prepare()  # noqa: SLF001 - acceptance fixture
        job = report_exports.create_export_job(
            project["project_uuid"],
            ReportExportJobWrite(
                mode="draft",
                version="V1.0",
                expected_project_revision=revision,
                roundtrip_capable=True,
            ),
        )
        report_exports.process_export_job(job["job_uuid"])
        exported = report_exports.get_export_job(job["job_uuid"])
        if exported["status"] != "succeeded":
            raise RuntimeError(
                f"Word export failed: {exported.get('error_code') or 'unknown'}"
            )
        output = report_exports.export_docx_path(job["job_uuid"])
        imported = create_roundtrip_job(
            project["project_uuid"],
            UploadFile(file=io.BytesIO(output.read_bytes()), filename="word-saved.docx"),
        )
        if imported["status"] != "ready_to_commit":
            raise RuntimeError(
                f"Word-saved upload failed: {imported.get('error_code') or imported['status']}"
            )
        diff = get_roundtrip_diff(imported["id"])
        summary = diff["summary"]
        if int(summary.get("conflicts") or 0) != 0:
            raise RuntimeError("Word-saved unchanged draft unexpectedly contains conflicts")
        result = {
            "status": "succeeded",
            "word_refresh_status": exported["word_refresh_status"],
            "page_count": exported["page_count"],
            "roundtrip_status": imported["status"],
            "diff_total": summary.get("total", len(diff["items"])),
            "unchanged": summary.get("unchanged", 0),
            "apply_word": summary.get("apply_word", 0),
            "conflicts": summary.get("conflicts", 0),
            "ignored_changes": len(diff.get("ignored_changes") or []),
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        fixture.tearDown()


if __name__ == "__main__":
    raise SystemExit(main())
