import json
import logging
import unittest

from fastapi.testclient import TestClient

import backend.api as api


class CaptureLogs(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record.getMessage())


class ApiAccessLogTests(unittest.TestCase):
    def setUp(self):
        self.capture = CaptureLogs()
        api.access_logger.addHandler(self.capture)
        self.client = TestClient(api.app)

    def tearDown(self):
        self.client.close()
        api.access_logger.removeHandler(self.capture)

    def last_record(self):
        self.assertEqual(len(self.capture.records), 1)
        return json.loads(self.capture.records[0])

    def test_success_uses_route_template_without_query(self):
        response = self.client.get("/api/health?private_token=secret-value")
        self.assertEqual(response.status_code, 200)
        record = self.last_record()
        self.assertEqual(record["route"], "/api/health")
        self.assertEqual(record["status"], 200)
        self.assertEqual(record["method"], "GET")
        self.assertGreaterEqual(record["duration_ms"], 0)
        self.assertEqual(len(record["request_id"]), 32)
        self.assertNotIn("secret-value", self.capture.records[0])

    def test_validation_error_logged_without_body(self):
        response = self.client.post("/api/check", json={"private": "secret-value"})
        self.assertEqual(response.status_code, 422)
        record = self.last_record()
        self.assertEqual(record["route"], "/api/check")
        self.assertEqual(record["status"], 422)
        self.assertNotIn("secret-value", self.capture.records[0])

    def test_unmatched_path_is_not_logged(self):
        response = self.client.get("/secret-value")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.last_record()["route"], "<unmatched>")
        self.assertNotIn("secret-value", self.capture.records[0])

    def test_bad_upload_logs_400_without_filename(self):
        response = self.client.post(
            "/api/check-file",
            data={"college_id": "jlu_cs", "include_semantic": "false"},
            files={"file": ("secret-value.txt", b"test", "text/plain")},
        )
        self.assertEqual(response.status_code, 400)
        record = self.last_record()
        self.assertEqual(record["route"], "/api/check-file")
        self.assertEqual(record["status"], 400)
        self.assertNotIn("secret-value", self.capture.records[0])

    def test_unavailable_rag_logs_503_without_question(self):
        previous = api.rag_service
        api.rag_service = None
        try:
            response = self.client.post(
                "/api/rag/answer",
                json={"college_id": "jlu_cs", "question": "secret-value"},
            )
        finally:
            api.rag_service = previous
        self.assertEqual(response.status_code, 503)
        record = self.last_record()
        self.assertEqual(record["route"], "/api/rag/answer")
        self.assertEqual(record["status"], 503)
        self.assertNotIn("secret-value", self.capture.records[0])


if __name__ == "__main__":
    unittest.main()
