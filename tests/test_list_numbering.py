# -*- coding: utf-8 -*-
"""list.numbering：段首半角句点“1.”必须报出，小数/版本号不能误报。

真实 22 篇语料中，106 处“1. 列表项”全部落在 ``kind=list_item``，而规则原来的
``kinds`` 只有 ``body``，导致这条规则线上等于没查。本测试锁住修复：

- ``list_item`` 纳入扫描，解析层把“1. 系统架构”归为 ``list_item``；
- 段首“1.”报出；
- ``3.14`` / ``2.0`` / ``2020.10`` / ``1.2`` 等不报；
- 顿号、括号等旧行为不回归。
"""
import unittest

from backend.detection.semantic.deterministic import check_list_numbering
from backend.detection.semantic.rules import SEMANTIC_RULES
from backend.recognition.classification import classify_kind

LIST = next(r for r in SEMANTIC_RULES if r["rule_id"] == "list.numbering")


def _conclusions(text, kind="list_item"):
    return [i["conclusion"] for i in check_list_numbering(
        LIST, {kind: [{"index": 0, "text": text}]})]


class HalfwidthListItemTest(unittest.TestCase):
    def test_list_item_kind_is_declared(self):
        self.assertIn("list_item", LIST["kinds"])

    def test_parser_routes_halfwidth_item_to_list_item(self):
        # 修复的前提：解析层确实把“1. xxx”标成 list_item；否则只加正则仍查不到。
        self.assertEqual("list_item", classify_kind("1. 系统架构"))

    def test_halfwidth_period_is_reported(self):
        self.assertEqual(
            ["列表总项序号应采用全角“．”，不用半角句点"],
            _conclusions("1. 系统架构"),
        )

    def test_each_paragraph_is_reported(self):
        blocks = {"list_item": [
            {"index": 0, "text": "1. 系统架构"},
            {"index": 1, "text": "2. 简化复杂度"},
        ]}
        issues = check_list_numbering(LIST, blocks)
        self.assertEqual(2, len(issues))
        self.assertEqual([0, 1], [i["blocks"][0]["index"] for i in issues])

    def test_decimals_versions_and_dates_are_not_reported(self):
        for text in ("3.14 是圆周率", "版本 2.0 已发布", "2020.10 发布",
                     "1.2 研究背景", "1.5 倍"):
            with self.subTest(text=text):
                self.assertEqual([], _conclusions(text), text)

    def test_fullwidth_period_is_allowed(self):
        # “1．系统架构”是模板要求的正确写法，不能报。
        self.assertEqual([], _conclusions("1．系统架构"))

    def test_existing_punctuation_rules_still_work(self):
        self.assertTrue(_conclusions("（1）、第一项内容。"))
        self.assertTrue(_conclusions("1、扩大硬件计算集群"))


if __name__ == "__main__":
    unittest.main()
