# -*- coding: utf-8 -*-
"""对话分支与 RAGService 的接线：不加载知识库/模型，只验证结果与会话落库。"""
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from backend.rag.dialog.intent import Intent
from backend.rag.rag_service import RAGService
from backend.rag.session_manager import SessionManager


class EmptyRetriever:
    def invoke(self, question):
        return []


class FakeRAG(RAGService):
    """绕过 __init__ 的 KB/LLM 初始化，只复用对话分支与结果构造。"""

    def __init__(self, manager):
        self.session_manager = manager
        # 字段名与 config/colleges.json 对齐：真实配置是 college_name，不是 name。
        self.kb_manager = SimpleNamespace(
            colleges={"jlu_cs": {"college_name": "计算机学院"}},
            get_vocab=lambda college_id: set(),
            get_retriever=lambda college_id: EmptyRetriever(),
        )
        self.stats = {"total_requests": 0, "total_time": 0,
                      "total_search_time": 0, "total_generate_time": 0,
                      "total_rewrite_time": 0, "rewrite_count": 0,
                      "cache_hits": 0}
        self.cache = None
        self.query_rewriter = None
        self.llm = None


ISSUES = [
    {"rule_id": "body.font_size", "category": "正文格式", "severity": "error",
     "location": "第 3 段", "message": "正文字号不符合要求",
     "expected": "12 pt", "suggestion": "改为 12 磅", "source": "rule_engine"},
    {"rule_id": "caption.number", "category": "图表", "severity": "error",
     "location": "第 12 段", "message": "图题编号格式不符",
     "expected": "图X-Y", "suggestion": "", "source": "rule_engine"},
]


class DialogServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.manager = SessionManager(
            db_path=os.path.join(self.tmp.name, "sessions.db"), expire_seconds=0)
        self.service = FakeRAG(self.manager)
        self.manager.create_session("c1", "jlu_cs")
        self.manager.set_paper_errors("c1", ISSUES, total=2)

    def tearDown(self):
        self.tmp.cleanup()

    def run_dialog(self, intent, question="问题"):
        return self.service._answer_dialog(
            "jlu_cs", question, "c1", intent, time.time())

    def test_overview_returns_facts_and_persists_order(self):
        result = self.run_dialog(Intent("paper_overview", {"scope": "all"}))
        self.assertEqual(result["intent"], "paper_overview")
        self.assertEqual(result["answer_source"], "paper_facts")
        self.assertEqual(result["college"], "计算机学院")
        self.assertIn("共检测出 2 条问题", result["conclusion"])
        self.assertEqual(result["actions"][0]["type"], "open-result")
        self.assertEqual([item["type"] for item in result["blocks"]],
                         ["summary", "issues"])
        self.assertEqual(result["blocks"][1]["issues"][0]["rule_id"], "body.font_size")
        self.assertTrue(result["suggestions"])
        self.assertTrue(result["response_id"])
        self.assertEqual(result["trace"], ["router", "paper_overview"])
        session = self.manager.get_session("c1")
        self.assertEqual(len(session["last_listed"]), 2)
        self.assertEqual(len(session["history"]), 2)  # 一轮 user + assistant
        self.assertEqual(session["last_intent"], "paper_overview")

    def test_detail_index_returns_highlight_action(self):
        candidates = [{"rule_id": "caption.number", "location": "第 12 段",
                       "message": "图题编号格式不符"}]
        result = self.run_dialog(Intent("paper_detail",
                                        {"scope": "index", "index": 1,
                                         "candidates": candidates}))
        self.assertIn("图题编号格式不符", result["conclusion"])
        self.assertEqual(result["actions"][0]["type"], "highlight-issues")
        self.assertEqual(result["slots"], {"scope": "index", "index": 1})

    def test_clarify_persists_pending(self):
        result = self.run_dialog(Intent("paper_detail", {"scope": "index"}))
        self.assertEqual(result["answer_source"], "clarify")
        self.assertIsNotNone(result["clarify"])
        session = self.manager.get_session("c1")
        self.assertEqual(session["pending_clarify"]["kind"], "paper_index")
        self.assertTrue(session["pending_clarify"]["candidates"])

    def test_missing_paper_uses_fixed_text(self):
        # create_session 不带 paper_errors = 还没检测过
        self.manager.create_session("c1", "jlu_cs")
        result = self.run_dialog(Intent("paper_overview", {"scope": "all"}))
        self.assertEqual(result["answer_source"], "none")
        self.assertIn("还没有可用的检测结果", result["conclusion"])

    def test_checked_paper_with_zero_issues_is_not_missing(self):
        self.manager.set_paper_errors("c1", [], total=0)
        result = self.run_dialog(Intent("paper_overview", {"scope": "all"}))
        self.assertEqual(result["answer_source"], "paper_facts")
        self.assertIn("当前记录的问题为 0 条", result["conclusion"])
        self.assertNotIn("还没有可用的检测结果", result["conclusion"])

    def test_zero_issue_paper_detail_does_not_claim_missing(self):
        self.manager.set_paper_errors("c1", [], total=0)
        result = self.run_dialog(
            Intent("paper_detail", {"scope": "index", "index": 1}))
        self.assertEqual(result["answer_source"], "paper_facts")
        self.assertIn("没有可选的条号", result["conclusion"])
        self.assertNotIn("还没有可用的检测结果", result["conclusion"])

    def test_out_of_scope(self):
        result = self.run_dialog(Intent("out_of_scope"))
        self.assertEqual(result["answer_source"], "out_of_scope")
        self.assertFalse(result["found"])

    def test_conversation_id_none_does_not_touch_session(self):
        result = self.service._answer_dialog(
            "jlu_cs", "哪里不对", None, Intent("paper_overview", {"scope": "all"}),
            time.time())
        self.assertEqual(result["answer_source"], "none")
        self.assertIn("还没有可用的检测结果", result["conclusion"])

    def test_router_switch_off_does_not_enter_dialog(self):
        # 回滚开关：关闭后同一句“这篇论文哪里不对”必须回到原检索链路，
        # 不能被事实层接管（假 KB 检索为空，因此得到“未检索到”）。
        with mock.patch("backend.rag.rag_service.USE_DIALOG_ROUTER", False), \
                mock.patch.object(RAGService, "_answer_dialog",
                                  side_effect=AssertionError("不应进入对话分支")):
            result = self.service.answer("jlu_cs", "这篇论文哪里不对？", "c1")
        self.assertEqual(result["conclusion"], "未检索到匹配的论文格式规范")
        self.assertEqual(result["answer_source"], "spec_kb")

    def test_spec_only_bypasses_dialog_router(self):
        # 规则解释弹窗的问题带分号/“规则编号”，不能被拆成多问题或论文类问题
        with mock.patch.object(RAGService, "_answer_dialog",
                               side_effect=AssertionError("不应进入对话分支")):
            result = self.service.answer(
                "jlu_cs",
                "请解释这条论文格式规范；问题：关键词标签应写作“关键字”；"
                "规范要求：关键字：；出现位置：第 27 段（规则编号：keyword.title_label）",
                "c1",
                spec_only=True,
            )
        self.assertEqual(result["conclusion"], "未检索到匹配的论文格式规范")
        self.assertEqual(result["answer_source"], "spec_kb")
        self.assertNotEqual(result["intent"], "clarify")


if __name__ == "__main__":
    unittest.main()
