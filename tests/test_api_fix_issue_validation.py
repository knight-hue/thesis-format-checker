import json
import unittest

from fastapi import HTTPException
from fastapi.testclient import TestClient

import backend.api as api


class FixIssuesJsonValidationTests(unittest.TestCase):
    def test_valid_rule_engine_issue_keeps_extension_fields(self):
        issues = api._parse_fix_issues(json.dumps([{
            "rule_id": "table.caption_number",
            "source": "rule_engine",
            "location": {"type": "table", "index": 1},
            "caption_style": {"alignment": "center"},
        }]))

        self.assertEqual(issues[0]["location"]["index"], 1)
        self.assertEqual(issues[0]["caption_style"], {"alignment": "center"})

    def test_valid_semantic_issue_accepts_document_location(self):
        issues = api._parse_fix_issues(json.dumps([{
            "rule_id": "units",
            "source": "rag",
            "location": {"type": "document", "kinds": ["body"]},
            "blocks": [{"index": 0, "text": "一公里"}],
        }]))

        self.assertNotIn("index", issues[0]["location"])
        self.assertEqual(issues[0]["blocks"][0]["index"], 0)

    def test_rejects_missing_nested_coordinates(self):
        with self.assertRaises(HTTPException) as caught:
            api._parse_fix_issues(json.dumps([{
                "rule_id": "table.cell_font_size",
                "location": {"type": "table_cell", "index": 0, "row": 0},
            }]))

        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("row 和 col", caught.exception.detail)

    def test_rejects_coerced_index_type(self):
        with self.assertRaises(HTTPException) as caught:
            api._parse_fix_issues(json.dumps([{
                "rule_id": "body.font_size",
                "location": {"type": "paragraph", "index": "0"},
            }]))

        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("0.location.index", caught.exception.detail)

    def test_rejects_image_bytes_inside_json(self):
        with self.assertRaises(HTTPException) as caught:
            api._parse_fix_issues(json.dumps([{
                "rule_id": "image.replace",
                "location": {"type": "image", "index": 0},
                "image_bytes": "not-binary",
            }]))

        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("images 文件字段", caught.exception.detail)

    def test_endpoint_rejects_malformed_issue_before_parsing_docx(self):
        client = TestClient(api.app)
        try:
            response = client.post(
                "/api/fix",
                data={"college_id": "jlu_cs", "issues_json": json.dumps([{
                    "rule_id": "body.font_size",
                    "location": {"type": "paragraph", "index": -1},
                }])},
                files={"file": ("paper.docx", b"not needed", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            )
        finally:
            client.close()

        self.assertEqual(response.status_code, 400)
        self.assertIn("issues_json 校验失败", response.json()["detail"])


class FixIssueTargetValidationTests(unittest.TestCase):
    def setUp(self):
        self.document = {
            "sections": [{"index": 0}],
            "blocks": [
                {"type": "paragraph", "index": 0},
                {"type": "table", "index": 1,
                 "cells": [{"row": 0, "col": 0, "text": "cell"}]},
                {"type": "image", "index": 2},
            ],
        }

    def test_accepts_existing_table_cell(self):
        issues = api._parse_fix_issues(json.dumps([{
            "rule_id": "table.cell_font_size",
            "location": {"type": "table_cell", "index": 1, "row": 0, "col": 0},
        }]))

        api._validate_issue_targets(issues, self.document)

    def test_rejects_location_pointing_to_wrong_block_type(self):
        issues = api._parse_fix_issues(json.dumps([{
            "rule_id": "image.replace",
            "location": {"type": "image", "index": 0},
        }]))

        with self.assertRaises(HTTPException) as caught:
            api._validate_issue_targets(issues, self.document)

        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("未指向 image", caught.exception.detail)

    def test_rejects_missing_semantic_block(self):
        issues = api._parse_fix_issues(json.dumps([{
            "rule_id": "units",
            "source": "rag",
            "location": {"type": "document"},
            "blocks": [{"index": 9, "text": "missing"}],
        }]))

        with self.assertRaises(HTTPException) as caught:
            api._validate_issue_targets(issues, self.document)

        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("blocks[0].index", caught.exception.detail)


if __name__ == "__main__":
    unittest.main()
