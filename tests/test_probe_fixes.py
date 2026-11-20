# -*- coding: utf-8 -*-
"""最深探针（独立复现原始 XML）查出的 6 处判据问题，逐条钉住。

每一条都记着"为什么"和实测规模，防止以后被改回去。
"""
import io
import json
import os
import unittest

from docx.oxml import parse_xml

from backend.parser.font_reader import _indent_from_chain, _left_indent_from_chain
from backend.detection.rule_engine import RuleEngine
from backend.detection.rules.formula import is_standalone_formula
from backend.detection.semantic.deterministic import check_numerals
from backend.detection.semantic.rules import SEMANTIC_RULES

from backend.college_registry import path_of

RULES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "config", "rules")
W = ("http://schemas.openxmlformats.org/wordprocessingml/2006/main")


def load_rules(college="jlu_cs"):
    with io.open(path_of(college, "rules", RULES_DIR),
                 encoding="utf-8") as handle:
        return json.load(handle)


def rule_ids(document, college="jlu_cs"):
    return [issue["rule_id"] for issue in
            RuleEngine(load_rules(college)).check(document)["issues"]]


def body_block(index, runs):
    return {"type": "paragraph", "index": index, "kind": "body",
            "text": "".join(r.get("text") or "" for r in runs),
            "alignment": "justify", "runs": runs}


def body_run(text, *, size=12.0, cjk="宋体", latin="Times New Roman"):
    return {"text": text, "font_name": latin, "east_asia_font": cjk,
            "font_size_pt": size, "bold": False}


class WhitespaceRunTest(unittest.TestCase):
    """① 纯空白 run 不渲染可见字符，其字号不参与正文格式判定。

    实测 79 条“正文字号不符合要求”打在只有一个空格的 run 上（杨明旭块 152 等）；
    base.py 的 _check_run_fonts 一直是跳过空白 run 的，正文检查漏了这一刀。
    """

    def test_whitespace_run_size_is_not_reported(self):
        block = body_block(0, [body_run("正文"), body_run("   ", size=10.5)])
        self.assertNotIn("body.font_size", rule_ids({"blocks": [block]}))

    def test_visible_run_size_is_still_reported(self):
        block = body_block(0, [body_run("正文", size=10.5)])
        self.assertIn("body.font_size", rule_ids({"blocks": [block]}))


class NumeralsUnitStartTest(unittest.TestCase):
    """④ 汉字数字必须"自己起头"：`4万亿元` 里的"亿"是单位的一部分，不是汉字数字。"""

    NUMERALS = next(r for r in SEMANTIC_RULES if r["rule_id"] == "numerals")

    def _hit(self, text):
        return bool(check_numerals(self.NUMERALS,
                                   {"body": [{"index": 0, "text": text}]}))

    def test_arabic_number_with_scale_unit_is_not_reported(self):
        self.assertFalse(self._hit("个人消费贷总规模已超过4万亿元，同比增长24.6%。"))

    def test_chinese_measurement_is_still_reported(self):
        self.assertTrue(self._hit("其航运货运量每年都在七亿吨以上。"))
        self.assertTrue(self._hit("上海市航运总量有望突破十亿吨。"))
        self.assertTrue(self._hit("项目总投入达到五亿元。"))

    def test_time_condition_is_not_reported(self):
        """“一小时内”是时间条件，不是“测量统计数据”里的数值。"""
        self.assertFalse(self._hit("系统运行一小时内，操作持续进行且页面未卡顿。"))


class TextFormulaStandaloneTest(unittest.TestCase):
    """⑤ 以句末标点收尾的段落不是公式。

    解析层对纯文本公式会把整段文字填进 formula_text，扣减后残句恒为空 →
    整句被当独立公式，"编号置于行末"报在散文上（实测 1 条）。
    """

    PROSE = {"text": "逻辑回归用一组权重w=[w1，w2，w3]以及偏置b来得到最终的融合概率"
                     "如公式（3-5）所示。",
             "formula_number": "（3-5）",
             "formula_text": "逻辑回归用一组权重w=[w1，w2，w3]以及偏置b来得到最终的"
                             "融合概率如公式所示。"}
    DISPLAY = {"text": "Fm(x)=Fm-1(x)+ηhm(x)              （2-4）",
               "formula_number": "（2-4）",
               "formula_text": "Fm(x)=Fm-1(x)+ηhm(x)"}

    def test_sentence_is_not_a_formula(self):
        self.assertFalse(is_standalone_formula(self.PROSE))

    def test_display_formula_is_still_a_formula(self):
        self.assertTrue(is_standalone_formula(self.DISPLAY))

    def test_number_position_is_not_reported_on_the_sentence(self):
        rules = {"formula": {"alignment": "center", "number_position": "end",
                             "check_continuity": False,
                             "number_pattern": r"[(（]\d+-\d+[)）]"}}
        blocks = [{"type": "paragraph", "index": 0, "kind": "formula",
                   "alignment": "justify", **self.PROSE}]
        ids = [i["rule_id"] for i in RuleEngine(rules).check({"blocks": blocks})["issues"]]
        self.assertNotIn("formula.number_position", ids)


class ReferenceIndentStartTest(unittest.TestCase):
    """③ “序号左顶格”要比**首行起点**（左缩进 + 首行缩进），不是首行缩进本身。

    参考文献通用悬挂缩进（w:left=420 + w:hanging=420）时首行正好落在左边线上，
    只比首行缩进会报 -21 pt 的假错（实测 26 条）。
    """

    def _ids(self, left, first):
        block = {"type": "paragraph", "index": 0, "kind": "reference_entry",
                 "text": "［1］作者. 题名[J]. 刊名, 2020, 1(1): 1-2.",
                 "alignment": "left", "left_indent_pt": left,
                 "first_line_indent_pt": first,
                 "runs": [body_run("［1］作者. 题名[J]. 刊名, 2020, 1(1): 1-2.",
                                   size=10.5, cjk="宋体")]}
        return rule_ids({"blocks": [block]})

    def test_hanging_indent_is_left_flush(self):
        self.assertNotIn("reference.entry_indent", self._ids(21.0, -21.0))

    def test_left_offset_is_reported(self):
        self.assertIn("reference.entry_indent", self._ids(24.0, 0.0))

    def test_first_line_indent_is_reported(self):
        self.assertIn("reference.entry_indent", self._ids(0.0, 24.0))


class IndentCharsZeroTest(unittest.TestCase):
    """⑥ 字符单位写 0 时视同未设置，回落到 twips 值（Word 就是这么解释的）。

    实测 11 份样张有 `w:firstLineChars="0"` + 非零 `w:firstLine`、
    9 份有 `w:leftChars="0"` + 非零 `w:left`；照字面取 0 会把真实缩进读成 0。
    """

    def _chain(self, attrs):
        return [parse_xml(
            '<w:pPr xmlns:w="%s"><w:ind %s/></w:pPr>' % (W, attrs))]

    def test_chars_zero_falls_back_to_twips(self):
        chain = self._chain('w:left="420" w:leftChars="0" '
                            'w:hanging="420" w:hangingChars="200"')
        self.assertEqual(-21.0, _indent_from_chain(chain, 10.5))
        self.assertEqual(21.0, _left_indent_from_chain(chain, 10.5))

    def test_real_chars_value_still_wins(self):
        chain = self._chain('w:firstLineChars="200" w:firstLine="480"')
        self.assertEqual(24.0, _indent_from_chain(chain, 12.0))


class LeftIndentExplicitZeroTest(unittest.TestCase):
    """左缩进：段自身写了 0 就到此为止，不再继承样式链（口径同 _indent_value）。

    WPS 文档常给段落写 `w:leftChars="0" w:left="0"` 这类显式零——孙晟样张的目录
    条目段自身双零、样式链却带着 210/420/1260 twips。直接格式覆盖样式：写了 0
    就是 0；曾有一个“零视同未设置、继续回退”的副本遮蔽正确实现，把这类段落读成
    样式值（30 块，10.5/21/63 pt；2026-10-12 收敛为一份实现）。
    """

    def _chain(self, own_attrs, style_attrs='w:left="210"'):
        return [
            parse_xml('<w:pPr xmlns:w="%s"><w:ind %s/></w:pPr>' % (W, own_attrs)),
            parse_xml('<w:pPr xmlns:w="%s"><w:ind %s/></w:pPr>' % (W, style_attrs)),
        ]

    def test_double_zero_stops_style_inheritance(self):
        chain = self._chain('w:left="0" w:leftChars="0"')
        self.assertEqual(0.0, _left_indent_from_chain(chain, 10.5))

    def test_zero_chars_without_twips_stops(self):
        chain = self._chain('w:leftChars="0"')
        self.assertEqual(0.0, _left_indent_from_chain(chain, 10.5))

    def test_zero_twips_without_chars_stops(self):
        chain = self._chain('w:left="0"')
        self.assertEqual(0.0, _left_indent_from_chain(chain, 10.5))

    def test_missing_left_still_inherits_style(self):
        """完全没写左缩进时仍按样式继承——停止回退只针对“写了 0”。"""
        chain = self._chain('w:firstLine="420"')
        self.assertEqual(10.5, _left_indent_from_chain(chain, 10.5))


class TableCellRunTest(unittest.TestCase):
    """② 表内字体/字号按 run 判，不按“单元格代表值”判。

    单元格代表值（font_name / east_asia_font / font_size_pt）取的是第一个有文字的
    run，而一个格里的字体字号常混着（标题行五号、数据行 9 磅）。只按代表值判，同格
    其余 run 的不合规就漏了。
    """

    def _ids(self, runs, text):
        cell = {"row": 0, "col": 0, "text": text, "runs": runs,
                "font_name": runs[0].get("font_name"),
                "east_asia_font": runs[0].get("east_asia_font"),
                "font_size_pt": runs[0].get("font_size_pt")}
        table = {"type": "table", "index": 0, "n_rows": 1, "n_cols": 1,
                 "cells": [cell]}
        return rule_ids({"blocks": [table]})

    def test_later_run_size_is_still_reported(self):
        """代表值（第一个 run）是 10.5 磅合规，第二个 run 9 磅仍要报出来。"""
        runs = [body_run("字段", cjk="宋体", latin="宋体", size=10.5),
                body_run("2020", cjk=None, latin="宋体", size=9.0)]
        self.assertIn("table.cell_font_size", self._ids(runs, "字段2020"))

    def test_later_run_cjk_font_is_still_reported(self):
        """代表值的中文字体是宋体合规，第二个 run 用了黑体仍要报出来。"""
        runs = [body_run("字段", cjk="宋体", latin="宋体", size=10.5),
                body_run("名称", cjk="黑体", latin="宋体", size=10.5)]
        self.assertIn("table.cell_east_asia_font", self._ids(runs, "字段名称"))

    def test_whitespace_run_in_cell_is_skipped(self):
        runs = [body_run("字段", cjk="宋体", latin="宋体", size=10.5),
                body_run("   ", cjk="宋体", latin="宋体", size=9.0)]
        self.assertNotIn("table.cell_font_size", self._ids(runs, "字段   "))


if __name__ == "__main__":
    unittest.main()
