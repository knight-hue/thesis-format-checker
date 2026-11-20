# -*- coding: utf-8 -*-
"""P1-2b：术语 gate 接入 checker 后的三态行为。

验证：
- gate mismatch → issues，decision=confirmed，source=rule_engine；
- 词表未收录 + LLM mismatch → advisory_issues，decision=suggested；
- 无 LLM / LLM unknown → pending_review；
- pending 不计入 total_issues / passed；
- 四范围独立、strict、B04 透传；
- pending 条目能通过 CheckResponse 序列化。
"""
import unittest
from unittest.mock import Mock

from fastapi.testclient import TestClient

from backend import api
from backend.api import CheckResponse, _annotate_result_issues
from backend.detection.checker_service import (
    MAX_PENDING_REVIEW,
    _cap_pending_review,
    check_document,
)
from backend.detection.issue_compaction import compact_check_result


def doc(*blocks):
    return {
        "schema_version": "1.0",
        "sections": [],
        "blocks": [
            {"type": "paragraph", "index": index, "kind": kind, "text": text}
            for index, (kind, text) in enumerate(blocks)
        ],
    }


def terminology(result, key):
    return [item for item in result.get(key) or []
            if item.get("rule_id") == "terminology"]


def rag_returning(payload):
    rag = Mock(documents_allowed=True, llm_model="test")
    rag.judge_compliance_mixed_batch.return_value = [payload]
    return rag


class ConfirmedTerminologyTest(unittest.TestCase):
    def test_gate_mismatch_becomes_confirmed_issue(self):
        result = check_document(
            doc(("body", "CNN模型具有很强的拟合能力。")),
            "jlu_se", rag_service=None)
        items = terminology(result, "issues")
        self.assertEqual(1, len(items))
        item = items[0]
        self.assertEqual("confirmed", item["decision"])
        self.assertEqual("rule_engine", item["source"])
        self.assertEqual("body", item["evidence"]["scope"])
        self.assertTrue(item["evidence"]["first_occurrence"])
        self.assertEqual("first_occurrence_no_gloss",
                         item["evidence"]["reason_code"])
        self.assertEqual(1, result["breakdown"]["semantic"]["confirmed_count"])
        self.assertEqual(0, result["breakdown"]["semantic"]["pending_review_count"])

    def test_scope_is_independent_through_checker(self):
        result = check_document(
            doc(("en_abstract_body",
                 "Convolutional Neural Network (CNN) is used."),
                ("body", "CNN模型具有很强的拟合能力。")),
            "jlu_se", rag_service=None)
        items = terminology(result, "issues")
        self.assertEqual(1, len(items))
        self.assertEqual("body", items[0]["evidence"]["scope"])

    def test_strict_mode_turns_ai_into_confirmed(self):
        document = doc(("body", "AI技术正在快速发展。"))
        default = check_document(document, "jlu_se", rag_service=None)
        strict = check_document(
            document, "jlu_se", rag_service=None, terminology_strict=True)
        self.assertEqual([], terminology(default, "issues"))
        self.assertEqual(1, len(terminology(strict, "issues")))
        self.assertEqual("confirmed",
                         terminology(strict, "issues")[0]["decision"])

    def test_b04_can_be_enabled_per_request(self):
        document = doc(
            ("title", "基于心电信号的疾病诊断"),
            ("keyword_body", "心电；临床；诊断"),
            ("abstract_body", "本文研究心电、临床诊断与患者数据。"),
            ("body", "ECG信号用于诊断。"),
        )
        default = check_document(document, "jlu_se", rag_service=None)
        enabled = check_document(
            document, "jlu_se", rag_service=None,
            terminology_b04_domain_exempt=True)
        self.assertTrue(terminology(default, "issues"))
        self.assertEqual([], terminology(enabled, "issues"))


class PendingAndSuggestedTest(unittest.TestCase):
    def test_unlisted_without_llm_goes_pending(self):
        result = check_document(
            doc(("body", "XYZ模型具有很强的拟合能力。")),
            "jlu_se", rag_service=None)
        pending = terminology(result, "pending_review")
        self.assertEqual(1, len(pending))
        item = pending[0]
        self.assertEqual("pending_review", item["decision"])
        self.assertEqual("rule_engine", item["source"])
        self.assertIs(False, item["fixable"])
        self.assertEqual(1, item["occurrence_count"])
        self.assertTrue(item["location_samples"])
        self.assertEqual("model_guess_unlisted_term",
                         item["evidence"]["reason_code"])
        self.assertEqual([], terminology(result, "issues"))
        self.assertEqual(1, result["breakdown"]["semantic"]["pending_review_count"])

    def test_llm_mismatch_becomes_suggested_advisory(self):
        rag = rag_returning({
            "status": "mismatch", "compliant": False,
            "conclusion": "未解释", "basis": "b", "suggestion": "s",
        })
        result = check_document(
            doc(("body", "XYZ模型具有很强的拟合能力。")),
            "jlu_se", rag_service=rag)
        advisory = terminology(result, "advisory_issues")
        self.assertEqual(1, len(advisory))
        item = advisory[0]
        self.assertEqual("suggested", item["decision"])
        self.assertEqual("rag", item["source"])
        self.assertIsNone(item["confidence"])
        self.assertEqual([], terminology(result, "issues"))
        self.assertEqual([], terminology(result, "pending_review"))
        self.assertEqual(0, result["breakdown"]["semantic"]["confirmed_count"])

    def test_llm_unknown_goes_pending(self):
        rag = rag_returning({
            "status": "unknown", "compliant": None,
            "conclusion": "无法判断", "basis": "", "suggestion": "",
        })
        result = check_document(
            doc(("body", "XYZ模型具有很强的拟合能力。")),
            "jlu_se", rag_service=rag)
        self.assertEqual([], terminology(result, "issues"))
        self.assertEqual([], terminology(result, "advisory_issues"))
        self.assertEqual(1, len(terminology(result, "pending_review")))


class PendingCapTest(unittest.TestCase):
    def test_pending_review_is_capped(self):
        items = list(range(MAX_PENDING_REVIEW + 7))
        kept, dropped = _cap_pending_review(items)
        self.assertEqual(MAX_PENDING_REVIEW, len(kept))
        self.assertEqual(7, dropped)


class ResponseContractTest(unittest.TestCase):
    def test_pending_items_validate_check_response(self):
        result = check_document(
            doc(("body", "XYZ模型具有很强的拟合能力。")),
            "jlu_se", rag_service=None)
        _annotate_result_issues(result, llm_available=False)
        compacted = compact_check_result(
            result, max_returned=100, max_location_samples=10)
        response = CheckResponse.model_validate(compacted)
        self.assertEqual(0, response.total_issues)
        self.assertTrue(response.passed)
        self.assertEqual(1, len(response.pending_review))
        item = response.pending_review[0]
        self.assertEqual("pending_review", item.decision)
        self.assertIs(False, item.fixable)
        self.assertEqual(1, item.occurrence_count)
        self.assertTrue(item.location_samples)
        self.assertEqual(1, response.breakdown.semantic.pending_review_count)
        self.assertEqual(0, response.breakdown.semantic.confirmed_count)

    def test_confirmed_items_validate_check_response(self):
        result = check_document(
            doc(("body", "CNN模型具有很强的拟合能力。")),
            "jlu_se", rag_service=None)
        _annotate_result_issues(result, llm_available=False)
        compacted = compact_check_result(
            result, max_returned=100, max_location_samples=10)
        response = CheckResponse.model_validate(compacted)
        self.assertEqual(1, response.total_issues)
        self.assertEqual([], response.pending_review)
        self.assertEqual(1, response.breakdown.semantic.confirmed_count)
        self.assertEqual("confirmed", response.issues[0].decision)

    def test_suggested_is_not_counted_in_total_issues(self):
        rag = rag_returning({
            "status": "mismatch", "compliant": False,
            "conclusion": "未解释", "basis": "b", "suggestion": "s",
        })
        result = check_document(
            doc(("body", "XYZ模型具有很强的拟合能力。")),
            "jlu_se", rag_service=rag)
        _annotate_result_issues(result, llm_available=True)
        compacted = compact_check_result(
            result, max_returned=100, max_location_samples=10)
        response = CheckResponse.model_validate(compacted)
        self.assertEqual(0, response.total_issues)
        self.assertEqual(1, len(response.advisory_issues))
        self.assertEqual("suggested", response.advisory_issues[0].decision)
        self.assertEqual(0, response.breakdown.semantic.confirmed_count)


class ApiCheckFlagsTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(api.app)

    def tearDown(self):
        self.client.close()

    def _check(self, text, **flags):
        payload = {
            "college_id": "jlu_se",
            "include_semantic": True,
            "document": doc(("body", text)),
        }
        payload.update(flags)
        return self.client.post("/api/check", json=payload)

    def test_check_endpoint_passes_strict_flag(self):
        default = self._check("AI技术正在快速发展。")
        strict = self._check("AI技术正在快速发展。", terminology_strict=True)
        self.assertEqual(200, default.status_code, default.text)
        self.assertEqual(200, strict.status_code, strict.text)
        self.assertEqual(
            [], [item for item in default.json()["issues"]
                 if item["rule_id"] == "terminology"])
        strict_terms = [item for item in strict.json()["issues"]
                        if item["rule_id"] == "terminology"]
        self.assertEqual(1, len(strict_terms))
        self.assertEqual("confirmed", strict_terms[0]["decision"])

    def test_check_endpoint_passes_b04_flag(self):
        document = {
            "schema_version": "1.0",
            "sections": [],
            "blocks": [
                {"type": "paragraph", "index": 0, "kind": "title",
                 "text": "基于心电信号的疾病诊断"},
                {"type": "paragraph", "index": 1, "kind": "keyword_body",
                 "text": "心电；临床；诊断"},
                {"type": "paragraph", "index": 2, "kind": "abstract_body",
                 "text": "本文研究心电、临床诊断与患者数据。"},
                {"type": "paragraph", "index": 3, "kind": "body",
                 "text": "ECG信号用于诊断。"},
            ],
        }
        payload = {"college_id": "jlu_se", "include_semantic": True,
                   "document": document,
                   "terminology_b04_domain_exempt": True}
        response = self.client.post("/api/check", json=payload)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(
            [], [item for item in response.json()["issues"]
                 if item["rule_id"] == "terminology"])


if __name__ == "__main__":
    unittest.main()
