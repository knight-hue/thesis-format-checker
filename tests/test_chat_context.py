# -*- coding: utf-8 -*-
"""P1 上下文与多问题拆分的回归。"""
import os
import tempfile
import time
import unittest

from backend.rag.dialog import facts
from backend.rag.dialog.intent import Intent, detect_intent, split_questions
from backend.rag.rag_service import RAGService
from backend.rag.session_manager import SessionManager

ISSUES = [
    {"rule_id": "body.font_size", "category": "正文格式", "severity": "error",
     "location": "第 3 段", "message": "正文字号不符合要求",
     "expected": "12 pt", "suggestion": "改为 12 磅"},
    {"rule_id": "reference.entry_period", "category": "参考文献", "severity": "warning",
     "location": "第 20 段", "message": "参考文献条目应以全角句点结束",
     "expected": "．", "suggestion": "改为全角句点"},
]


class MultiQuestionTest(unittest.TestCase):
    def test_split_only_returns_multiple_parts(self):
        self.assertEqual(split_questions("这篇论文哪里不对？"), [])
        self.assertEqual(split_questions("图题是什么？表题呢？"),
                         ["图题是什么", "表题呢"])

    def test_multi_question_asks_which_one_first(self):
        intent = detect_intent("图题是什么？表题呢？", True, ISSUES)
        self.assertEqual(intent.name, "clarify")
        self.assertEqual(intent.slots["kind"], "multi_question")
        self.assertEqual(intent.slots["options"], ["图题是什么", "表题呢"])


class ContextScopeTest(unittest.TestCase):
    def test_context_reuses_last_scope(self):
        intent = detect_intent(
            "这些怎么改？", True, ISSUES,
            last_scope={"scope": "category", "category": "参考文献"})
        self.assertEqual(intent.name, "paper_fix")
        self.assertEqual(intent.slots["scope"], "category")
        self.assertEqual(intent.slots["category"], "参考文献")
        self.assertEqual(intent.slots["context"], "last_scope")

    def test_context_does_not_override_spec_question(self):
        intent = detect_intent(
            "这些规范要求是什么？", True, ISSUES,
            last_scope={"scope": "category", "category": "参考文献"})
        self.assertEqual(intent.name, "spec_qa")

    def test_only_errors_filter(self):
        intent = detect_intent("只看错误", True, ISSUES)
        self.assertEqual(intent.name, "paper_detail")
        self.assertEqual(intent.slots["scope"], "severity")
        self.assertEqual(intent.slots["severity"], "error")

    def test_severity_filter_in_facts(self):
        selected, reason, meta = facts.select_issues(
            ISSUES, {"scope": "severity", "severity": "warning"})
        self.assertEqual([item["rule_id"] for item in selected],
                         ["reference.entry_period"])
        self.assertEqual(reason, "")
        self.assertEqual(meta["matched_total"], 1)


class FakeRAG(RAGService):
    def __init__(self, manager):
        self.session_manager = manager
        self.kb_manager = type("KB", (), {
            "colleges": {"jlu_cs": {"college_name": "计算机学院"}},
            "get_vocab": lambda self, college_id: set(),
        })()
        self.stats = {"total_requests": 0, "total_time": 0,
                      "total_search_time": 0, "total_generate_time": 0,
                      "total_rewrite_time": 0, "rewrite_count": 0,
                      "cache_hits": 0}
        self.cache = None
        self.query_rewriter = None
        self.llm = None


class ScopePersistenceTest(unittest.TestCase):
    def test_detail_scope_is_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = SessionManager(
                db_path=os.path.join(tmp, "sessions.db"), expire_seconds=0)
            manager.create_session("c1", "jlu_cs")
            manager.set_paper_errors("c1", ISSUES, total=2)
            service = FakeRAG(manager)
            service._answer_dialog(
                "jlu_cs", "图题那几条是什么？", "c1",
                Intent("paper_detail", {"scope": "category", "category": "参考文献"}),
                time.time(),
            )
            session = manager.get_session("c1")
            self.assertEqual(session["last_scope"]["scope"], "category")
            self.assertEqual(session["last_scope"]["category"], "参考文献")


if __name__ == "__main__":
    unittest.main()
