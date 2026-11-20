# -*- coding: utf-8 -*-
"""「AI 判断，仅供参考」条目也必须带 fixable。

语义检查真正跑起来时才会产生 advisory_issues；它们与正式问题共用
IssueResponse 响应模型，fixable 是必填字段。缺少标注会让
/api/check-jobs/result 在序列化阶段抛 ResponseValidationError（500）。
"""
import unittest

from backend import api


class AnnotateResultIssuesTest(unittest.TestCase):
    def test_advisory_issues_get_fixable_and_reason(self):
        result = {
            "issues": [
                {
                    "rule_id": "figure.caption_number",
                    "category": "图题",
                    "severity": "error",
                    "location": {"type": "paragraph"},
                    "actual": "图1",
                    "expected": "图 1-1",
                    "message": "图题编号不规范",
                    "source": "rule_engine",
                }
            ],
            "advisory_issues": [
                {
                    "rule_id": "terminology",
                    "category": "名词术语",
                    "severity": "warning",
                    "location": {"type": "document"},
                    "actual": "CNN",
                    "expected": "首现注明中文含义",
                    "message": "AI 判断，仅供参考",
                    "source": "rag",
                }
            ],
        }

        api._annotate_result_issues(result, llm_available=False)

        self.assertIs(result["issues"][0]["fixable"], True)
        self.assertIs(result["advisory_issues"][0]["fixable"], False)
        self.assertIn("AI 服务不可用", result["advisory_issues"][0]["fix_reason"])

    def test_advisory_rewrite_is_fixable_when_llm_available(self):
        result = {
            "advisory_issues": [
                {
                    "rule_id": "terminology",
                    "category": "名词术语",
                    "severity": "warning",
                    "location": {"type": "document"},
                    "actual": "CNN",
                    "expected": "首现注明中文含义",
                    "message": "AI 判断，仅供参考",
                    "source": "rag",
                }
            ],
        }

        api._annotate_result_issues(result, llm_available=True)

        # terminology 在可改写白名单内：AI 就绪时允许走语义改写通道。
        self.assertIs(result["advisory_issues"][0]["fixable"], True)
        self.assertNotIn("fix_reason", result["advisory_issues"][0])

    def test_missing_lists_do_not_crash(self):
        result = {}
        api._annotate_result_issues(result, llm_available=False)
        self.assertEqual(result, {})

    def test_existing_fixable_is_not_overwritten(self):
        """聚合层已经把多项合并的修复项降级为不可修，读回时不得重标成可修。"""
        result = {
            "issues": [
                {
                    "rule_id": "table.cell_east_asia_font",
                    "source": "rule_engine",
                    "location": {"type": "table_cell", "index": 0,
                                 "row": 0, "col": 0},
                    "fixable": False,
                    "fix_reason": "该问题合并了多个位置，当前版本不支持批量自动修复",
                }
            ],
        }
        api._annotate_result_issues(result, llm_available=True)
        self.assertIs(result["issues"][0]["fixable"], False)
        self.assertEqual(result["issues"][0]["fix_reason"],
                         "该问题合并了多个位置，当前版本不支持批量自动修复")


if __name__ == "__main__":
    unittest.main()
