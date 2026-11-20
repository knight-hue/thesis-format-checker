# -*- coding: utf-8 -*-
"""P2 工具节点与执行轨迹的回归。"""
import unittest

from backend.rag.dialog.graph import DialogState, run_dialog
from backend.rag.dialog.intent import Intent, detect_intent

ISSUES = [
    {"rule_id": "body.font_size", "category": "正文格式", "severity": "error",
     "location": {"type": "paragraph", "index": 3}, "message": "正文字号不符合要求",
     "expected": "12 pt", "suggestion": "改为 12 磅"},
    {"rule_id": "caption.number", "category": "图表", "severity": "error",
     "location": {"type": "paragraph", "index": 12}, "message": "图题编号格式不符",
     "expected": "图X-Y", "suggestion": ""},
]


def _run(intent):
    state = DialogState(
        question="问题",
        intent=intent,
        has_paper=True,
        paper_errors=ISSUES,
        paper_total=len(ISSUES),
        last_listed=[],
        pending_clarify=None,
    )
    run_dialog(state, lambda _question: {"found": True, "conclusion": "规范答案"})
    return state


class RuleToolTest(unittest.TestCase):
    def test_explicit_rule_id_uses_exact_filter(self):
        intent = detect_intent("规则号 body.font_size 怎么改？", True, ISSUES)
        self.assertEqual(intent.name, "paper_fix")
        self.assertEqual(intent.slots["scope"], "rule")
        self.assertEqual(intent.slots["rule_ids"], ["body.font_size"])

    def test_index_wins_over_general_rule_phrase(self):
        intent = detect_intent("第 1 条对应哪条规则？", True, ISSUES)
        self.assertEqual(intent.slots["scope"], "index")
        self.assertEqual(intent.slots["index"], 1)
        self.assertEqual(intent.slots["mode"], "rule")

    def test_rule_lookup_returns_only_recorded_ids(self):
        state = _run(Intent("paper_detail",
                            {"scope": "rule", "rule_ids": ["body.font_size"],
                             "mode": "rule"}))
        self.assertIn("body.font_size", state.outcome.conclusion)
        self.assertNotIn("caption.number", state.outcome.conclusion)
        self.assertEqual(state.outcome.answer_source, "paper_facts")

    def test_trace_records_nodes(self):
        state = _run(Intent("paper_detail", {"scope": "all"}))
        self.assertEqual(state.outcome.trace[0], "router")
        self.assertIn("paper_detail", state.outcome.trace)

    def test_multi_question_trace(self):
        state = _run(Intent("clarify", {
            "kind": "multi_question",
            "options": ["图题是什么", "表题呢"],
        }))
        self.assertEqual(state.outcome.answer_source, "clarify")
        self.assertEqual(state.outcome.clarify["kind"], "multi_question")
        self.assertEqual(state.outcome.trace, ["router", "clarify"])


if __name__ == "__main__":
    unittest.main()
