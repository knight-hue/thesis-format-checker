# -*- coding: utf-8 -*-
"""状态机回归：路由、事实拼装、澄清回环、护栏。"""
import unittest

from backend.rag.dialog.graph import DialogState, run_dialog
from backend.rag.dialog.intent import Intent

ISSUES = [
    {"rule_id": "body.font_size", "category": "正文格式", "severity": "error",
     "location": {"type": "paragraph", "index": 3}, "message": "正文字号不符合要求",
     "expected": "12 pt", "suggestion": "改为 12 磅"},
    {"rule_id": "caption.number", "category": "图表", "severity": "error",
     "location": {"type": "paragraph", "index": 12}, "message": "图题编号格式不符",
     "expected": "图X-Y", "suggestion": ""},
]


def make_state(intent, errors=None, has_paper=None, last_listed=None, pending=None, question="问题"):
    errors = ISSUES if errors is None else errors
    return DialogState(
        question=question,
        intent=intent,
        has_paper=bool(errors) if has_paper is None else has_paper,
        paper_errors=errors,
        paper_total=len(errors),
        last_listed=last_listed or [],
        pending_clarify=pending,
    )


class SpecAnswer:
    def __init__(self):
        self.calls = []

    def __call__(self, question):
        self.calls.append(question)
        return {"found": True, "conclusion": "规范答案", "basis": "§4.3",
                "suggestion": "", "sources": []}


class GraphTest(unittest.TestCase):
    def test_paper_overview(self):
        state = make_state(Intent("paper_overview", {"scope": "all"}))
        run_dialog(state, SpecAnswer())
        self.assertIn("共检测出 2 条问题", state.outcome.conclusion)
        self.assertEqual(state.outcome.answer_source, "paper_facts")
        self.assertEqual(state.outcome.actions[0]["type"], "open-result")
        self.assertEqual(len(state.outcome.last_listed), 2)
        self.assertTrue(state.outcome.history_answer)

    def test_paper_detail_with_index(self):
        candidates = [{"rule_id": "caption.number", "location": "第 12 段",
                       "message": "图题编号格式不符"}]
        state = make_state(Intent("paper_detail",
                                  {"scope": "index", "index": 1,
                                   "candidates": candidates}))
        run_dialog(state, SpecAnswer())
        self.assertIn("图题编号格式不符", state.outcome.conclusion)
        self.assertEqual(state.outcome.actions[0]["type"], "highlight-issues")
        self.assertIn("caption.number", state.outcome.actions[0]["rule_ids"])

    def test_index_missing_turns_into_clarify(self):
        state = make_state(Intent("paper_detail", {"scope": "index"}),
                           last_listed=[])
        run_dialog(state, SpecAnswer())
        self.assertIsNotNone(state.outcome.clarify)
        self.assertEqual(state.outcome.clarify["kind"], "paper_index")
        self.assertEqual(state.outcome.answer_source, "clarify")
        self.assertEqual(state.outcome.pending_clarify["kind"], "paper_index")

    def test_fix_mode_mentions_manual_selection(self):
        state = make_state(Intent("paper_fix", {"scope": "all"}))
        run_dialog(state, SpecAnswer())
        self.assertIn("系统不会自动修改", state.outcome.conclusion)

    def test_missing_paper_does_not_call_spec(self):
        spec = SpecAnswer()
        state = make_state(Intent("paper_overview", {"scope": "all"}),
                           errors=[], has_paper=False)
        run_dialog(state, spec)
        self.assertIn("还没有可用的检测结果", state.outcome.conclusion)
        self.assertEqual(spec.calls, [])
        self.assertEqual(state.outcome.answer_source, "none")

    def test_out_of_scope_does_not_call_spec(self):
        spec = SpecAnswer()
        state = make_state(Intent("out_of_scope"))
        run_dialog(state, spec)
        self.assertEqual(spec.calls, [])
        self.assertEqual(state.outcome.answer_source, "out_of_scope")

    def test_heading_clarify(self):
        state = make_state(Intent("clarify", {"kind": "heading_level", "attribute": "字号"}))
        run_dialog(state, SpecAnswer())
        self.assertEqual(state.outcome.clarify["kind"], "heading_level")
        self.assertEqual(len(state.outcome.clarify["options"]), 3)
        self.assertEqual(state.outcome.pending_clarify["attribute"], "字号")

    def test_consumed_heading_level_rewrites_question(self):
        spec = SpecAnswer()
        state = make_state(Intent("spec_qa", {"heading_level": "一级",
                                              "attribute": "字号",
                                              "clarify_answer": True}))
        run_dialog(state, spec)
        self.assertEqual(spec.calls[0], "一级标题的字号要求是什么？")
        self.assertIsNone(state.outcome.pending_clarify)
        self.assertIsNotNone(state.outcome.spec_result)

    def test_step_guard_falls_back_to_spec(self):
        spec = SpecAnswer()
        state = make_state(Intent("paper_overview", {"scope": "all"}))
        run_dialog(state, spec, max_steps=1)
        # 护栏触发时落规范问答，仍然有结果，不会死循环
        self.assertIsNotNone(state.outcome.spec_result)
        self.assertEqual(len(spec.calls), 1)

    def test_spec_does_not_write_history_twice(self):
        state = make_state(Intent("spec_qa"))
        run_dialog(state, SpecAnswer())
        self.assertEqual(state.outcome.history_answer, "")


if __name__ == "__main__":
    unittest.main()
