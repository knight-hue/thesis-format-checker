import io
import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from docx import Document
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

import backend.api as api
from backend.document_contract import DocumentContract
from backend.parser import docx_to_json
from tests.test_real_docx_e2e import DOCX_CONTENT_TYPE, _build_realistic_paper


class ApiCoreSmokeEndToEndTests(unittest.TestCase):
    """不依赖在线模型或本地知识库的核心 HTTP 闭环。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp.name)
        self.source = self.temp_path / "smoke-paper.docx"
        _build_realistic_paper(self.source)
        self.source_bytes = self.source.read_bytes()
        self.client = TestClient(api.app)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def _check_file(self, payload):
        return self.client.post(
            "/api/check-file",
            data={"college_id": "jlu_cs", "include_semantic": "false"},
            files={"file": ("paper.docx", payload, DOCX_CONTENT_TYPE)},
        )

    def _parse_source_contract(self):
        output_dir = self.temp_path / "parsed"
        json_path = docx_to_json(str(self.source), output_dir=str(output_dir))
        with open(json_path, encoding="utf-8") as stream:
            document = json.load(stream)
        DocumentContract.model_validate(document)
        return document

    @staticmethod
    def _issue_identity(issue):
        location = issue["location"]
        return (
            issue["rule_id"],
            location.get("type"),
            location.get("index"),
            location.get("run"),
            location.get("row"),
            location.get("col"),
        )

    def test_offline_core_api_flow(self):
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200, health.text)
        health_result = api.HealthResponse.model_validate(health.json())
        self.assertEqual(health_result.status, "healthy")

        colleges = self.client.get("/api/colleges")
        self.assertEqual(colleges.status_code, 200, colleges.text)
        college_result = TypeAdapter(list[api.CollegeInfo]).validate_python(
            colleges.json()
        )
        self.assertIn("jlu_cs", {item.college_id for item in college_result})

        file_check = self._check_file(self.source_bytes)
        self.assertEqual(file_check.status_code, 200, file_check.text)
        file_result = api.CheckResponse.model_validate(file_check.json())
        self.assertEqual(file_result.schema_version, "1.0")
        self.assertGreater(file_result.total_issues, 0)
        # 三态契约：本次未跑语义，不会出现待人工项；正式问题默认 confirmed。
        self.assertEqual([], file_result.pending_review)
        self.assertTrue(all(issue.decision == "confirmed"
                            for issue in file_result.issues))

        document = self._parse_source_contract()
        contract_check = self.client.post(
            "/api/check",
            json={
                "college_id": "jlu_cs",
                "include_semantic": False,
                "document": document,
            },
        )
        self.assertEqual(contract_check.status_code, 200, contract_check.text)
        contract_result = api.CheckResponse.model_validate(contract_check.json())
        self.assertEqual(
            {self._issue_identity(issue) for issue in file_check.json()["issues"]},
            {self._issue_identity(issue) for issue in contract_check.json()["issues"]},
        )

        target = next(
            issue
            for issue in file_check.json()["issues"]
            if issue["rule_id"] == "body.font_size" and issue["fixable"]
        )
        fixed = self.client.post(
            "/api/fix",
            data={
                "college_id": "jlu_cs",
                "issues_json": json.dumps([target], ensure_ascii=False),
            },
            files={"file": ("paper.docx", self.source_bytes, DOCX_CONTENT_TYPE)},
        )
        self.assertEqual(fixed.status_code, 200, fixed.text)
        self.assertEqual(fixed.headers["content-type"], DOCX_CONTENT_TYPE)
        self.assertEqual(fixed.headers["X-Applied-Count"], "1")
        self.assertEqual(fixed.headers["X-Skipped-Count"], "0")
        self.assertEqual(fixed.headers["X-Fixed-Indexes"], "0")
        self.assertTrue(Document(io.BytesIO(fixed.content)).paragraphs)
        with ZipFile(io.BytesIO(fixed.content)) as package:
            self.assertIsNone(package.testzip())

        rechecked = self._check_file(fixed.content)
        self.assertEqual(rechecked.status_code, 200, rechecked.text)
        api.CheckResponse.model_validate(rechecked.json())
        self.assertNotIn(
            self._issue_identity(target),
            {self._issue_identity(issue) for issue in rechecked.json()["issues"]},
        )

        rebuilt = self.client.post(
            "/api/rebuild",
            files={"file": ("fixed-paper.docx", fixed.content, DOCX_CONTENT_TYPE)},
        )
        self.assertEqual(rebuilt.status_code, 200, rebuilt.text)
        self.assertEqual(rebuilt.headers["content-type"], DOCX_CONTENT_TYPE)
        self.assertTrue(Document(io.BytesIO(rebuilt.content)).paragraphs)
        with ZipFile(io.BytesIO(rebuilt.content)) as package:
            self.assertIsNone(package.testzip())

        rebuilt_check = self._check_file(rebuilt.content)
        self.assertEqual(rebuilt_check.status_code, 200, rebuilt_check.text)
        api.CheckResponse.model_validate(rebuilt_check.json())


if __name__ == "__main__":
    unittest.main()
