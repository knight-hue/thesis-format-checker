# -*- coding: utf-8 -*-
"""批量修复：同规则多处聚合后，一次提交要能全部改到。

判定层把同一规则的多处问题聚合成一条（occurrence_count），修复层过去只认
一个 location，只能改第一处；现在 issue 带 locations（全部位置），
/api/fix 展开后逐处应用，并只在“每一处都改成功”时才把该条从列表移除。
"""
import unittest

from backend.detection.issue_compaction import compact_check_result
from backend.fix.applied_match import fixed_issue_indexes
from backend.fix.fix_blocks import fix_document


def _cell_issue(row):
    return {
        "rule_id": "table.cell_east_asia_font",
        "category": "表",
        "severity": "error",
        "location": {"type": "table_cell", "index": 0, "row": row, "col": 0},
        "actual": "黑体",
        "expected": "宋体",
        "message": "表内中文字体不符合要求",
        "source": "rule_engine",
        "fixable": True,
    }


def _document(rows):
    cells = []
    for row in rows:
        cells.append({
            "row": row, "col": 0, "text": "中文",
            "east_asia_font": "黑体", "font_size_pt": 10.5,
            "runs": [{"text": "中文", "font_name": "宋体",
                      "east_asia_font": "黑体", "font_size_pt": 10.5}],
        })
    return {"schema_version": "1.0", "college_id": "jlu_cs",
            "sections": [{}],
            "blocks": [{"type": "table", "index": 0, "cells": cells}]}


class CompactionLocationsTest(unittest.TestCase):
    def test_grouped_fixable_issue_keeps_all_locations(self):
        result = {"issues": [_cell_issue(0), _cell_issue(1), _cell_issue(2)]}
        compacted = compact_check_result(result, max_returned=100,
                                         max_location_samples=10)
        issue = compacted["issues"][0]
        self.assertEqual(issue["occurrence_count"], 3)
        self.assertTrue(issue["fixable"])
        self.assertEqual(len(issue["locations"]), 3)
        self.assertEqual([loc["row"] for loc in issue["locations"]], [0, 1, 2])

    def test_unfixable_group_does_not_carry_locations(self):
        issue = _cell_issue(0)
        issue["fixable"] = False
        issue["fix_reason"] = "人工处理"
        compacted = compact_check_result(
            {"issues": [issue, _cell_issue(1)]}, max_returned=100,
            max_location_samples=10)
        self.assertNotIn("locations", compacted["issues"][0])


class BatchFixTest(unittest.TestCase):
    def _grouped_issue(self, rows):
        result = {"issues": [_cell_issue(row) for row in rows]}
        return compact_check_result(result, max_returned=100,
                                    max_location_samples=10)["issues"][0]

    def test_fix_document_changes_every_location(self):
        issue = self._grouped_issue([0, 1, 2])
        document = _document([0, 1, 2])
        fixed = fix_document(document, [issue], college_id="jlu_cs",
                             rag_service=None)
        self.assertEqual(fixed["applied_count"], 3)
        for cell in document["blocks"][0]["cells"]:
            self.assertEqual(cell["east_asia_font"], "宋体")
            self.assertEqual(cell["runs"][0]["east_asia_font"], "宋体")

    def test_all_locations_must_succeed_to_remove_issue(self):
        issue = self._grouped_issue([0, 1])
        document = _document([0, 1])
        fixed = fix_document(document, [issue], college_id="jlu_cs",
                             rag_service=None)
        self.assertEqual(fixed_issue_indexes([issue], fixed), [0])

    def test_partial_success_keeps_issue_in_list(self):
        issue = self._grouped_issue([0, 1])
        # 第二处不在本次文档里：只能改第一处，整条不能算修复。
        document = _document([0])
        fixed = fix_document(document, [issue], college_id="jlu_cs",
                             rag_service=None)
        self.assertEqual(fixed["applied_count"], 1)
        self.assertEqual(fixed_issue_indexes([issue], fixed), [])


if __name__ == "__main__":
    unittest.main()
