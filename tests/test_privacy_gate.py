# -*- coding: utf-8 -*-
"""隐私硬闸：论文内容只允许发给本机模型端点。"""
import os
import unittest
from unittest import mock

from fastapi import HTTPException

from backend import api
from backend.rag.llm_client import documents_may_leave_machine, is_local_endpoint


class EndpointTest(unittest.TestCase):
    def test_local_endpoints_are_recognised(self):
        for url in ("http://localhost:11434/v1", "http://127.0.0.1:8001/v1",
                    "http://127.1.2.3:9000", "http://[::1]:8000",
                    "http://host.docker.internal:11434/v1",
                    "http://ollama.local:11434"):
            self.assertTrue(is_local_endpoint(url), url)

    def test_cloud_endpoints_are_not_local(self):
        for url in ("https://api.deepseek.com", "https://example.com/v1",
                    "http://192.168.1.20:8000", ""):
            self.assertFalse(is_local_endpoint(url), url)

    def test_cloud_is_blocked_without_explicit_opt_in(self):
        with mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch("backend.rag.llm_client.ALLOW_CLOUD_LLM_FOR_DOCUMENTS", False):
            os.environ.pop("THESIS_ALLOW_CLOUD_LLM_FOR_DOCUMENTS", None)
            self.assertFalse(documents_may_leave_machine("https://api.deepseek.com"))
            self.assertTrue(documents_may_leave_machine("http://127.0.0.1:11434/v1"))

    def test_explicit_opt_in_allows_cloud(self):
        with mock.patch.dict(os.environ,
                             {"THESIS_ALLOW_CLOUD_LLM_FOR_DOCUMENTS": "1"}):
            self.assertTrue(documents_may_leave_machine("https://api.deepseek.com"))

    def test_local_settings_switch_allows_cloud(self):
        """演示期开关（local_settings.ALLOW_CLOUD_LLM_FOR_DOCUMENTS）也放行。"""
        with mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch("backend.rag.llm_client.ALLOW_CLOUD_LLM_FOR_DOCUMENTS", True):
            os.environ.pop("THESIS_ALLOW_CLOUD_LLM_FOR_DOCUMENTS", None)
            self.assertTrue(documents_may_leave_machine("https://api.deepseek.com"))

    def test_env_zero_overrides_local_settings_switch(self):
        """环境变量 0 优先于本地开关，可临时关回。"""
        with mock.patch.dict(os.environ,
                             {"THESIS_ALLOW_CLOUD_LLM_FOR_DOCUMENTS": "0"}), \
                mock.patch("backend.rag.llm_client.ALLOW_CLOUD_LLM_FOR_DOCUMENTS", False):
            self.assertFalse(documents_may_leave_machine("https://api.deepseek.com"))


class DetectionGateTest(unittest.TestCase):
    def test_cloud_endpoint_disables_semantic_detection(self):
        fake = type("FakeLLM", (), {"documents_allowed": False})()
        with mock.patch.object(api, "llm_service", fake):
            self.assertIsNone(api._detection_service())
            with self.assertRaises(HTTPException) as caught:
                api._require_detection_service()
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("不能把论文内容发往外部服务", caught.exception.detail)

    def test_local_endpoint_keeps_semantic_detection(self):
        fake = type("FakeLLM", (), {"documents_allowed": True})()
        with mock.patch.object(api, "llm_service", fake):
            self.assertIs(api._detection_service(), fake)
            self.assertIs(api._require_detection_service(), fake)


if __name__ == "__main__":
    unittest.main()
