import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi.testclient import TestClient

import backend.api as api


DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _zip_payload(parts):
    payload = io.BytesIO()
    with ZipFile(payload, "w", compression=ZIP_DEFLATED) as package:
        for name, content in parts.items():
            package.writestr(name, content)
    return payload.getvalue()


class ApiUploadValidationTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(api.app)

    def tearDown(self):
        self.client.close()

    def _check(self, payload, filename="paper.docx"):
        return self.client.post(
            "/api/check-file",
            data={"college_id": "jlu_cs", "include_semantic": "false"},
            files={"file": (filename, payload, DOCX_CONTENT_TYPE)},
        )

    def test_rejects_empty_upload(self):
        response = self._check(b"")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "上传文件不能为空")

    def test_rejects_wrong_extension_before_reading_payload(self):
        response = self._check(b"not-a-document", filename="paper.pdf")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "仅支持 .docx 文件")

    def test_rejects_random_bytes_as_client_error(self):
        response = self._check(b"not-a-zip-package")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "文件不是有效的 DOCX 文档")

    def test_rejects_zip_without_required_ooxml_parts(self):
        response = self._check(_zip_payload({"word/other.xml": b"<other/>"}))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "文件不是有效的 DOCX 文档")

    def test_rejects_compressed_upload_over_byte_limit(self):
        with patch.object(api, "MAX_UPLOAD_BYTES", 8):
            response = self._check(b"123456789")
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["detail"], "上传文件不能超过 1 MB")

    def test_rejects_excessive_zip_entry_count(self):
        payload = _zip_payload({
            "[Content_Types].xml": b"<Types/>",
            "_rels/.rels": b"<Relationships/>",
            "word/document.xml": b"<document/>",
        })
        with patch.object(api, "MAX_DOCX_ENTRIES", 2):
            response = self._check(payload)
        self.assertEqual(response.status_code, 413)
        self.assertIn("内部文件数量", response.json()["detail"])

    def test_rejects_excessive_uncompressed_size(self):
        payload = _zip_payload({
            "[Content_Types].xml": b"<Types/>",
            "_rels/.rels": b"<Relationships/>",
            "word/document.xml": b"x" * 100,
        })
        with patch.object(api, "MAX_DOCX_UNCOMPRESSED_BYTES", 50):
            response = self._check(payload)
        self.assertEqual(response.status_code, 413)
        self.assertIn("解压后内容", response.json()["detail"])

    def test_failed_upload_removes_request_temp_directory(self):
        with tempfile.TemporaryDirectory() as parent:
            request_dir = Path(parent) / "request"
            request_dir.mkdir()
            with patch.object(api.tempfile, "mkdtemp", return_value=str(request_dir)):
                response = self._check(b"broken-docx")
            self.assertEqual(response.status_code, 400)
            self.assertFalse(request_dir.exists())

    def test_unknown_college_is_bad_request_not_not_found(self):
        """未知学院是请求参数问题（400），404 留给内部文件缺失。"""
        from docx import Document

        document = Document()
        document.add_paragraph("吉林大学本科毕业论文（设计）")
        payload = io.BytesIO()
        document.save(payload)

        response = self.client.post(
            "/api/check-file",
            data={"college_id": "no_such_college", "include_semantic": "false"},
            files={"file": ("paper.docx", payload.getvalue(), DOCX_CONTENT_TYPE)},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("未知学院", response.json()["detail"])

    def test_missing_internal_file_is_404_without_leaking_path(self):
        internal_path = r"C:\secret\rules\paper_rules.json"
        with patch.object(api, "docx_to_json", side_effect=FileNotFoundError(internal_path)):
            response = self._check(_zip_payload({
                "[Content_Types].xml": b"<Types/>",
                "_rels/.rels": b"<Relationships/>",
                "word/document.xml": b"<document/>",
            }))
        self.assertEqual(response.status_code, 404)
        detail = response.json()["detail"]
        self.assertNotIn("secret", detail)
        self.assertNotIn(".json", detail)
        self.assertIn("内部文件缺失", detail)


if __name__ == "__main__":
    unittest.main()
