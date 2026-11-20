import json
import unittest

from scripts.export_openapi import DEFAULT_OUTPUT, render_openapi


EXPECTED_PATHS = {
    "/api/check",
    "/api/check-file",
    "/api/check-jobs/create",
    "/api/check-jobs/status",
    "/api/check-jobs/result",
    "/api/colleges",
    "/api/fix",
    "/api/health",
    "/api/kb/build",
    "/api/rag/answer",
    "/api/rag/feedback",
    "/api/rebuild",
    "/api/session/reset",
}

JSON_RESPONSE_MODELS = {
    ("/api/check", "post"): "CheckResponse",
    ("/api/check-file", "post"): "CheckResponse",
    ("/api/check-jobs/status", "post"): "CheckJobStatusResponse",
    ("/api/check-jobs/result", "post"): "CheckJobResultResponse",
    ("/api/health", "get"): "HealthResponse",
    ("/api/kb/build", "post"): "BuildKBResponse",
    ("/api/rag/answer", "post"): "RAGResponse",
    ("/api/rag/feedback", "post"): "ChatFeedbackResponse",
    ("/api/session/reset", "post"): "SessionResetResponse",
}

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class OpenApiSnapshotTests(unittest.TestCase):
    def test_snapshot_matches_application(self):
        self.assertTrue(DEFAULT_OUTPUT.is_file(), f"缺少 OpenAPI 快照：{DEFAULT_OUTPUT}")
        self.assertEqual(DEFAULT_OUTPUT.read_text(encoding="utf-8"), render_openapi())

    def test_snapshot_contains_all_public_api_paths(self):
        schema = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
        self.assertEqual(set(schema["paths"]), EXPECTED_PATHS)

    def test_operation_ids_are_unique(self):
        schema = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
        operation_ids = [
            operation["operationId"]
            for path_item in schema["paths"].values()
            for method, operation in path_item.items()
            if method in {"get", "post", "put", "patch", "delete"}
        ]
        self.assertEqual(len(operation_ids), len(set(operation_ids)))

    def test_json_success_responses_have_explicit_models(self):
        schema = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
        for (path, method), model in JSON_RESPONSE_MODELS.items():
            with self.subTest(path=path, method=method):
                response_schema = schema["paths"][path][method]["responses"]["200"]
                self.assertEqual(
                    response_schema["content"]["application/json"]["schema"]["$ref"],
                    f"#/components/schemas/{model}",
                )

        colleges = schema["paths"]["/api/colleges"]["get"]["responses"]["200"]
        self.assertEqual(
            colleges["content"]["application/json"]["schema"]["items"]["$ref"],
            "#/components/schemas/CollegeInfo",
        )
        accepted = schema["paths"]["/api/check-jobs/create"]["post"]["responses"]["202"]
        self.assertEqual(
            accepted["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/CheckJobAcceptedResponse",
        )

    def test_docx_downloads_are_documented_as_binary(self):
        schema = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
        for path in ("/api/fix", "/api/rebuild"):
            with self.subTest(path=path):
                binary = schema["paths"][path]["post"]["responses"]["200"]["content"]
                self.assertEqual(
                    binary[DOCX_MEDIA_TYPE]["schema"],
                    {"format": "binary", "type": "string"},
                )
        fix_headers = schema["paths"]["/api/fix"]["post"]["responses"]["200"]["headers"]
        self.assertEqual(
            set(fix_headers),
            {
                "X-Applied-Count",
                "X-Fixed-Indexes",
                "X-Llm-Calls",
                "X-Skipped-Count",
                "X-Writeback-Skip-Count",
                "X-Writeback-Warnings",
            },
        )

    def test_declared_http_errors_use_error_response(self):
        schema = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
        declared = 0
        for path_item in schema["paths"].values():
            for method, operation in path_item.items():
                if method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                for status, response in operation["responses"].items():
                    if status not in {"400", "404", "409", "413", "429", "500", "503"}:
                        continue
                    declared += 1
                    self.assertEqual(
                        response["content"]["application/json"]["schema"]["$ref"],
                        "#/components/schemas/ErrorResponse",
                    )
        self.assertGreater(declared, 0)


if __name__ == "__main__":
    unittest.main()
