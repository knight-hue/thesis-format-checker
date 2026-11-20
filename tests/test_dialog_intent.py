# -*- coding: utf-8 -*-
"""意图层回归：只测规则识别，不碰检索/模型。"""
import unittest

from backend.rag.dialog.intent import detect_intent, parse_cn_number

ISSUES = [
    {"rule_id": "body.font_size", "category": "正文格式", "severity": "error",
     "location": {"type": "paragraph", "index": 3}, "message": "正文字号不符合要求"},
    {"rule_id": "caption.number", "category": "图表", "severity": "error",
     "location": {"type": "paragraph", "index": 12}, "message": "图题编号格式不符"},
    {"rule_id": "reference.entry_period", "category": "参考文献", "severity": "warning",
     "location": {"type": "paragraph", "index": 20}, "message": "参考文献条目应以全角句点结束"},
    {"rule_id": "heading.heading_1.font", "category": "标题", "severity": "error",
     "location": {"type": "section", "index": 1}, "message": "一级标题字体不符"},
    {"rule_id": "formula.number", "category": "公式", "severity": "error",
     "location": {"type": "paragraph", "index": 30}, "message": "公式编号不连续"},
]

LISTED = [
    {"rule_id": "caption.number", "location": "第 12 段"},
    {"rule_id": "body.font_size", "location": "第 3 段"},
    {"rule_id": "reference.entry_period", "location": "第 20 段"},
]


class NumberParseTest(unittest.TestCase):
    def test_arabic_and_chinese(self):
        cases = {"3": 3, "三": 3, "十": 10, "十二": 12, "二十": 20, "二十三": 23}
        for text, expected in cases.items():
            self.assertEqual(parse_cn_number(text), expected, text)

    def test_invalid(self):
        for text in ("", "几", "第", "很多"):
            self.assertIsNone(parse_cn_number(text), text)


class DetectIntentTest(unittest.TestCase):
    def check(self, question, expected, has_paper=True, **kwargs):
        intent = detect_intent(question, has_paper, ISSUES, **kwargs)
        self.assertEqual(intent.name, expected, question)
        return intent

    def test_paper_rules(self):
        self.check("这篇论文哪里不对？", "paper_overview")
        self.check("检测结果怎么样", "paper_overview")
        self.check("如何修改这些问题？", "paper_fix")
        self.check("参考文献怎么改", "paper_fix")
        self.check("图题那几条怎么改", "paper_fix")
        self.check("我的图题字号错了吗", "paper_detail")
        self.check("公式编号不连续吗", "paper_detail")

    def test_index_rules(self):
        intent = self.check("第3条怎么改", "paper_fix")
        self.assertEqual(intent.slots["scope"], "index")
        self.assertEqual(intent.slots["index"], 3)
        intent = self.check("第三条呢", "paper_detail")
        self.assertEqual(intent.slots["index"], 3)
        intent = self.check("第十二条", "paper_detail")
        self.assertEqual(intent.slots["index"], 12)
        # 上一轮列过条时，序号必须带上那份顺序（否则卡片 #N 与“第 N 条”对不上）
        intent = self.check("第 2 条", "paper_detail", last_listed=LISTED)
        self.assertEqual(intent.slots["candidates"], LISTED)
        # 「最后一条」= 上一次列表的最后一项；「上一条」= 游标回退（相对指代）
        intent = self.check("最后一条", "paper_detail", last_listed=LISTED)
        self.assertEqual(intent.slots["index"], 3)
        intent = self.check("上一条", "paper_detail", last_listed=LISTED)
        self.assertEqual(intent.slots["direction"], -1)
        intent = self.check("下一条", "paper_detail", last_listed=LISTED)
        self.assertEqual(intent.slots["direction"], 1)
        intent = self.check("最后一条", "paper_detail", last_listed=[])
        self.assertEqual(intent.slots["scope"], "index")
        self.assertNotIn("index", intent.slots)

    def test_correction_uses_last_index(self):
        intent = self.check("不是第 2 条，是第 3 条", "paper_detail",
                            last_listed=LISTED)
        self.assertEqual(intent.slots["index"], 3)
        intent = self.check("不对，我是说第 4 条", "paper_detail",
                            last_listed=LISTED)
        self.assertEqual(intent.slots["index"], 4)

    def test_basis_intent(self):
        intent = self.check("第 2 条依据什么？", "paper_basis")
        self.assertEqual(intent.slots["index"], 2)
        intent = self.check("依据哪句模板？", "paper_basis")
        self.assertEqual(intent.slots["scope"], "all")
        intent = self.check("图题的依据是什么？", "paper_basis")
        self.assertEqual(intent.slots["scope"], "category")
        # 「图题字号要求多少」仍是规范问题，不能被依据类抢走
        self.check("图题字号要求多少", "spec_qa")

    def test_coverage_intent(self):
        intent = self.check("还有哪些没查？", "paper_coverage")
        self.assertEqual(intent.slots["scope"], "all")
        self.check("图内字体查没查？", "paper_coverage")

    def test_multiple_categories(self):
        # 真实规则号是 figure.* / table.*，类目分别是“图”与“表”
        realistic = [
            {"rule_id": "figure.caption_number", "category": "图", "severity": "error",
             "location": {"type": "paragraph", "index": 1}, "message": "图题编号不符"},
            {"rule_id": "table.caption_number", "category": "表", "severity": "error",
             "location": {"type": "paragraph", "index": 2}, "message": "表题编号不符"},
        ]
        intent = detect_intent("图题和表题各有什么问题", True, realistic)
        self.assertEqual(intent.name, "paper_detail")
        self.assertEqual(intent.slots["scope"], "categories")
        self.assertEqual(len(intent.slots["categories"]), 2)
        self.assertEqual([group["label"] for group in intent.slots["categories"]],
                         ["图", "表"])
        self.assertEqual(intent.reason, "R5 多类目定位")

    def test_continue_paging(self):
        intent = self.check("继续看", "paper_detail",
                            last_scope={"scope": "category", "category": "图表"})
        self.assertEqual(intent.slots["scope"], "page")
        self.assertEqual(intent.slots["page"], 2)
        self.assertEqual(intent.slots["category"], "图表")

    def test_single_issue_context_reuse(self):
        intent = self.check("这条怎么改？", "paper_fix",
                            last_listed=LISTED,
                            last_scope={"scope": "index", "cursor": 2})
        self.assertEqual(intent.slots["index"], 2)
        self.assertEqual(intent.reason, "R2b 单条上下文复用")

    def test_basis_after_single_issue_reuses_cursor(self):
        intent = self.check("依据哪句模板？", "paper_basis",
                            last_listed=LISTED,
                            last_scope={"scope": "index", "cursor": 2})
        self.assertEqual(intent.slots["index"], 2)
        self.assertEqual(intent.reason, "R3b 依据类（当前条）")

    def test_spec_rules(self):
        self.check("一级标题应该用什么字号", "spec_qa")
        self.check("参考文献的著录格式要求是什么", "spec_qa")
        self.check("这个怎么弄", "spec_qa")
        intent = self.check("标题字多大", "clarify")
        self.assertEqual(intent.slots["kind"], "heading_level")
        self.assertEqual(intent.slots["attribute"], "字号")
        intent = self.check("标题用什么字体", "clarify")
        self.assertEqual(intent.slots["attribute"], "字体")

    def test_spec_beats_category_without_my_paper(self):
        # 「图题字号要求多少」问的是规范，不是自己论文错在哪
        self.check("图题字号要求多少", "spec_qa")
        self.check("我的图题字号错了吗", "paper_detail")

    def test_out_of_scope_before_spec(self):
        self.check("今天天气怎么样", "out_of_scope")
        self.check("帮我写代码", "out_of_scope")
        self.check("翻译这段是什么意思", "out_of_scope")
        self.check("你是谁", "out_of_scope")

    def test_no_paper_keeps_paper_intent_for_router(self):
        # 没有检测结果时，论文类问题也要识别出来，由状态机转 paper_missing
        self.check("哪里不对", "paper_overview", has_paper=False)
        self.check("怎么改", "paper_fix", has_paper=False)

    def test_pending_paper_index(self):
        pending = {"kind": "paper_index", "candidates": LISTED}
        intent = self.check("第 2 条", "paper_detail", pending_clarify=pending)
        self.assertEqual(intent.slots["index"], 2)
        self.assertEqual(intent.slots["candidates"], LISTED)
        intent = self.check("第 2 条怎么改", "paper_fix", pending_clarify=pending)
        self.assertEqual(intent.slots["index"], 2)
        intent = self.check("随便", "paper_detail", pending_clarify=pending)
        self.assertEqual(intent.slots["scope"], "all")

    def test_pending_not_an_answer_is_dropped(self):
        pending = {"kind": "paper_index", "candidates": LISTED}
        intent = detect_intent("一级标题用什么字体", True, ISSUES, pending_clarify=pending)
        self.assertEqual(intent.name, "spec_qa")

    def test_pending_heading_level(self):
        pending = {"kind": "heading_level", "attribute": "字号",
                   "original_question": "标题字多大"}
        intent = self.check("二级标题", "spec_qa", pending_clarify=pending)
        self.assertEqual(intent.slots["heading_level"], "二级")
        self.assertEqual(intent.slots["attribute"], "字号")
        self.assertTrue(intent.slots["clarify_answer"])
        # 不是层级回答 -> 丢弃 pending，按新问题识别
        intent = detect_intent("都一样", True, ISSUES, pending_clarify=pending)
        self.assertEqual(intent.name, "spec_qa")
        self.assertNotIn("heading_level", intent.slots)

    def test_reasons_are_stable(self):
        self.assertEqual(detect_intent("哪里不对", True, ISSUES).reason, "R4 概览类")
        self.assertEqual(detect_intent("怎么改", True, ISSUES).reason, "R3 修改类")
        self.assertEqual(detect_intent("今天天气怎么样", True, ISSUES).reason, "R7 非论文领域")


if __name__ == "__main__":
    unittest.main()
