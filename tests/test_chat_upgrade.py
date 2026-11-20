# -*- coding: utf-8 -*-
"""对话模块第二轮升级的回归：依据、总数、分页、未检、相对指代、旧结果版本。"""
import os
import tempfile
import time
import unittest
from types import SimpleNamespace

from backend.rag.dialog.intent import Intent, detect_intent
from backend.rag.rag_service import RAGService
from backend.rag.session_manager import SessionManager, summarize_issues

ISSUES = [
    {"rule_id": "body.font_size", "category": "正文格式", "severity": "error",
     "location": {"type": "paragraph", "index": 3}, "message": "正文字号不符合要求",
     "expected": "12 pt", "actual": "10.5 pt", "suggestion": "改为 12 磅",
     "basis": "正文用宋体五号字。", "source": "rule_engine"},
    {"rule_id": "reference.entry_period", "category": "参考文献", "severity": "warning",
     "location": {"type": "paragraph", "index": 20}, "message": "参考文献条目应以全角句点结束",
     "expected": "．", "actual": "半角句点", "suggestion": "改为全角句点",
     "basis": "参考文献条目以句点结束。", "source": "rule_engine"},
    {"rule_id": "figure.caption_number", "category": "图", "severity": "error",
     "location": {"type": "paragraph", "index": 5}, "message": "图题编号格式不符",
     "expected": "图X-Y", "actual": "图X.Y", "suggestion": "",
     "basis": "图题按章编号。", "source": "rule_engine"},
]

UNCHECKED = [{"rule_id": "figure.inner_font_size",
              "reason": "图内文字在位图上，字体字号无法自动核对，请人工核对。"}]


class SummaryTest(unittest.TestCase):
    def test_summary_keeps_explainable_fields(self):
        summary = summarize_issues(ISSUES)
        self.assertEqual(summary[0]["suggestion"], "改为 12 磅")
        self.assertEqual(summary[0]["actual"], "10.5 pt")
        self.assertEqual(summary[0]["basis"], "正文用宋体五号字。")

    def test_summary_compacts_structured_actual(self):
        summary = summarize_issues([{"rule_id": "x", "location": {"type": "document"},
                                     "actual": {"pt": 10.5}}])
        self.assertEqual(summary[0]["actual"], '{"pt": 10.5}')


class SessionStateTest(unittest.TestCase):
    def test_unchecked_and_version_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = SessionManager(
                db_path=os.path.join(tmp, "sessions.db"), expire_seconds=0)
            manager.bind_paper_errors("c1", "jlu_cs", ISSUES, unchecked=UNCHECKED)
            session = manager.get_session("c1")
            self.assertEqual(session["paper_unchecked"], UNCHECKED)
            first = manager.paper_fingerprint("c1")

            # 修复后只删条目：未检清单保留，指纹变化
            manager.remove_paper_errors("c1", [ISSUES[0]])
            session = manager.get_session("c1")
            self.assertEqual(session["paper_unchecked"], UNCHECKED)
            self.assertNotEqual(manager.paper_fingerprint("c1"), first)

    def test_missing_unchecked_is_not_stored_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = SessionManager(
                db_path=os.path.join(tmp, "sessions.db"), expire_seconds=0)
            manager.bind_paper_errors("c1", "jlu_cs", ISSUES)
            self.assertIsNone(manager.get_session("c1")["paper_unchecked"])


class EmptyKb:
    def invoke(self, question):
        return []


class FakeRAG(RAGService):
    def __init__(self, manager):
        self.session_manager = manager
        self.kb_manager = SimpleNamespace(
            colleges={"jlu_cs": {"college_name": "计算机学院"}},
            get_vocab=lambda college_id: set(),
            get_retriever=lambda college_id: EmptyKb(),
        )
        self.stats = {"total_requests": 0, "total_time": 0,
                      "total_search_time": 0, "total_generate_time": 0,
                      "total_rewrite_time": 0, "rewrite_count": 0,
                      "cache_hits": 0}
        self.cache = None
        self.query_rewriter = None
        self.llm = None


class DialogUpgradeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.manager = SessionManager(
            db_path=os.path.join(self.tmp.name, "sessions.db"), expire_seconds=0)
        self.service = FakeRAG(self.manager)
        self.manager.bind_paper_errors("c1", "jlu_cs", ISSUES, unchecked=UNCHECKED)

    def tearDown(self):
        self.tmp.cleanup()

    def run_dialog(self, intent, question="问题"):
        return self.service._answer_dialog("jlu_cs", question, "c1", intent, time.time())

    def ask(self, question):
        """走完整识别链路：会话里的 last_listed / last_scope 参与序号解析。"""
        session = self.manager.get_session("c1") or {}
        intent = detect_intent(
            question, True, session.get("paper_errors") or [],
            last_listed=session.get("last_listed") or [],
            last_scope=session.get("last_scope") or {},
        )
        return self.run_dialog(intent, question)

    @staticmethod
    def _cards(result):
        return [card for block in result["blocks"]
                for card in block.get("issues") or []]

    def test_basis_answer_uses_template_sentence(self):
        result = self.run_dialog(
            Intent("paper_basis", {"scope": "index", "index": 1}), "第 1 条依据什么")
        self.assertIn("正文用宋体五号字。", result["conclusion"])
        cards = [card for block in result["blocks"]
                 for card in block.get("issues") or []]
        self.assertTrue(cards)
        self.assertEqual(cards[0]["basis"], "正文用宋体五号字。")
        self.assertEqual(cards[0]["actual"], "10.5 pt")
        self.assertEqual(cards[0]["suggestion"], "改为 12 磅")

    def test_coverage_lists_unchecked(self):
        result = self.run_dialog(Intent("paper_coverage", {"scope": "all"}),
                                 "还有哪些没查")
        self.assertIn("figure.inner_font_size", result["conclusion"])
        self.assertIn("不等于通过", result["conclusion"])
        self.assertEqual(result["actions"][0]["type"], "open-result")

    def test_fix_uses_stored_suggestion(self):
        result = self.run_dialog(
            Intent("paper_fix", {"scope": "index", "index": 1}), "第 1 条怎么改")
        self.assertIn("修改建议：改为 12 磅", result["conclusion"])
        self.assertNotIn("该条不提供自动修改", result["conclusion"])

    def test_detail_reports_total_beyond_three(self):
        many = [dict(ISSUES[0], message=f"字号不符 {index}") for index in range(5)]
        self.manager.set_paper_errors("c1", many, total=5, unchecked=UNCHECKED)
        result = self.run_dialog(
            Intent("paper_detail", {"scope": "category", "category": "正文格式"}),
            "正文格式有哪些问题")
        self.assertIn("共 5 条，先列前 3 条", result["conclusion"])
        labels = [item["query"] for item in result["suggestions"]]
        self.assertIn("继续看", labels)

    def test_continue_reads_next_batch(self):
        many = [dict(ISSUES[0], message=f"字号不符 {index}") for index in range(12)]
        self.manager.set_paper_errors("c1", many, total=12, unchecked=UNCHECKED)
        self.run_dialog(
            Intent("paper_detail", {"scope": "category", "category": "正文格式"}),
            "正文格式有哪些问题")
        result = self.run_dialog(
            Intent("paper_detail", {"scope": "page", "page": 2,
                                    "category": "正文格式"}), "继续看")
        self.assertIn("第 4–12 条（共 12 条）", result["conclusion"])
        cards = [card for block in result["blocks"]
                 for card in block.get("issues") or []]
        self.assertEqual([card["index"] for card in cards], list(range(4, 13)))

    def test_next_and_previous_follow_cursor(self):
        self.run_dialog(Intent("paper_detail", {"scope": "all"}), "这些问题")
        listed = self.manager.get_session("c1")["last_listed"]
        result = self.run_dialog(
            Intent("paper_detail", {"scope": "index", "index": 1,
                                    "candidates": listed}), "第 1 条")
        self.assertIn("正文字号不符合要求", result["conclusion"])
        result = self.run_dialog(
            Intent("paper_detail", {"scope": "index", "direction": 1}),
            "下一条")
        # 下一条按上一次列出顺序取第 2 条（这里列表是个 3 条，正文在前）
        self.assertIn("参考文献条目应以全角句点结束", result["conclusion"])
        result = self.run_dialog(
            Intent("paper_detail", {"scope": "index", "direction": -1}),
            "上一条")
        self.assertIn("正文字号不符合要求", result["conclusion"])

    def test_next_at_end_is_honest(self):
        self.run_dialog(Intent("paper_detail", {"scope": "all"}), "这些问题")
        listed = self.manager.get_session("c1")["last_listed"]
        self.run_dialog(
            Intent("paper_detail", {"scope": "index", "index": len(listed),
                                    "candidates": listed}), "最后一条")
        result = self.run_dialog(
            Intent("paper_detail", {"scope": "index", "direction": 1}),
            "下一条")
        self.assertIn("已经是最后一批了", result["conclusion"])

    def test_highlight_action_carries_location(self):
        result = self.run_dialog(
            Intent("paper_detail", {"scope": "index", "index": 1}), "第 1 条")
        action = result["actions"][0]
        self.assertEqual(action["type"], "highlight-issues")
        self.assertEqual(action["targets"], [{"rule_id": "body.font_size",
                                              "location": "第 3 段"}])

    def test_paper_version_changes_after_recheck(self):
        first = self.run_dialog(Intent("paper_overview", {"scope": "all"}))
        self.assertTrue(first["paper_version"])
        self.manager.bind_paper_errors("c1", "jlu_cs", ISSUES[:1],
                                       keep_history=True, unchecked=UNCHECKED)
        second = self.run_dialog(Intent("paper_overview", {"scope": "all"}))
        self.assertNotEqual(first["paper_version"], second["paper_version"])

    # ---------- 界面实测发现的两处语义问题 ----------
    def test_index_follows_last_listed_not_global_order(self):
        first = self.ask("图题有哪几条？")
        self.assertIn("图题编号格式不符", first["conclusion"])
        # 全量排序下第 1 条是正文（error + rule_id 更小），这里必须按上一次列出的顺序
        result = self.ask("第 1 条")
        self.assertIn("图题编号格式不符", result["conclusion"])
        self.assertEqual(self._cards(result)[0]["index"], 1)

    def test_relative_next_uses_absolute_card_index(self):
        self.ask("只看错误")
        first = self.ask("第 1 条")
        self.assertIn("正文字号不符合要求", first["conclusion"])
        nxt = self.ask("下一条")
        self.assertIn("图题编号格式不符", nxt["conclusion"])
        self.assertEqual(self._cards(nxt)[0]["index"], 2)

    def test_detail_surfaces_total_as_visible_block(self):
        many = [dict(ISSUES[0], message=f"字号不符 {index}") for index in range(5)]
        self.manager.set_paper_errors("c1", many, total=5, unchecked=UNCHECKED)
        result = self.ask("正文格式那几条")
        lead = result["blocks"][0]
        self.assertEqual(lead["type"], "text")
        self.assertIn("共 5 条，先列前 3 条", lead["text"])

    def test_multi_category_header_shows_each_count(self):
        self.manager.set_paper_errors("c1", [
            {"rule_id": "figure.caption_number", "category": "图", "severity": "error",
             "location": {"type": "paragraph", "index": 1}, "message": "图题一"},
            {"rule_id": "figure.caption_number_2", "category": "图", "severity": "error",
             "location": {"type": "paragraph", "index": 2}, "message": "图题二"},
            {"rule_id": "table.caption_number", "category": "表", "severity": "error",
             "location": {"type": "paragraph", "index": 3}, "message": "表题一"},
        ], total=3, unchecked=UNCHECKED)
        result = self.ask("图题和表题各有什么问题？")
        lead = result["blocks"][0]
        self.assertIn("图 2 条", lead["text"])
        self.assertIn("表 1 条", lead["text"])
        self.assertEqual({card["category"] for card in self._cards(result)}, {"图", "表"})


if __name__ == "__main__":
    unittest.main()
