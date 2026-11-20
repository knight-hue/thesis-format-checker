import io
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from docx import Document
from fastapi.testclient import TestClient

import backend.api as api
from backend.check_jobs import CheckJobStore
from tests.test_real_docx_e2e import DOCX_CONTENT_TYPE, _build_realistic_paper


class ApiAsyncCheckJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp.name)
        self.source = self.temp_path / "async-paper.docx"
        _build_realistic_paper(self.source)
        self.source_bytes = self.source.read_bytes()

        self.old_store = api.check_job_store
        self.old_files_dir = api.CHECK_JOB_FILES_DIR
        self.db_path = self.temp_path / "check_jobs.db"
        api.check_job_store = CheckJobStore(
            str(self.db_path), expire_seconds=3600, max_active=4
        )
        api.CHECK_JOB_FILES_DIR = str(self.temp_path / "files")
        self.client_context = TestClient(api.app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        api.check_job_store = self.old_store
        api.CHECK_JOB_FILES_DIR = self.old_files_dir
        self.temp.cleanup()

    def _create(self, job_id):
        return self.client.post(
            "/api/check-jobs/create",
            data={
                "job_id": str(job_id),
                "college_id": "jlu_cs",
                "include_semantic": "false",
            },
            files={"file": ("paper.docx", self.source_bytes, DOCX_CONTENT_TYPE)},
        )

    @staticmethod
    def _ai_docx_bytes():
        document = Document()
        document.add_paragraph("AI技术正在快速发展。")
        buffer = io.BytesIO()
        document.save(buffer)
        return buffer.getvalue()

    def test_check_file_passes_strict_flag(self):
        response = self.client.post(
            "/api/check-file",
            data={
                "college_id": "jlu_cs",
                "include_semantic": "true",
                "terminology_strict": "true",
            },
            files={"file": ("ai.docx", self._ai_docx_bytes(),
                            DOCX_CONTENT_TYPE)},
        )
        self.assertEqual(200, response.status_code, response.text)
        terms = [item for item in response.json()["issues"]
                 if item["rule_id"] == "terminology"]
        self.assertEqual(1, len(terms))
        self.assertEqual("confirmed", terms[0]["decision"])

    def test_async_job_passes_strict_flag(self):
        job_id = uuid.uuid4()
        accepted = self.client.post(
            "/api/check-jobs/create",
            data={
                "job_id": str(job_id),
                "college_id": "jlu_cs",
                "include_semantic": "true",
                "terminology_strict": "true",
            },
            files={"file": ("ai.docx", self._ai_docx_bytes(),
                            DOCX_CONTENT_TYPE)},
        )
        self.assertEqual(202, accepted.status_code, accepted.text)
        terminal, _ = self._wait_for_terminal(job_id)
        self.assertEqual("succeeded", terminal["status"], terminal)
        result_response = self.client.post(
            "/api/check-jobs/result", json={"job_id": str(job_id)})
        self.assertEqual(200, result_response.status_code, result_response.text)
        result = api.CheckJobResultResponse.model_validate(result_response.json())
        terms = [item for item in result.result.issues
                 if item.rule_id == "terminology"]
        self.assertEqual(1, len(terms))
        self.assertEqual("confirmed", terms[0].decision)

    def _wait_for_terminal(self, job_id, timeout=10):
        deadline = time.monotonic() + timeout
        statuses = []
        while time.monotonic() < deadline:
            response = self.client.post(
                "/api/check-jobs/status", json={"job_id": str(job_id)}
            )
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            statuses.append(payload)
            if payload["status"] in {"succeeded", "failed"}:
                return payload, statuses
            time.sleep(0.05)
        self.fail("异步检测任务未在测试时限内结束")

    def test_async_job_completes_and_result_survives_new_store_instance(self):
        job_id = uuid.uuid4()
        accepted = self._create(job_id)

        self.assertEqual(accepted.status_code, 202, accepted.text)
        accepted_payload = api.CheckJobAcceptedResponse.model_validate(accepted.json())
        self.assertEqual(str(accepted_payload.job_id), str(job_id))
        self.assertEqual(accepted_payload.status, "queued")

        terminal, statuses = self._wait_for_terminal(job_id)
        api.CheckJobStatusResponse.model_validate(terminal)
        self.assertEqual(terminal["status"], "succeeded", terminal)
        self.assertEqual(terminal["progress"], 100)
        self.assertTrue(terminal["result_available"])
        self.assertTrue(statuses)

        result_response = self.client.post(
            "/api/check-jobs/result", json={"job_id": str(job_id)}
        )
        self.assertEqual(result_response.status_code, 200, result_response.text)
        result = api.CheckJobResultResponse.model_validate(result_response.json())
        self.assertEqual(result.status, "succeeded")
        self.assertGreater(result.result.total_issues, 0)

        reopened = CheckJobStore(str(self.db_path), expire_seconds=3600, max_active=4)
        persisted = reopened.get(str(job_id))
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted["status"], "succeeded")
        self.assertEqual(persisted["result"]["total_issues"], result.result.total_issues)

    def test_client_job_id_allows_status_recovery_after_create_response_loss(self):
        job_id = uuid.uuid4()
        accepted = self._create(job_id)
        self.assertEqual(accepted.status_code, 202, accepted.text)

        terminal, _ = self._wait_for_terminal(job_id)
        self.assertEqual(terminal["status"], "succeeded")
        recovered = self.client.post(
            "/api/check-jobs/result", json={"job_id": str(job_id)}
        )
        self.assertEqual(recovered.status_code, 200, recovered.text)
        self.assertEqual(recovered.json()["job_id"], str(job_id))

    def test_duplicate_job_id_and_missing_job_are_explicit(self):
        job_id = uuid.uuid4()
        first = self._create(job_id)
        self.assertEqual(first.status_code, 202, first.text)

        duplicate = self._create(job_id)
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
        self.assertIn("直接查询", duplicate.json()["detail"])

        missing = self.client.post(
            "/api/check-jobs/status", json={"job_id": str(uuid.uuid4())}
        )
        self.assertEqual(missing.status_code, 404, missing.text)

    def test_incomplete_jobs_become_recoverable_failures_after_restart(self):
        job_id = str(uuid.uuid4())
        api.check_job_store.create(job_id)

        restarted = CheckJobStore(str(self.db_path), expire_seconds=3600, max_active=4)
        self.assertEqual(restarted.fail_incomplete(), 1)
        record = restarted.get(job_id)

        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["stage"], "failed")
        self.assertIn("服务已重启", record["message"])


if __name__ == "__main__":
    unittest.main()
