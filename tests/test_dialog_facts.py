# -*- coding: utf-8 -*-
"""事实层回归：论文类答案只能由 issue 字段与总数推出。"""
import re
import unittest

from backend.rag.dialog import facts

ISSUES = [
    {"rule_id": "body.font_size", "category": "正文格式", "severity": "error",
     "location": {"type": "paragraph", "index": 3}, "message": "正文字号不符合要求",
     "expected": "12 pt", "suggestion": "改为 12 磅", "source": "rule_engine"},
    {"rule_id": "caption.number", "category": "图表", "severity": "error",
     "location": {"type": "paragraph", "index": 12}, "message": "图题编号格式不符",
     "expected": "图X-Y", "suggestion": "", "source": "rule_engine"},
    {"rule_id": "reference.entry_period", "category": "参考文献", "severity": "warning",
     "location": {"type": "paragraph", "index": 20}, "message": "参考文献条目应以全角句点结束",
     "expected": "．", "suggestion": "改为全角句点", "source": "rule_engine"},
]

FORBIDDEN = ("通常", "一般", "大约", "估计", "约莫", "大概")


def _numbers(text):
    return {int(value) for value in re.findall(r"\d+", text)}


def _issue_numbers(issues):
    """issue 自身携带的数字（含定位、实测值、要求值、说明）。"""
    values = set()
    for issue in issues:
        for key in ("actual", "expected", "message", "category", "rule_id"):
            values |= _numbers(str(issue.get(key) or ""))
        location = issue.get("location")
        if isinstance(location, dict):
            for value in location.values():
                values |= _numbers(str(value))
            values |= _numbers(str(location))
    return values


class OverviewTest(unittest.TestCase):
    def test_counts_come_from_data(self):
        text, listed = facts.build_overview(ISSUES, 3)
        self.assertIn("共检测出 3 条问题", text)
        self.assertIn("正文格式：1 条", text)
        self.assertIn("图表：1 条", text)
        self.assertIn("参考文献：1 条", text)
        self.assertEqual(len(listed), 3)
        # 数字只能是总数/分类计数/列表序号，或 issue 自己携带的数字（如定位段号）
        allowed = _issue_numbers(ISSUES) | {1, 2, 3, 10}
        self.assertTrue(_numbers(text) <= allowed, _numbers(text) - allowed)

    def test_truncation_note_is_honest(self):
        text, _ = facts.build_overview(ISSUES[:2], 100)
        self.assertIn("共检测出 100 条问题", text)
        self.assertIn("本次统计基于已保存的 2 条", text)
        allowed = _issue_numbers(ISSUES[:2]) | {1, 2, 10, 100}
        self.assertTrue(_numbers(text) <= allowed, _numbers(text) - allowed)

    def test_empty_paper(self):
        text, listed = facts.build_overview([], 0)
        self.assertIn("当前记录的问题为 0 条", text)
        self.assertEqual(listed, [])

    def test_no_fuzzy_words(self):
        text, _ = facts.build_overview(ISSUES, 3)
        for word in FORBIDDEN:
            self.assertNotIn(word, text)


class DetailTest(unittest.TestCase):
    def test_detail_uses_issue_fields(self):
        text, listed = facts.build_detail(ISSUES[:2])
        self.assertIn("第 3 段", text)
        self.assertIn("正文字号不符合要求", text)
        self.assertIn("规范要求：12 pt", text)
        self.assertIn("修改建议：改为 12 磅", text)
        self.assertEqual(len(listed), 2)

    def test_fix_mode_explains_no_auto_change(self):
        text, _ = facts.build_detail([ISSUES[1]], mode="fix")
        self.assertIn("该条不提供自动修改", text)
        self.assertIn("系统不会自动修改", text)

    def test_no_items(self):
        text, listed = facts.build_detail([])
        self.assertIn("没有找到", text)
        self.assertEqual(listed, [])

    def test_no_fuzzy_words(self):
        text, _ = facts.build_detail(ISSUES)
        for word in FORBIDDEN:
            self.assertNotIn(word, text)


class SelectTest(unittest.TestCase):
    def test_index_uses_candidates(self):
        candidates = [{"rule_id": "caption.number", "location": "第 12 段",
                       "message": "图题编号格式不符"}]
        selected, reason, meta = facts.select_issues(
            ISSUES, {"scope": "index", "index": 1, "candidates": candidates})
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["rule_id"], "caption.number")
        self.assertEqual(reason, "")
        self.assertEqual(meta["matched_total"], 1)
        self.assertEqual(meta["listed"], candidates)

    def test_index_out_of_range(self):
        selected, reason, _meta = facts.select_issues(
            ISSUES, {"scope": "index", "index": 9,
                     "candidates": [{"rule_id": "x", "location": "第 1 段"}]})
        self.assertEqual(selected, [])
        self.assertIn("超出", reason)

    def test_category_filter(self):
        selected, reason, meta = facts.select_issues(
            ISSUES, {"scope": "category", "category": "图表"})
        self.assertEqual([item["rule_id"] for item in selected], ["caption.number"])
        self.assertEqual(reason, "")
        self.assertEqual(meta["matched_total"], 1)

    def test_matched_total_counts_beyond_displayed(self):
        many = [dict(ISSUES[0], message=f"字号不符 {index}") for index in range(5)]
        selected, _reason, meta = facts.select_issues(
            many, {"scope": "category", "category": "正文格式"})
        self.assertEqual(len(selected), facts.DETAIL_LIMIT)
        self.assertEqual(meta["matched_total"], 5)
        self.assertTrue(meta["has_more"])

    def test_page_scope_continues_after_first_batch(self):
        many = [dict(ISSUES[0], message=f"字号不符 {index}") for index in range(15)]
        selected, _reason, meta = facts.select_issues(many, {"scope": "page", "page": 2})
        # 第一页只列 3 条，「继续看」从第 4 条开始，不跳过中间条目
        self.assertEqual(len(selected), 10)
        self.assertEqual(meta["offset"], 3)
        self.assertEqual(meta["matched_total"], 15)
        self.assertTrue(meta["has_more"])

    def test_multiple_categories_are_combined(self):
        selected, reason, meta = facts.select_issues(ISSUES, {
            "scope": "categories",
            "categories": [{"category": "图表", "rule_ids": ["caption.number"]},
                           {"category": "参考文献", "rule_ids": ["reference.entry_period"]}],
        })
        self.assertEqual(reason, "")
        self.assertEqual({item["rule_id"] for item in selected},
                         {"caption.number", "reference.entry_period"})
        self.assertEqual(meta["matched_total"], 2)

    def test_multiple_categories_keep_one_from_each(self):
        # 排序后 figure.* 全在前面；不加保证时「表」会被挤出前 3 条
        many = [{"rule_id": f"figure.caption_number_{index}", "category": "图",
                 "severity": "error", "location": {"type": "paragraph", "index": index},
                 "message": f"图题问题 {index}"}
                for index in range(1, 4)]
        many.append({"rule_id": "table.caption_number", "category": "表",
                     "severity": "error", "location": {"type": "paragraph", "index": 9},
                     "message": "表题问题"})
        selected, _reason, meta = facts.select_issues(many, {
            "scope": "categories",
            "categories": [{"category": "图", "rule_ids": []},
                           {"category": "表", "rule_ids": []}],
        })
        self.assertEqual({item["category"] for item in selected}, {"图", "表"})
        self.assertEqual(len(selected), facts.DETAIL_LIMIT)
        self.assertEqual(meta["category_counts"], {"图": 3, "表": 1})

    def test_detail_header_matches_selection(self):
        header = facts.detail_header_matched(9, 3, 0, counts={"正文格式": 9})
        self.assertIn("正文格式 9 条；共 9 条，先列前 3 条：", header)
        self.assertEqual(facts.detail_header_matched(9, 1, 2, start_number=3),
                         "第 3 条：")


def _issue(rule_id, location, message, basis="", severity="error"):
    return {"rule_id": rule_id, "category": "正文格式", "severity": severity,
            "location": location, "message": message, "basis": basis}


class DetailTotalTest(unittest.TestCase):
    def test_more_than_displayed_says_total(self):
        text, _listed = facts.build_detail(ISSUES[:2], matched_total=9)
        self.assertIn("共 9 条，先列前 2 条", text)
        self.assertIn("继续看", text)

    def test_page_numbers_are_absolute(self):
        text, _listed = facts.build_detail(ISSUES[2:], matched_total=9, offset=2)
        self.assertIn("第 3–3 条（共 9 条）：", text)


class BasisTest(unittest.TestCase):
    def test_basis_comes_from_issue_field(self):
        issue = _issue("body.font_size", {"type": "paragraph", "index": 3},
                       "正文字号不符", basis="正文用宋体五号字。")
        text, listed = facts.build_basis([issue])
        self.assertIn("正文用宋体五号字。", text)
        self.assertIn("body.font_size", text)
        self.assertEqual(len(listed), 1)

    def test_missing_basis_is_stated_not_invented(self):
        issue = _issue("body.font_size", {"type": "paragraph", "index": 3},
                       "正文字号不符")
        text, listed = facts.build_basis([issue])
        self.assertIn("没有记录这些条目对应的模板原句", text)
        self.assertNotIn("宋体", text)
        self.assertEqual(listed, [])


class CoverageTest(unittest.TestCase):
    def test_lists_unchecked_items(self):
        text = facts.build_coverage([{"rule_id": "figure.inner_font_size",
                                      "reason": "位图文字无法自动核对"}])
        self.assertIn("没有执行", text)
        self.assertIn("figure.inner_font_size", text)
        self.assertIn("不等于通过", text)

    def test_unknown_unchecked_is_not_reported_as_checked(self):
        text = facts.build_coverage(None)
        self.assertIn("没有保存", text)
        self.assertNotIn("都已检查", text)

    def test_empty_unchecked_does_not_claim_pass(self):
        text = facts.build_coverage([])
        self.assertIn("没有未检项记录", text)
        self.assertNotIn("通过", text)


class ClarifyTest(unittest.TestCase):
    def test_paper_index_payload(self):
        payload = facts.build_clarify_paper_index(ISSUES, [])
        self.assertEqual(payload["kind"], "paper_index")
        self.assertEqual(payload["options"][0], "第 1 条")
        self.assertEqual(len(payload["options"]), min(len(ISSUES), 5))
        self.assertIn("第 12 段", payload["question"])

    def test_heading_payload(self):
        payload = facts.build_clarify_heading("字号")
        self.assertEqual(payload["kind"], "heading_level")
        self.assertEqual(payload["options"], ["一级标题", "二级标题", "三级标题"])
        self.assertIn("字号", payload["question"])


class FixedTextTest(unittest.TestCase):
    def test_missing_paper_has_no_digits(self):
        text = facts.build_missing_paper()
        self.assertFalse(re.search(r"\d", text), text)

    def test_out_of_scope_has_no_digits(self):
        text = facts.build_out_of_scope()
        self.assertFalse(re.search(r"\d", text), text)


if __name__ == "__main__":
    unittest.main()
