# -*- coding: utf-8 -*-
"""聊天回归评测：固定问法 -> 意图/来源/关键事实。

只跑规则与事实层，不加载知识库或模型，可作为每次改动后的聊天冒烟集。
"""
import unittest

from backend.rag.dialog.graph import DialogState, run_dialog
from backend.rag.dialog.intent import detect_intent

ISSUES = [
    {"rule_id": "body.font_size", "category": "正文格式", "severity": "error",
     "location": "第 3 段", "message": "正文字号不符合要求",
     "expected": "12 pt", "suggestion": "改为 12 磅",
     "basis": "正文用宋体五号字。"},
    {"rule_id": "reference.entry_period", "category": "参考文献", "severity": "warning",
     "location": "第 20 段", "message": "参考文献条目应以全角句点结束",
     "expected": "．", "suggestion": "改为全角句点",
     "basis": "参考文献条目以句点结束。"},
]

FIGURE_TABLE_ISSUES = [
    {"rule_id": "figure.caption_number", "category": "图", "severity": "error",
     "location": "第 5 段", "message": "图题编号格式不符", "expected": "图X-Y"},
    {"rule_id": "table.caption_number", "category": "表", "severity": "error",
     "location": "第 9 段", "message": "表题编号格式不符", "expected": "表X-Y"},
]

UNCHECKED = [{"rule_id": "figure.inner_font_size",
              "reason": "图内文字在位图上，字体字号无法自动核对，请人工核对。"}]


def _spec_answer(_question):
    return {"found": True, "conclusion": "规范答案", "basis": "§4.3",
            "suggestion": "", "sources": [], "answer_source": "spec_kb"}


def ask(question, issues=ISSUES, has_paper=True, last_scope=None, pending=None,
        unchecked=None):
    intent = detect_intent(question, has_paper, issues,
                           last_scope=last_scope, pending_clarify=pending)
    state = DialogState(
        question=question,
        intent=intent,
        has_paper=has_paper,
        paper_errors=list(issues),
        paper_total=len(issues),
        last_listed=[],
        pending_clarify=pending,
        paper_unchecked=unchecked,
        last_scope=last_scope or {},
    )
    run_dialog(state, _spec_answer)
    return intent, state.outcome


CASES = [
    {
        "name": "论文概览",
        "question": "这篇论文哪里不对？",
        "intent": "paper_overview",
        "source": "paper_facts",
        "contains": "共检测出 2 条问题",
    },
    {
        "name": "类目定位",
        "question": "参考文献有哪几条不符合？",
        "intent": "paper_detail",
        "source": "paper_facts",
        "contains": "参考文献条目应以全角句点结束",
    },
    {
        "name": "按规则号修改",
        "question": "规则号 body.font_size 怎么改？",
        "intent": "paper_fix",
        "source": "paper_facts",
        "contains": "正文字号不符合要求",
    },
    {
        "name": "只看错误",
        "question": "只看错误",
        "intent": "paper_detail",
        "source": "paper_facts",
        "contains": "正文字号不符合要求",
        "excludes": "参考文献条目",
    },
    {
        "name": "多问题澄清",
        "question": "图题是什么？表题呢？",
        "intent": "clarify",
        "source": "clarify",
        "contains": "先回答哪一个",
    },
    {
        "name": "越界拒答",
        "question": "今天天气怎么样",
        "intent": "out_of_scope",
        "source": "out_of_scope",
        "contains": "超出了论文格式规范的范围",
    },
    {
        "name": "未检测论文",
        "question": "这篇论文哪里不对？",
        "has_paper": False,
        "issues": [],
        "intent": "paper_overview",
        "source": "none",
        "contains": "还没有可用的检测结果",
    },
    {
        "name": "标题层级澄清",
        "question": "标题的字体要求是什么？",
        "intent": "clarify",
        "source": "clarify",
        "contains": "哪一级标题",
    },
    {
        "name": "规范问答",
        "question": "正文行距要求是多少？",
        "intent": "spec_qa",
        "source": "spec_kb",
        "contains": "规范答案",
    },
    {
        "name": "模板依据",
        "question": "第 1 条依据哪句模板？",
        "intent": "paper_basis",
        "source": "paper_facts",
        "contains": "正文用宋体五号字。",
    },
    {
        "name": "未检清单",
        "question": "还有哪些没查？",
        "intent": "paper_coverage",
        "source": "paper_facts",
        "contains": "figure.inner_font_size",
        "unchecked": UNCHECKED,
    },
    {
        "name": "多类目并列",
        "question": "图题和表题各有什么问题？",
        "issues": FIGURE_TABLE_ISSUES,
        "intent": "paper_detail",
        "source": "paper_facts",
        "contains": "图题编号格式不符",
    },
    {
        "name": "纠正序号",
        "question": "不是第 1 条，是第 2 条",
        "intent": "paper_detail",
        "source": "paper_facts",
        "contains": "参考文献条目应以全角句点结束",
    },
    {
        "name": "继续看到底",
        "question": "继续看",
        "last_scope": {"scope": "category", "category": "参考文献"},
        "intent": "paper_detail",
        "source": "paper_facts",
        "contains": "已经列到最后了",
    },
]


class ChatEvalTest(unittest.TestCase):
    def test_fixed_questions(self):
        for case in CASES:
            with self.subTest(case=case["name"]):
                _intent, outcome = ask(
                    case["question"],
                    issues=case.get("issues", ISSUES),
                    has_paper=case.get("has_paper", True),
                    last_scope=case.get("last_scope"),
                    unchecked=case.get("unchecked"),
                )
                self.assertEqual(_intent.name, case["intent"])
                self.assertEqual(outcome.answer_source, case["source"])
                text = outcome.conclusion or (
                    (outcome.spec_result or {}).get("conclusion") or "")
                if outcome.clarify:
                    text = outcome.clarify.get("question", "")
                self.assertIn(case["contains"], text)
                if case.get("excludes"):
                    self.assertNotIn(case["excludes"], text)

    def test_context_follow_up(self):
        _intent, outcome = ask(
            "这些怎么改？",
            last_scope={"scope": "category", "category": "参考文献"},
        )
        self.assertEqual(_intent.name, "paper_fix")
        self.assertIn("参考文献", outcome.conclusion)
        self.assertNotIn("正文字号", outcome.conclusion)


if __name__ == "__main__":
    unittest.main()
