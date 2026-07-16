from __future__ import annotations

import unittest

from app.main import app


class R2ReportApiContractTests(unittest.TestCase):
    def test_all_mutable_report_delete_routes_require_revision_query(self) -> None:
        schema = app.openapi()
        delete_paths = (
            "/api/projects/{project_uuid}/report/organizations/{organization_uuid}",
            "/api/projects/{project_uuid}/report/members/{member_uuid}",
            "/api/projects/{project_uuid}/report/crypto-products/{product_uuid}",
            "/api/projects/{project_uuid}/report/standards/{standard_uuid}",
            "/api/projects/{project_uuid}/report/special-indicators/{indicator_uuid}",
            "/api/projects/{project_uuid}/report/objects/{object_uuid}",
            "/api/projects/{project_uuid}/report/object-relations/{relation_uuid}",
            "/api/projects/{project_uuid}/report/result-correction-relations/{correction_uuid}",
            "/api/projects/{project_uuid}/report/blocks/{block_uuid}",
        )
        for path in delete_paths:
            with self.subTest(path=path):
                parameters = schema["paths"][path]["delete"]["parameters"]
                expected_revision = next(item for item in parameters if item["name"] == "expected_revision")
                self.assertEqual(expected_revision["in"], "query")
                self.assertTrue(expected_revision["required"])
                if_match = next(item for item in parameters if item["name"] == "if-match")
                self.assertEqual(if_match["in"], "header")


if __name__ == "__main__":
    unittest.main()
