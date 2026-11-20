# -*- coding: utf-8 -*-
"""三态契约字段：只落字段与序列化，不改变检测判定。

本文件锁住 P1-1 的边界：

- ``issues`` 默认 ``confirmed``，``advisory_issues`` 显式 ``suggested``，
  ``pending_review`` 默认 ``pending_review``；
- 已有 ``decision`` 不被覆盖，给后续运行时判定留出写入空间；
- ``pending_review`` 不计入 ``total_issues``；
- 请求开关与 breakdown 计数有稳定默认值。
"""
import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from backend import api
from backend.api import (
    CheckRequest,
    CheckResponse,
    IssueEvidence,
    IssueResponse,
    SemanticBreakdownResponse,
    _annotate_result_issues,
)
from backend.detection.issue_compaction import compact_check_result


def issue_dict(rule_id="terminology", source="rag", **extra):
    data = {
        "rule_id": rule_id,
        "category": "名词术语",
        "severity": "warning",
        "location": {"type": "document", "index": 1, "kinds": ["body"]},
        "actual": "CNN",
        "expected": "外文缩写首次出现时在括号内注明含义",
        "message": "名词术语或外文缩写使用不符合要求",
        "source": source,
    }
    data.update(extra)
    return data


def issue_model(**extra):
    data = issue_dict(**extra)
    return IssueResponse(
        **data,
        fixable=False,
        occurrence_count=1,
        location_samples=[{"type": "document", "index": 1}],
    )


class ThreeStateModelTest(unittest.TestCase):
    def test_check_request_flags_default_off_and_accept_true(self):
        base = {"college_id": "jlu_se",
                "document": {"sections": [], "blocks": []}}
        req = CheckRequest(**base)
        self.assertFalse(req.terminology_strict)
        self.assertFalse(req.terminology_b04_domain_exempt)

        req = CheckRequest(**base, terminology_strict=True,
                           terminology_b04_domain_exempt=True)
        self.assertTrue(req.terminology_strict)
        self.assertTrue(req.terminology_b04_domain_exempt)

    def test_issue_defaults_to_confirmed_without_evidence(self):
        issue = issue_model()
        self.assertEqual("confirmed", issue.decision)
        self.assertIsNone(issue.confidence)
        self.assertIsNone(issue.evidence)

    def test_issue_accepts_evidence_and_confidence(self):
        issue = issue_model(
            decision="suggested",
            confidence=0.62,
            evidence={
                "scope": "body",
                "first_occurrence": True,
                "earlier_occurrences": [
                    {"index": 1, "kind": "body", "excerpt": "更早出现"}
                ],
                "rule_hits": ["gloss_missing"],
                "reason_code": "model_guess_unlisted_term",
            },
        )
        self.assertEqual("suggested", issue.decision)
        self.assertAlmostEqual(0.62, issue.confidence)
        self.assertEqual("body", issue.evidence.scope)
        self.assertEqual("model_guess_unlisted_term",
                         issue.evidence.reason_code)

    def test_evidence_rejects_invalid_enum_and_limits(self):
        with self.assertRaises(ValidationError):
            IssueEvidence(scope="preface")
        with self.assertRaises(ValidationError):
            IssueEvidence(reason_code="other")
        with self.assertRaises(ValidationError):
            IssueEvidence(earlier_occurrences=[
                {"index": i, "kind": "body", "excerpt": "x"} for i in range(4)
            ])
        with self.assertRaises(ValidationError):
            issue_model(confidence=1.5)

    def test_check_response_defaults_pending_review_empty(self):
        response = CheckResponse(
            schema_version="1.0",
            college_id="jlu_se",
            passed=True,
            total_issues=0,
            grouped_issues=0,
            returned_issues=0,
            issues_truncated=False,
            issue_summary=[],
            issues=[],
            breakdown={
                "rule_engine": {"passed": True, "total_issues": 0},
                "semantic": {
                    "enabled": True,
                    "llm_enabled": False,
                    "passed": None,
                    "total_issues": 0,
                    "llm_calls": 0,
                    "cache_hits": 0,
                    "prefilter_skips": 0,
                },
            },
        )
        self.assertEqual([], response.pending_review)
        self.assertEqual([], response.advisory_issues)
        self.assertEqual([], response.unchecked)
        self.assertEqual(0, response.breakdown.semantic.confirmed_count)
        self.assertEqual(0, response.breakdown.semantic.pending_review_count)

    def test_semantic_breakdown_counts_default_zero(self):
        breakdown = SemanticBreakdownResponse(
            enabled=True, llm_enabled=False, passed=None, total_issues=0,
            llm_calls=0, cache_hits=0, prefilter_skips=0,
        )
        self.assertEqual(0, breakdown.confirmed_count)
        self.assertEqual(0, breakdown.pending_review_count)


class ThreeStateAnnotationTest(unittest.TestCase):
    def test_decision_by_bucket(self):
        result = {
            "issues": [issue_dict(rule_id="body.font_size", source="rule_engine")],
            "advisory_issues": [issue_dict()],
            "pending_review": [issue_dict(decision="pending_review")],
        }
        _annotate_result_issues(result, llm_available=False)
        self.assertEqual("confirmed", result["issues"][0]["decision"])
        self.assertEqual("suggested", result["advisory_issues"][0]["decision"])
        self.assertEqual("pending_review", result["pending_review"][0]["decision"])
        self.assertIn("fixable", result["issues"][0])
        self.assertIn("fixable", result["advisory_issues"][0])
        self.assertIn("fixable", result["pending_review"][0])

    def test_existing_decision_is_not_overwritten(self):
        result = {
            "issues": [issue_dict(decision="suggested")],
            "advisory_issues": [],
            "pending_review": [],
        }
        _annotate_result_issues(result, llm_available=False)
        self.assertEqual("suggested", result["issues"][0]["decision"])

    def test_pending_review_is_not_counted_in_total_issues(self):
        pending = issue_dict(decision="pending_review", fixable=False,
                             occurrence_count=1,
                             location_samples=[{"type": "document"}])
        result = {
            "issues": [],
            "advisory_issues": [],
            "pending_review": [pending],
            "passed": True,
            "breakdown": {"rule_engine": {"passed": True, "total_issues": 0},
                          "semantic": {"enabled": True, "llm_enabled": False,
                                       "passed": None, "total_issues": 0,
                                       "llm_calls": 0, "cache_hits": 0,
                                       "prefilter_skips": 0}},
        }
        compacted = compact_check_result(result, max_returned=100,
                                         max_location_samples=10)
        self.assertEqual(0, compacted["total_issues"])
        self.assertEqual([], compacted["issues"])
        self.assertEqual(1, len(compacted["pending_review"]))
        self.assertEqual("pending_review",
                         compacted["pending_review"][0]["decision"])

    def test_compact_result_always_has_pending_review_array(self):
        compacted = compact_check_result({"issues": []}, max_returned=100,
                                         max_location_samples=10)
        self.assertEqual([], compacted["pending_review"])


class OpenAPISnapshotContractTest(unittest.TestCase):
    def test_snapshot_contains_three_state_fields(self):
        spec = json.loads(
            (Path(__file__).resolve().parents[1] / "docs" / "openapi.json")
            .read_text(encoding="utf-8")
        )
        schemas = spec["components"]["schemas"]
        self.assertIn("pending_review", schemas["CheckResponse"]["properties"])
        self.assertIn("decision", schemas["IssueResponse"]["properties"])
        self.assertIn("evidence", schemas["IssueResponse"]["properties"])
        self.assertIn("confirmed_count",
                      schemas["SemanticBreakdownResponse"]["properties"])
        self.assertIn("terminology_strict",
                      schemas["CheckRequest"]["properties"])


if __name__ == "__main__":
    unittest.main()
