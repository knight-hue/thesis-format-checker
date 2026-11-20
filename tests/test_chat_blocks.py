# -*- coding: utf-8 -*-
"""结构化回答块与反馈存储的回归。"""
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from backend import api
from backend.rag.dialog import blocks
from backend.rag.session_manager import SessionManager

ISSUES = [
    {"rule_id": "body.font_size", "category": "正文格式", "severity": "error",
     "location": {"type": "paragraph", "index": 3}, "message": "正文字号不符合要求",
     "expected": "12 pt", "actual": "10.5 pt", "suggestion": "改为 12 磅"},
    {"rule_id": "caption.number", "category": "图表", "severity": "error",
     "location": {"type": "paragraph", "index": 12}, "message": "图题编号格式不符",
     "expected": "图X-Y", "suggestion": ""},
]


class OverviewBlocksTest(unittest.TestCase):
    def test_summary_and_cards_come_from_issues(self):
        result = blocks.overview_blocks(ISSUES, 2)
        self.assertEqual([item["type"] for item in result], ["summary", "issues"])
        self.assertIn("共检测出 2 条问题", result[0]["text"])
        self.assertIn("正文格式 1 条", result[0]["text"])
        cards = result[1]["issues"]
        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0]["index"], 1)
        self.assertEqual(cards[0]["rule_id"], "body.font_size")
        self.assertEqual(cards[0]["location"], "第 3 段")
        self.assertEqual(cards[0]["actual"], "10.5 pt")
        self.assertEqual(cards[1]["suggestion"], "")

    def test_total_larger_than_saved_is_honest(self):
        result = blocks.overview_blocks(ISSUES[:1], 100)
        self.assertIn("共检测出 100 条问题", result[0]["text"])
        self.assertIn("本次统计基于已保存的 1 条", result[0]["text"])

    def test_empty_paper_has_no_issue_block(self):
        result = blocks.overview_blocks([], 0)
        self.assertEqual(len(result), 1)
        self.assertIn("当前记录的问题为 0 条", result[0]["text"])
        self.assertEqual(result[0]["issues"], [])

    def test_detail_blocks_index_from_one(self):
        result = blocks.detail_blocks(ISSUES)
        self.assertEqual(result[0]["type"], "issues")
        self.assertEqual([card["index"] for card in result[0]["issues"]], [1, 2])
        self.assertEqual(result[0]["issues"][1]["rule_id"], "caption.number")

    def test_empty_text_is_not_a_block(self):
        self.assertEqual(blocks.text_blocks(""), [])
        self.assertEqual(blocks.text_blocks("   ")[0]["text"], "   ")


class SuggestionTest(unittest.TestCase):
    def test_suggestions_are_clickable_queries(self):
        for intent in ("paper_overview", "paper_detail", "paper_fix",
                       "paper_basis", "paper_coverage",
                       "spec_qa", "out_of_scope", "paper_missing"):
            items = blocks.suggestions_for(intent)
            self.assertTrue(items, intent)
            for item in items:
                self.assertTrue(item["label"])
                self.assertTrue(item["query"])
                self.assertNotIn("下一条", item["query"])  # 列表不足时不提供做不到的按钮

    def test_paper_suggestions_route_back_to_paper_intents(self):
        from backend.rag.dialog.intent import detect_intent
        issues = [{"rule_id": "caption.number", "category": "图表",
                   "severity": "error", "location": "第 1 段",
                   "message": "图题编号格式不符"}]
        paper_intents = ("paper_overview", "paper_detail", "paper_fix",
                         "paper_basis", "paper_coverage")
        for intent in ("paper_overview", "paper_detail", "paper_fix",
                       "paper_basis", "paper_coverage"):
            for item in blocks.suggestions_for(intent):
                detected = detect_intent(item["query"], True, issues)
                self.assertIn(detected.name, paper_intents, item["query"])

    def test_more_suggestion_only_when_there_are_more(self):
        without_more = blocks.suggestions_for("paper_detail", stats={"has_more": False})
        with_more = blocks.suggestions_for("paper_detail", stats={"has_more": True})
        self.assertNotIn("继续看", [item["query"] for item in without_more])
        self.assertIn("继续看", [item["query"] for item in with_more])

    def test_overview_does_not_offer_first_issue_when_empty(self):
        items = blocks.suggestions_for("paper_overview", stats={"matched_total": 0})
        self.assertNotIn("第 1 条怎么改？", [item["query"] for item in items])


class FeedbackStoreTest(unittest.TestCase):
    def test_feedback_saved_without_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "sessions.db")
            manager = SessionManager(db_path=db_path)
            self.assertTrue(manager.save_feedback(
                "rid-1", "conv-1", "helpful", "paper_overview", "paper_facts"))
            conn = sqlite3.connect(db_path)
            try:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM chat_feedback WHERE response_id = ?", ("rid-1",)
                ).fetchone()
                columns = set(row.keys())
            finally:
                conn.close()
            self.assertEqual(row["rating"], "helpful")
            self.assertEqual(row["intent"], "paper_overview")
            self.assertEqual(row["answer_source"], "paper_facts")
            self.assertNotIn("question", columns)
            self.assertNotIn("conclusion", columns)

    def test_same_response_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = SessionManager(db_path=os.path.join(tmp, "sessions.db"))
            manager.save_feedback("rid-2", "conv", "helpful")
            manager.save_feedback("rid-2", "conv", "not_helpful")
            conn = sqlite3.connect(manager.db_path)
            try:
                rows = conn.execute(
                    "SELECT rating FROM chat_feedback WHERE response_id = ?", ("rid-2",)
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(rows, [("not_helpful",)])

    def test_empty_response_id_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = SessionManager(db_path=os.path.join(tmp, "sessions.db"))
            self.assertFalse(manager.save_feedback("", "conv", "helpful"))


class ChatResponseModelTest(unittest.TestCase):
    def test_answer_response_keeps_blocks_and_trace(self):
        class FakeRag:
            def answer(self, college_id, question, conversation_id=None, spec_only=False):
                return {
                    "college": "计算机学院", "question": question,
                    "conclusion": "共检测出 1 条问题", "basis": "", "suggestion": "",
                    "sources": [], "found": True, "total_time": 0.01,
                    "blocks": blocks.overview_blocks(ISSUES[:1], 1),
                    "suggestions": [{"label": "只看错误", "query": "只看错误"}],
                    "response_id": "rid", "trace": ["router", "paper_overview"],
                }

        with mock.patch("backend.api._require_rag", return_value=FakeRag()):
            response = TestClient(api.app).post("/api/rag/answer", json={
                "college_id": "jlu_cs", "question": "这篇论文哪里不对？",
            })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["blocks"][0]["type"], "summary")
        self.assertEqual(data["blocks"][1]["issues"][0]["rule_id"], "body.font_size")
        self.assertEqual(data["suggestions"][0]["query"], "只看错误")
        self.assertEqual(data["trace"], ["router", "paper_overview"])
        self.assertEqual(data["response_id"], "rid")


class FeedbackApiTest(unittest.TestCase):
    def test_endpoint_accepts_rating_only(self):
        saved = {}

        class FakeRag:
            def save_feedback(self, response_id, **kwargs):
                saved.update({"response_id": response_id, **kwargs})
                return True

        with mock.patch("backend.api._require_rag", return_value=FakeRag()):
            response = TestClient(api.app).post("/api/rag/feedback", json={
                "response_id": "r1",
                "conversation_id": "c1",
                "rating": "helpful",
                "intent": "paper_overview",
                "answer_source": "paper_facts",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        self.assertEqual(saved["response_id"], "r1")
        self.assertNotIn("question", saved)
        self.assertNotIn("conclusion", saved)

    def test_invalid_rating_is_rejected(self):
        with mock.patch("backend.api._require_rag", return_value=object()):
            response = TestClient(api.app).post("/api/rag/feedback", json={
                "response_id": "r1", "rating": "maybe",
            })
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
