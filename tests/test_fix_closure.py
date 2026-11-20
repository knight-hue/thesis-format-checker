# -*- coding: utf-8 -*-
"""修复闭环守护：判定层能看到的问题，修复层必须改到同一个口径。

这些用例不是验证判定规则本身（那由 samples 真实语料与 test_rules_wiring 负责），
而是守住「判定逐 run / 修复只改代表值」「判定看首行起点 / 修复只改首行缩进」
这类会导致“修复后重检仍报同一处”的脱钩。

真实语料的整体闭环由 26 份样张审计脚本覆盖；这里是可重复的最小回归。
"""
import unittest
from unittest import mock

from docx import Document

from backend.detection.checker_service import check_document
from backend.fix._core import _set_paragraph_text
from backend.fix.fix_blocks import fix_document
from backend.fix.fixability import classify_fixability
from backend.write_back._patch.paragraph import patch_paragraph_runs

RULES_DIR = "config/rules"


class _Report:
    def __init__(self):
        self.skips = []

    def skip(self, reason, where=None):
        self.skips.append(reason)
        return False


def _document(blocks):
    return {"schema_version": "1.0", "college_id": "jlu_cs",
            "sections": [{}], "blocks": blocks}


def _rule_ids(document):
    result = check_document(document, "jlu_cs", rag_service=None,
                            include_semantic=False, rules_dir=RULES_DIR)
    return [issue.get("rule_id") for issue in result["issues"]]


class TableCellFixClosureTest(unittest.TestCase):
    """表内字体/字号按 run 判定，修复必须改到每个 run。"""

    def _table(self, east_asia="黑体", size=12.0):
        run = {"text": "中文", "font_name": "宋体",
               "east_asia_font": east_asia, "font_size_pt": size}
        return {"type": "table", "index": 0, "cells": [
            {"row": 0, "col": 0, "text": "中文",
             "east_asia_font": east_asia, "font_size_pt": size, "runs": [run]}]}

    def test_cell_font_fix_reaches_every_run(self):
        document = _document([self._table()])
        self.assertIn("table.cell_east_asia_font", _rule_ids(document))
        issue = {"rule_id": "table.cell_east_asia_font", "source": "rule_engine",
                 "category": "表", "severity": "error",
                 "location": {"type": "table_cell", "index": 0, "row": 0, "col": 0},
                 "actual": "黑体", "expected": "宋体", "message": "表内中文字体不符合要求"}
        fix_document(document, [issue], college_id="jlu_cs", rag_service=None)
        cell = document["blocks"][0]["cells"][0]
        self.assertEqual(cell["east_asia_font"], "宋体")
        self.assertEqual(cell["runs"][0]["east_asia_font"], "宋体")
        self.assertNotIn("table.cell_east_asia_font", _rule_ids(document))

    def test_cell_font_size_fix_reaches_every_run(self):
        document = _document([self._table(size=12.0)])
        self.assertIn("table.cell_font_size", _rule_ids(document))
        issue = {"rule_id": "table.cell_font_size", "source": "rule_engine",
                 "category": "表", "severity": "error",
                 "location": {"type": "table_cell", "index": 0, "row": 0, "col": 0},
                 "actual": "12 pt", "expected": "10.5 pt", "message": "表内字号不符合要求"}
        fix_document(document, [issue], college_id="jlu_cs", rag_service=None)
        cell = document["blocks"][0]["cells"][0]
        self.assertEqual(cell["font_size_pt"], 10.5)
        self.assertEqual(cell["runs"][0]["font_size_pt"], 10.5)
        self.assertNotIn("table.cell_font_size", _rule_ids(document))


class ReferenceIndentFixClosureTest(unittest.TestCase):
    """顶格判定看首行起点 = 左缩进 + 首行缩进；修复必须两项一起归零。"""

    def test_entry_indent_fix_clears_left_and_first_line(self):
        block = {"type": "paragraph", "index": 0, "kind": "reference_entry",
                 "text": "张三. 论文[J].",
                 "left_indent_pt": 29.1, "first_line_indent_pt": -22.0,
                 "runs": [{"text": "张三. 论文[J].", "font_name": "宋体",
                           "east_asia_font": "宋体", "font_size_pt": 10.5}]}
        document = _document([block])
        self.assertIn("reference.entry_indent", _rule_ids(document))
        issue = {"rule_id": "reference.entry_indent", "source": "rule_engine",
                 "category": "参考文献", "severity": "warning",
                 "location": {"type": "paragraph", "index": 0},
                 "actual": "7.1 pt", "expected": "0 pt", "message": "应左顶格"}
        fix_document(document, [issue], college_id="jlu_cs", rag_service=None)
        self.assertEqual(block["left_indent_pt"], 0.0)
        self.assertEqual(block["first_line_indent_pt"], 0.0)
        self.assertNotIn("reference.entry_indent", _rule_ids(document))


class ContinuedNoteAlignmentFixTest(unittest.TestCase):
    """续表标注按到版心左边界的距离修，而不是把整句 expected 写进 alignment。"""

    def test_alignment_fix_uses_distance_from_expected(self):
        block = {"type": "paragraph", "index": 0, "kind": "table_continued_note",
                 "text": "续表", "alignment": "left",
                 "left_indent_pt": 0.0, "first_line_indent_pt": 0.0,
                 "runs": [{"text": "续表", "font_name": "宋体",
                           "east_asia_font": "宋体", "font_size_pt": 10.5}]}
        document = _document([block])
        issue = {"rule_id": "table.continued_note_alignment", "source": "rule_engine",
                 "category": "表", "severity": "warning",
                 "location": {"type": "paragraph", "index": 0},
                 "actual": "续表距版心左边界 0.00 cm",
                 "expected": "续表从表格左边线起排（距版心左边界 2.38 cm）",
                 "message": "续表标注未与表格左边线对齐"}
        fix_document(document, [issue], college_id="jlu_cs", rag_service=None)
        self.assertEqual(block["alignment"], "left")
        self.assertEqual(block["first_line_indent_pt"], 0.0)
        self.assertAlmostEqual(block["left_indent_pt"], 2.38 * 28.3465, places=3)


class AutoNumberFixabilityTest(unittest.TestCase):
    """自动编号：判定能还原，修复层改 numbering.xml 的 numFmt，保留自动编号。"""

    def test_auto_numbered_heading_is_fixable(self):
        block = {"type": "paragraph", "index": 0, "kind": "heading_1",
                 "text": "绪 论", "auto_number": True,
                 "auto_number_text": "第一章"}
        document = _document([block])
        result = check_document(document, "jlu_cs", rag_service=None,
                                include_semantic=False, rules_dir=RULES_DIR)
        issue = next(item for item in result["issues"]
                     if item.get("rule_id") == "heading.heading_1.number")
        self.assertTrue(issue.get("auto_number"))
        fixable, reason = classify_fixability(issue)
        self.assertTrue(fixable)
        self.assertEqual(reason, "")


class LatinFontPreservationTest(unittest.TestCase):
    """语义改写新产生的数字/西文必须沿用原段落的西文字体。"""

    def test_semantic_rewrite_keeps_latin_font(self):
        block = {"text": "从图中可以看出2018年",
                 "runs": [
                     {"text": "从图中可以看出", "font_name": "宋体",
                      "east_asia_font": "宋体", "font_size_pt": 12},
                     {"text": "2018", "font_name": "Times New Roman",
                      "east_asia_font": "宋体", "font_size_pt": 12},
                     {"text": "年", "font_name": "宋体",
                      "east_asia_font": "宋体", "font_size_pt": 12},
                 ]}
        _set_paragraph_text(block, "从图中可以看出2019年增长5", preserve_latin=True)
        self.assertEqual(block["text"], "从图中可以看出2019年增长5")
        texts = [run["text"] for run in block["runs"]]
        self.assertEqual("".join(texts), block["text"])
        latin_runs = [run for run in block["runs"] if run["text"] == "2019"]
        self.assertEqual(latin_runs[0]["font_name"], "Times New Roman")
        self.assertEqual(block["runs"][-1]["text"], "5")
        self.assertEqual(block["runs"][-1]["font_name"], "Times New Roman")

    def test_writeback_rebuilds_multi_run_paragraph(self):
        doc = Document()
        paragraph = doc.add_paragraph()
        paragraph.add_run("中文")

        old = {"text": "中文", "runs": [
            {"text": "中文", "font_name": "宋体", "east_asia_font": "宋体"}]}
        new = {"text": "中文5年", "runs": [
            {"text": "中文", "font_name": "宋体", "east_asia_font": "宋体"},
            {"text": "5", "font_name": "Times New Roman", "east_asia_font": "宋体"},
            {"text": "年", "font_name": "宋体", "east_asia_font": "宋体"},
        ]}
        # 模拟 rewrite_paragraph_text 已经把整段文本改好，只剩 run 结构待重建。
        paragraph.runs[0].text = "中文5年"

        report = _Report()
        self.assertTrue(patch_paragraph_runs(paragraph, old, new, report, "test"))
        self.assertEqual(paragraph.text, "中文5年")
        runs = paragraph.runs
        self.assertEqual([run.text for run in runs], ["中文", "5", "年"])
        self.assertEqual(runs[1].font.name, "Times New Roman")
        self.assertEqual(runs[0].font.name, "宋体")
        self.assertFalse(report.skips)


class SemanticRewriteChannelTest(unittest.TestCase):
    """语义 issue（source=rag）走 LLM 改写通道：固定改写结果下要看得到写入与字体保留。"""

    def test_rag_issue_rewrites_block_and_keeps_latin_font(self):
        block = {
            "type": "paragraph", "index": 0, "text": "五年间增长",
            "runs": [
                {"text": "五年间增长", "font_name": "宋体",
                 "east_asia_font": "宋体", "font_size_pt": 12},
                {"text": "2018", "font_name": "Times New Roman",
                 "east_asia_font": "宋体", "font_size_pt": 12},
            ],
        }
        document = _document([block])
        issue = {
            "rule_id": "numerals", "source": "rag", "category": "数字用法",
            "severity": "warning",
            "location": {"type": "document", "kinds": ["body"]},
            "blocks": [{"index": 0}],
            "actual": "五年", "expected": "测量统计数据一律用阿拉伯数字。",
            "message": "测量统计数据未使用阿拉伯数字", "fixable": True,
        }

        class FakeRag:
            def rewrite_passage(self, college_id, task):
                return {"changed": True, "rewritten": "5年间增长2018"}

        fixed = fix_document(document, [issue], college_id="jlu_cs",
                             rag_service=FakeRag())
        self.assertEqual(fixed["applied_count"], 1)
        self.assertEqual(block["text"], "5年间增长2018")
        latin_runs = [run for run in block["runs"] if run["text"] == "5"]
        self.assertEqual(latin_runs[0]["font_name"], "Times New Roman")


if __name__ == "__main__":
    unittest.main()
