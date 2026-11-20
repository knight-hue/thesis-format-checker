# -*- coding: utf-8 -*-
"""Agent 工具层测试：schema、参数校验、8 个工具的行为与边界。"""
import unittest

from backend.rag.dialog.agent import tools as T


def issue(rule_id="body.font_size", category="正文格式", severity="error",
          index=3, actual="10 pt", expected="12 pt", message="正文字号不符合要求",
          suggestion="请改为 12 磅", basis="正文字号：小四号字", fixable=True):
    return {
        "rule_id": rule_id,
        "category": category,
        "severity": severity,
        "message": message,
        "location": {"type": "paragraph", "index": index},
        "actual": actual,
        "expected": expected,
        "suggestion": suggestion,
        "basis": basis,
        "fixable": fixable,
        "source": "rule_engine",
    }


def context(**kwargs):
    params = {
        "college_id": "jlu_cs",
        "paper_errors": [issue()],
        "paper_total": 1,
        "paper_unchecked": [{"rule_id": "figure.inner_font_size", "reason": "位图无法判定"}],
        "last_listed": [],
    }
    params.update(kwargs)
    return T.ToolContext(**params)


class ToolSchemaTest(unittest.TestCase):
    def test_eight_tools_registered(self):
        names = [item["function"]["name"] for item in T.TOOL_SPECS]
        self.assertEqual(len(names), 8)
        self.assertEqual(set(names), set(T.PARAM_SPECS))

    def test_schema_rejects_extra_properties(self):
        for item in T.TOOL_SPECS:
            params = item["function"]["parameters"]
            self.assertEqual(params["type"], "object")
            self.assertFalse(params["additionalProperties"])
            self.assertEqual(item["type"], "function")

    def test_required_fields_declared(self):
        schema = {item["function"]["name"]: item["function"]["parameters"]
                  for item in T.TOOL_SPECS}
        self.assertEqual(schema["get_issue"]["required"], ["index"])
        self.assertEqual(schema["search_spec"]["required"], ["query"])
        self.assertEqual(schema["get_overview"]["required"], [])


class ValidateArgsTest(unittest.TestCase):
    def test_unknown_tool(self):
        self.assertTrue(T.validate_args("not_a_tool", {})[1])

    def test_unknown_argument_rejected(self):
        _clean, error = T.validate_args("get_overview", {"nope": 1})
        self.assertIn("不支持", error)

    def test_missing_required(self):
        _clean, error = T.validate_args("get_issue", {})
        self.assertIn("缺少必填参数", error)

    def test_integer_type_enforced(self):
        _clean, error = T.validate_args("get_issue", {"index": "3"})
        self.assertIn("必须是整数", error)
        _clean, error = T.validate_args("get_issue", {"index": True})
        self.assertIn("必须是整数", error)

    def test_range_enforced(self):
        self.assertIn("不能小于", T.validate_args("get_issue", {"index": 0})[1])
        self.assertIn("不能大于", T.validate_args("list_issues", {"page_size": 99})[1])

    def test_enum_enforced(self):
        _clean, error = T.validate_args("list_issues", {"severity": "fatal"})
        self.assertIn("只能是", error)

    def test_whitespace_only_string_dropped(self):
        clean, error = T.validate_args("list_issues", {"category": "   "})
        self.assertEqual(error, "")
        self.assertNotIn("category", clean)

    def test_arguments_must_be_object(self):
        self.assertTrue(T.validate_args("get_overview", "x")[1])


class ToolBehaviourTest(unittest.TestCase):
    def test_overview_counts_by_category(self):
        ctx = context(paper_errors=[issue(), issue(rule_id="table.cell_font_size",
                                                  category="表")],
                      paper_total=2)
        result = T.execute("get_overview", {}, ctx)
        self.assertTrue(result.ok)
        self.assertEqual(result.data["total"], 2)
        self.assertEqual(result.data["category_counts"], {"正文格式": 1, "表": 1})
        self.assertEqual(result.data["unchecked_count"], 1)

    def test_overview_without_paper_says_so(self):
        result = T.execute("get_overview", {}, T.ToolContext(college_id="jlu_cs"))
        self.assertTrue(result.ok)
        self.assertFalse(result.data["has_paper"])
        self.assertEqual(result.data["total"], 0)
        # 从没检测过 ≠ 检测出 0 条问题：文案必须是“尚未检测”
        self.assertIn("检测", result.data["text"])
        self.assertNotIn("0 条问题", result.data["text"])

    def test_overview_carries_college_identity(self):
        ctx = context(college_name="计算机科学与技术学院")
        result = T.execute("get_overview", {}, ctx)
        self.assertEqual(result.data["college_id"], "jlu_cs")
        self.assertEqual(result.data["college_name"], "计算机科学与技术学院")

    def test_list_issues_filters_by_severity(self):
        ctx = context(paper_errors=[issue(), issue(rule_id="note.prefix",
                                                  category="注释", severity="warning")],
                      paper_total=2)
        result = T.execute("list_issues", {"severity": "warning"}, ctx)
        self.assertTrue(result.ok)
        self.assertEqual(result.data["returned"], 1)
        self.assertEqual(result.data["items"][0]["rule_id"], "note.prefix")

    def test_list_issues_caps_page_size(self):
        self.assertTrue(T.validate_args("list_issues", {"page_size": 50})[1])

    def test_list_issues_without_paper(self):
        result = T.execute("list_issues", {}, T.ToolContext())
        self.assertFalse(result.ok)
        self.assertIn("还没有检测结果", result.error)

    def test_get_issue_by_index(self):
        first = issue(rule_id="body.font_size")
        second = issue(rule_id="table.cell_font_size", category="表", index=9)
        ctx = context(paper_errors=[first, second], paper_total=2)
        result = T.execute("get_issue", {"index": 2}, ctx)
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.data["items"][0]["rule_id"], "table.cell_font_size")
        self.assertEqual(result.data["items"][0]["actual"], "10 pt")

    def test_get_issue_out_of_range(self):
        result = T.execute("get_issue", {"index": 9}, context())
        self.assertFalse(result.ok)

    def test_get_fix_returns_suggestion(self):
        result = T.execute("get_fix", {"index": 1}, context())
        self.assertTrue(result.ok)
        self.assertEqual(result.data["items"][0]["suggestion"], "请改为 12 磅")
        self.assertTrue(result.data["items"][0]["fixable"])

    def test_get_basis_requires_selector(self):
        self.assertFalse(T.execute("get_basis", {}, context()).ok)

    def test_get_basis_by_rule_id(self):
        result = T.execute("get_basis", {"rule_id": "body.font_size"}, context())
        self.assertTrue(result.ok)
        self.assertEqual(result.data["items"][0]["basis"], "正文字号：小四号字")

    def test_get_basis_unknown_rule(self):
        self.assertFalse(T.execute("get_basis", {"rule_id": "nope.nope"}, context()).ok)

    def test_get_coverage_lists_unchecked(self):
        result = T.execute("get_coverage", {}, context())
        self.assertTrue(result.ok)
        self.assertEqual(result.data["count"], 1)
        self.assertEqual(result.data["items"][0]["rule_id"], "figure.inner_font_size")

    def test_rule_lookup(self):
        result = T.execute("rule_lookup", {"category": "正文格式"}, context())
        self.assertTrue(result.ok)
        self.assertEqual(result.data["rule_ids"], ["body.font_size"])

    def test_search_spec_without_service(self):
        result = T.execute("search_spec", {"query": "正文字号"}, context())
        self.assertFalse(result.ok)
        self.assertIn("不可用", result.error)

    def test_search_spec_handles_missing_clauses(self):
        def boom(_query):
            raise ValueError("at least one clause is required")

        ctx = context(spec_answer=boom)
        result = T.execute("search_spec", {"query": "正文字号"}, ctx)
        self.assertFalse(result.ok)
        self.assertIn("未检索到匹配条款", result.error)

    def test_search_spec_success(self):
        ctx = context(spec_answer=lambda q: {"found": True, "conclusion": "小四号",
                                             "basis": "正文…", "suggestion": "",
                                             "sources": [{"chapter": "第4章"}]})
        result = T.execute("search_spec", {"query": "正文字号"}, ctx)
        self.assertTrue(result.ok)
        self.assertEqual(result.data["conclusion"], "小四号")
        self.assertEqual(result.data["sources"][0]["chapter"], "第4章")

    def test_execute_never_raises(self):
        class Exploding:
            def __getattr__(self, name):
                raise RuntimeError("boom")

        result = T.execute("get_overview", {}, Exploding())
        self.assertFalse(result.ok)
        self.assertIn("工具执行失败", result.error)


class CardBlocksTest(unittest.TestCase):
    def test_cards_resolve_to_blocks(self):
        ctx = context()
        cards = T.build_card_blocks(ctx, [1])
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["rule_id"], "body.font_size")

    def test_out_of_range_cards_skipped(self):
        self.assertEqual(T.build_card_blocks(context(), [7, 0, "x"]), [])

    def test_cards_are_limited(self):
        issues = [issue(rule_id=f"body.rule_{n}") for n in range(1, 9)]
        ctx = context(paper_errors=issues, paper_total=8)
        self.assertLessEqual(len(T.build_card_blocks(ctx, list(range(1, 9)))), T.MAX_CARDS)


if __name__ == "__main__":
    unittest.main()
