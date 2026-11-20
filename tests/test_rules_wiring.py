# -*- coding: utf-8 -*-
"""规则接线检查：config/rules/*.json 与代码之间的对应关系。

这几类脱钩都不会抛异常，只会让检测结论悄悄偏离规范，所以在这里断言下来：

1. config/rules 里的每个字段都必须真的被代码读取（否则是只有文档作用的"死配置"）；
2. 代码里硬编码的规则常量必须与 rules 对齐（否则改了 rules 仍按旧值走）；
3. 声明"可配置"的判定项改了 rules 之后，检测结论必须跟着变；
4. 声明的文字类可修复规则必须真有修复器，且修复后该规则不再报错；
5. 分类层与规则层对同一件事的判定不能互斥（互斥 = 该检查永不触发）。
"""
import json
import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import backend.api as api
from backend.detection.checker_service import check_document
from backend.detection.rule_engine import RuleEngine
from backend.detection.semantic.deterministic import check_reference, check_units
from backend.detection.semantic_checker import (
    DETERMINISTIC_SEMANTIC_RULES,
    LOCATED_SEMANTIC_RULES,
    MANUAL_ONLY_SEMANTIC_RULES,
    REWRITABLE_SEMANTIC_RULES,
    SEMANTIC_RULES,
    SemanticChecker,
)
from backend.fix._core import _CAPTION_FONT, _TEXT_LAYER_RULE_IDS
from backend.fix.fixability import annotate_issues, classify_fixability
from backend.recognition.classification import classify_kind
from backend.recognition.context import apply_contextual_kinds
from backend.rag.text_rewrite_service import (
    _DETERMINISTIC_FIXERS,
    _fix_body_fullwidth_punct,
    _fix_caption_number_space,
    _fix_heading_1_number,
    _fix_keyword_terminal_punct,
    _fix_note_prefix,
    _fix_reference_entry_number,
    _fix_reference_period,
    generate_text_fixes,
)

from backend.college_registry import path_of, rule_sets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_DIR = os.path.join(ROOT, "config", "rules")

# 死配置判据只看生产代码：tests/ 里出现某个键名不算"代码在读它"。
_SRC_DIRS = tuple("backend/%s" % name for name in
                  ("detection", "fix", "parser", "rag", "recognition",
                   "write_back", "docx_common"))
_COLLEGES = ("jlu_cs", "jlu_se")

# 官方模板原文里这几个标题的空白是模板自己排的（各 4 个半角空格），
# 逐字照抄才算与模板一致；表示例里写成全角空格或换成别的空格数量都要报。
# （目录标题已撤，只剩致谢这一处。）
TPL_ACK_TITLE = "致" + " " * 4 + "谢"


def _rules(college_id):
    path = path_of(college_id, "rules", RULES_DIR)
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _paragraph(index, kind, text, **extra):
    block = {
        "type": "paragraph",
        "index": index,
        "kind": kind,
        "text": text,
        "runs": [{"text": text, "font_name": "宋体", "east_asia_font": "宋体",
                  "font_size_pt": 12, "bold": False}],
    }
    block.update(extra)
    return block


def _rule_ids(document, rules):
    return [issue["rule_id"] for issue in RuleEngine(rules).check(document)["issues"]]


class RevokedStandardTest(unittest.TestCase):
    """按模板真标准撤销的检查不许复活。

    这些项当年是照“模板自己的排版结果”卡学生的（模板里没有对应的说明句）：
    正文对齐、正文首行缩进、各级标题加粗、关键词与标签同行、纸张尺寸/方向、
    页眉文字与格式、页码、前置部分页眉、目录整块。撤销后必须真的不再报——
    谁把 rules 键或代码分支加回来，这里就红。
    """

    # 会踩到上述各项的一段正文 + 各级标题 + 关键词
    def _document(self):
        def run(text, size, font="宋体", bold=False):
            return {"text": text, "font_name": font, "east_asia_font": font,
                    "font_size_pt": size, "bold": bold}

        return {
            "sections": [{"index": 0, "paper_size": "Letter",
                          "orientation": "landscape",
                          "header": {"text": "随便什么页眉", "font_size_pt": 22,
                                     "alignment": "right", "font_name": "宋体"},
                          "footer": {"has_page_number": False}}],
            "blocks": [
                _paragraph(0, "heading_1", "第1章 绪论", alignment="right",
                           first_line_indent_pt=42.0,
                           runs=[run("第1章 绪论", 9.0, "楷体", bold=False)]),
                _paragraph(1, "heading_2", "1.1 研究背景", alignment="right",
                           first_line_indent_pt=42.0,
                           runs=[run("1.1 研究背景", 9.0, "楷体", bold=False)]),
                _paragraph(2, "heading_3", "1.1.1 细节", alignment="right",
                           first_line_indent_pt=42.0,
                           runs=[run("1.1.1 细节", 9.0, "楷体", bold=False)]),
                _paragraph(3, "body", "正文段落，故意右对齐并缩进两格。",
                           alignment="right", first_line_indent_pt=42.0),
                _paragraph(4, "keyword_title", "关键词：", alignment="right",
                           runs=[run("关键词：", 14.0, "黑体")]),
                _paragraph(5, "keyword_body", "甲，乙，丙", alignment="right",
                           runs=[run("甲，乙，丙", 14.0, "黑体")]),
            ],
        }

    REVOKED_RULE_IDS = (
        "body.alignment",
        "body.first_line_indent",
        "heading.heading_1.bold",
        "heading.heading_2.bold",
        "heading.heading_3.bold",
        "page.paper_size",
        "page.orientation",
        "page.header_text",
        "page.header_missing",
        "page.header_font",
        "page.header_font_size",
        "page.header_alignment",
        "page.header_in_front_matter",
        "page.footer_page_number",
    )

    def test_revoked_rule_ids_never_fire(self):
        for college in _COLLEGES:
            ids = _rule_ids(self._document(), _rules(college))
            for rule_id in self.REVOKED_RULE_IDS:
                with self.subTest(college=college, rule_id=rule_id):
                    self.assertNotIn(rule_id, ids)

    def test_no_rule_id_starts_with_a_revoked_prefix(self):
        """目录整块与“关键词同行”是按前缀成片的，逐条列举容易漏，按前缀断言。"""
        for college in _COLLEGES:
            ids = _rule_ids(self._document(), _rules(college))
            for rule_id in ids:
                with self.subTest(college=college, rule_id=rule_id):
                    self.assertFalse(rule_id.startswith("toc."), rule_id)
                    self.assertFalse(rule_id.endswith(".same_line"), rule_id)

    def test_revoked_keys_are_not_in_rules(self):
        for college in _COLLEGES:
            rules = _rules(college)
            page = rules.get("page") or {}
            body = rules.get("body") or {}
            with self.subTest(college=college):
                self.assertNotIn("toc", rules, "目录检查整块已撤销")
                self.assertNotIn("paper_size", page)
                self.assertNotIn("orientation", page)
                for key in ("header", "footer", "header_text", "footer_text"):
                    self.assertNotIn(key, page)
                self.assertNotIn("alignment", body, "正文对齐已撤销")
                self.assertNotIn("first_line_indent_pt", body, "正文首行缩进已撤销")
                for level in ("heading_1", "heading_2", "heading_3"):
                    self.assertNotIn("bold", rules.get("headings", {}).get(level) or {})

    def test_author_info_only_where_the_template_prints_it(self):
        """软院模板写了“Author: …（居右）”，计算机模板没有这一行。"""
        self.assertIn("author_info", _rules("jlu_se"))
        self.assertNotIn("author_info", _rules("jlu_cs"))


def _semantic_document():
    """6 条零 LLM 语义条款各踩一处的合同，用于验证无 AI 服务时的降级行为。

    目录条款整块已撤销（模板里没有目录排版说明），这里只按仍生效的条款各踩一处。
    """
    return {
        "sections": [],
        "blocks": [
            _paragraph(0, "body", "试验路段长度 12 公里，样本量约为百分之二十五。"),
            _paragraph(1, "body", "（1）、第一项；2、第二项。"),
            _paragraph(2, "acknowledgement_body", "从 2020 年到 2023 年，感谢导师的指导。"),
            _paragraph(3, "reference_entry", "张三. 深度学习综述. 计算机学报, 2020."),
        ],
    }


def _production_sources():
    """把所有第一方 .py 源码拼成一段文本，用于判断某个键名是否被读到。"""
    chunks = []
    for rel in _SRC_DIRS:
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, rel)):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in filenames:
                if name.endswith(".py"):
                    with open(os.path.join(dirpath, name), encoding="utf-8") as handle:
                        chunks.append(handle.read())
    for name in os.listdir(os.path.join(ROOT, "backend")):
        if name.endswith(".py"):
            with open(os.path.join(ROOT, "backend", name), encoding="utf-8") as handle:
                chunks.append(handle.read())
    return "\n".join(chunks)


def _leaf_keys(node, out):
    """rules 里所有"配置项"的键名：标量值，以及不含字典的列表值。"""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, dict):
                _leaf_keys(value, out)
            elif isinstance(value, list):
                if any(isinstance(item, dict) for item in value):
                    _leaf_keys(value, out)
                else:
                    out.add(key)
            else:
                out.add(key)
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, dict):
                _leaf_keys(item, out)


class RulesConfigWiringTest(unittest.TestCase):
    """rules 的字段必须有人读，代码里的常量必须与 rules 对齐。"""

    def test_no_dead_rules_key(self):
        sources = _production_sources()
        keys = set()
        for _cid, path in rule_sets(RULES_DIR):
            # 只看判定配置：依据表（*_basis.json）的键是规则号前缀，不是配置项，
            # 它由 BasisCoverageTest 单独守。
            with open(path, encoding="utf-8") as handle:
                _leaf_keys(json.load(handle), keys)
        self.assertGreater(len(keys), 0)
        dead = [key for key in sorted(keys)
                if f'"{key}"' not in sources and f"'{key}'" not in sources]
        self.assertEqual([], dead, f"rules 里存在无人读取的配置项：{dead}")

    def test_caption_position_and_formula_number_position_are_valid(self):
        for college in _COLLEGES:
            rules = _rules(college)
            for section in ("tables", "figures"):
                with self.subTest(college=college, section=section):
                    self.assertIn(rules[section]["caption"]["position"], ("above", "below"))
            with self.subTest(college=college, section="formula"):
                self.assertIn(rules["formula"]["number_position"], ("start", "begin", "end"))

    def test_caption_font_constant_matches_rules(self):
        for college in _COLLEGES:
            rules = _rules(college)
            for section in ("tables", "figures"):
                caption = rules[section]["caption"]
                with self.subTest(college=college, section=section):
                    self.assertEqual(_CAPTION_FONT["font_name"], caption["font_name"])
                    self.assertEqual(_CAPTION_FONT["font_size_pt"], caption["font_size_pt"])


class RuleConfigEffectTest(unittest.TestCase):
    """改了 rules 的判定项，检测结论必须跟着变。"""

    def test_table_caption_position_follows_rules(self):
        rules = _rules("jlu_cs")
        caption = _paragraph(1, "table_caption", "表1-1 数据")
        table = {"type": "table", "index": 0, "cells": []}
        above = {"blocks": [caption, table]}
        below = {"blocks": [table, caption]}

        # 缺省 above：表题在上方不报，在下方报。
        self.assertNotIn("table.caption_position", _rule_ids(above, rules))
        self.assertIn("table.caption_position", _rule_ids(below, rules))

        rules["tables"]["caption"]["position"] = "below"
        self.assertNotIn("table.caption_position", _rule_ids(below, rules))
        self.assertIn("table.caption_position", _rule_ids(above, rules))

    def test_figure_caption_position_follows_rules(self):
        rules = _rules("jlu_cs")
        caption = _paragraph(1, "figure_caption", "图1-1 示意")
        image = {"type": "image", "index": 0}
        below = {"blocks": [image, caption]}
        above = {"blocks": [caption, image]}

        # 缺省 below：图题在下方不报，在上方报。
        self.assertNotIn("figure.caption_position", _rule_ids(below, rules))
        self.assertIn("figure.caption_position", _rule_ids(above, rules))

        rules["figures"]["caption"]["position"] = "above"
        self.assertNotIn("figure.caption_position", _rule_ids(above, rules))
        self.assertIn("figure.caption_position", _rule_ids(below, rules))

    def test_formula_number_position_follows_rules(self):
        rules = _rules("jlu_cs")
        number = "（1-1）"
        at_end = {"blocks": [_paragraph(
            0, "formula", f"E=mc^2{number}", formula_number=number, alignment="center")]}
        at_start = {"blocks": [_paragraph(
            0, "formula", f"{number}E=mc^2", formula_number=number, alignment="center")]}

        self.assertNotIn("formula.number_position", _rule_ids(at_end, rules))
        self.assertIn("formula.number_position", _rule_ids(at_start, rules))

        rules["formula"]["number_position"] = "start"
        self.assertNotIn("formula.number_position", _rule_ids(at_start, rules))
        self.assertIn("formula.number_position", _rule_ids(at_end, rules))


class ContinuedNoteContractTest(unittest.TestCase):
    """续表标注：分类层只认“续表”开头，规则层不能再判一次前缀。"""

    def test_classifier_requires_prefix(self):
        for text in ("续表", "续表2-1", "续表（2-1）方法", "续表 2－1"):
            with self.subTest(text=text):
                self.assertEqual("table_continued_note", classify_kind(text))
        for text in ("表格续页说明", "接上表", "续表说明见下"):
            with self.subTest(text=text):
                self.assertNotEqual("table_continued_note", classify_kind(text))

    def test_prefix_rule_is_retired(self):
        """前缀检查已移除：即便人为构造缺前缀的续表块，也不再报这条规则。

        缺前缀的续表在解析阶段根本不会被标成 table_continued_note，所以这条检查
        在生产中命中数恒为 0；若将来又被加回来，这个用例会失败。
        """
        rules = _rules("jlu_cs")
        block = _paragraph(1, "table_continued_note", "表格续页说明")
        self.assertNotIn("table.continued_note_prefix", _rule_ids({"blocks": [block]}, rules))

    def test_alignment_still_reported(self):
        """续表标注位置判据 = 与表格左边线对齐（模板原句）。

        居中/靠右排版落不到表格左边线上 → 报；左起且与表格左缩进一致的 → 不报。
        """
        rules = _rules("jlu_cs")
        # 版心宽 15 cm；表格左起、无缩进 → 左边线 = 版心左边界（0 cm）
        sections = [{"width_cm": 21.0, "left_cm": 3.0, "right_cm": 3.0}]
        table = {"type": "table", "index": 2, "n_rows": 1, "n_cols": 1,
                 "cells": [{"row": 0, "col": 0, "text": "a"}],
                 "width_pt": 200.0, "indent_pt": 0.0}
        centered = _paragraph(1, "table_continued_note", "续表2-1 方法",
                              alignment="center")
        aligned = _paragraph(1, "table_continued_note", "续表2-1 方法",
                             alignment="left", left_indent_pt=0.0,
                             first_line_indent_pt=0.0)
        indented = _paragraph(1, "table_continued_note", "续表2-1 方法",
                              alignment="left", left_indent_pt=24.0,
                              first_line_indent_pt=0.0)
        # 半个汉字（6 pt）以内的零头算对齐：网格宽度取整、缩进用字符单位都会有
        slight = _paragraph(1, "table_continued_note", "续表2-1 方法",
                            alignment="left", left_indent_pt=4.0,
                            first_line_indent_pt=0.0)
        self.assertIn("table.continued_note_alignment",
                      _rule_ids({"blocks": [centered, table], "sections": sections},
                                rules))
        self.assertNotIn("table.continued_note_alignment",
                         _rule_ids({"blocks": [aligned, table], "sections": sections},
                                   rules))
        self.assertNotIn("table.continued_note_alignment",
                         _rule_ids({"blocks": [slight, table], "sections": sections},
                                   rules))
        self.assertIn("table.continued_note_alignment",
                      _rule_ids({"blocks": [indented, table], "sections": sections},
                                rules))

    def test_centered_table_left_edge_is_measured(self):
        """表格居中时左边线要按表格宽度右移：标注留在左边界就没对齐。"""
        rules = _rules("jlu_cs")
        sections = [{"width_cm": 21.0, "left_cm": 3.0, "right_cm": 3.0}]  # 版心 15 cm
        centered_table = {"type": "table", "index": 2, "n_rows": 1, "n_cols": 1,
                          "cells": [{"row": 0, "col": 0, "text": "a"}],
                          "align": "center", "width_pt": 200.0, "indent_pt": 0.0}
        at_left = _paragraph(1, "table_continued_note", "续表2-1 方法",
                             alignment="left", left_indent_pt=0.0,
                             first_line_indent_pt=0.0)
        self.assertIn("table.continued_note_alignment",
                      _rule_ids({"blocks": [at_left, centered_table],
                                 "sections": sections}, rules))

    def test_no_table_no_position_check(self):
        """找不到它标的表，就没有可比的位置，宁可漏检。"""
        rules = _rules("jlu_cs")
        note = _paragraph(1, "table_continued_note", "续表2-1 方法", alignment="center")
        self.assertNotIn("table.continued_note_alignment",
                         _rule_ids({"blocks": [note]}, rules))


class ReferenceEntryNumberMessageTest(unittest.TestCase):
    """参考文献序号的「应为」提示取自 rules，不再写死在检查代码里。

    软院模板的参考文献部分只有“正文用宋体五号字”一句，没有“序号加方括号”这类
    说明，所以软院 rules 里根本没有这一项 —— 断言两院都有就变成替学院加要求。
    """

    def _colleges_with_the_rule(self):
        return [college for college in _COLLEGES
                if "number_prefix_pattern"
                in _rules(college)["references"].get("entry", {})]

    def test_example_and_pattern_agree_where_the_rule_exists(self):
        colleges = self._colleges_with_the_rule()
        self.assertTrue(colleges, "两个学院都没有参考文献序号要求，本测试也失去意义")
        for college in colleges:
            entry = _rules(college)["references"]["entry"]
            with self.subTest(college=college):
                self.assertRegex(entry["number_prefix_expected"],
                                 entry["number_prefix_pattern"])

    def test_soft_college_has_no_such_requirement(self):
        """软院模板没有这条说明，rules 里就不该有这一项（有就是替学院加要求）。"""
        entry = _rules("jlu_se")["references"].get("entry", {})
        self.assertNotIn("number_prefix_pattern", entry)
        self.assertNotIn("number_prefix_expected", entry)

    def test_issue_expected_comes_from_rules(self):
        for college in self._colleges_with_the_rule():
            rules = _rules(college)
            expected = rules["references"]["entry"]["number_prefix_expected"]
            bad = _paragraph(0, "reference_entry", "张三. 深度学习综述. 计算机学报, 2020.")
            good = _paragraph(0, "reference_entry", f"{expected} 张三. 深度学习综述.")
            with self.subTest(college=college):
                reported = [i for i in RuleEngine(rules).check(
                    {"blocks": [bad]})["issues"]
                    if i["rule_id"] == "reference.entry_number"]
                self.assertEqual(1, len(reported), "缺序号的条目未报 reference.entry_number")
                self.assertEqual(expected, reported[0]["expected"])
                self.assertNotIn("reference.entry_number",
                                 _rule_ids({"blocks": [good]}, rules))

    def test_auto_numbered_entry_is_read_from_numbering_xml(self):
        """序号由 Word 自动编号生成时，按 numbering.xml 还原出 `[1]` 再判，不报“缺序号”。

        渲染得出编号的条目要当“有序号”；渲染不出（编号定义不支持）时跳过该检查，
        而不是把“编号不在文本里”当成没写序号。
        """
        for college in self._colleges_with_the_rule():
            rules = _rules(college)
            block = _paragraph(0, "reference_entry", "张三. 深度学习综述. 计算机学报, 2020.")
            block["auto_number"] = True
            block["auto_number_text"] = "［1］"
            plain = _rule_ids({"blocks": [block]}, rules)
            with self.subTest(college=college):
                self.assertNotIn("reference.entry_number", plain)
            block.pop("auto_number_text")
            unrendered = _rule_ids({"blocks": [block]}, rules)
            self.assertNotIn("reference.entry_number", unrendered,
                             "编号还原不出来时不应按“缺序号”报")


class ReferenceEntryPeriodTest(unittest.TestCase):
    """§6.1：模板写“参考文献条目结尾：以“．”结束”——全角才算，半角“.”不算。"""

    def _ids(self, text, college="jlu_cs"):
        block = _paragraph(0, "reference_entry", text, alignment="left")
        return _rule_ids({"sections": [], "blocks": [block]}, _rules(college))

    def test_fullwidth_period_is_accepted(self):
        self.assertNotIn(
            "reference.entry_period",
            self._ids("［1］包驿．中国数字病理发展展望[J]．实用医院临床杂志，2017．"))

    def test_halfwidth_period_is_reported(self):
        self.assertIn("reference.entry_period",
                      self._ids("［1］Smith J. Neural networks[J]. Nature, 2019."))

    def test_software_college_has_no_period_sentence(self):
        """软院模板没有“以句点结束”这句，对软院不生效。"""
        self.assertNotIn(
            "reference.entry_period",
            self._ids("［1］Smith J. Neural networks[J]. Nature, 2019.", "jlu_se"))


class ReferenceEntryNumberTest(unittest.TestCase):
    """§6.1：「数字加方括号」——全角/半角方括号都认；只查有没有方括号。"""

    def _ids(self, text):
        block = _paragraph(0, "reference_entry", text, alignment="left")
        return _rule_ids({"sections": [], "blocks": [block]}, _rules("jlu_cs"))

    def test_fullwidth_brackets_are_accepted(self):
        self.assertNotIn("reference.entry_number",
                         self._ids("［1］包驿, 步宏. 中国数字病理发展展望[J]. 2017."))

    def test_halfwidth_brackets_are_accepted(self):
        self.assertNotIn("reference.entry_number",
                         self._ids("[1] 包驿, 步宏. 中国数字病理发展展望[J]. 2017."))

    def test_missing_brackets_are_still_reported(self):
        self.assertIn("reference.entry_number",
                      self._ids("1 包驿, 步宏. 中国数字病理发展展望[J]. 2017."))

    def test_missing_brackets_expected_uses_actual_number(self):
        issues = RuleEngine(_rules("jlu_cs")).check(
            {"blocks": [_paragraph(0, "reference_entry",
                                    "16 包驿, 步宏. 中国数字病理发展展望[J]. 2017.")]})["issues"]
        reported = [i for i in issues if i["rule_id"] == "reference.entry_number"]
        self.assertEqual(1, len(reported))
        self.assertEqual("［16］", reported[0]["expected"])


class EnglishKeywordCountWithoutSeparatorTest(unittest.TestCase):
    """§2.5：只用空格不用逗号时，报“没逗号”（可看见的事实），但数量不猜。"""

    def _ids(self, text):
        block = _paragraph(0, "en_keyword_body", text, alignment="left")
        return _rule_ids({"sections": [], "blocks": [block]}, _rules("jlu_cs"))

    def test_space_only_keywords_are_reported_as_missing_commas(self):
        ids = self._ids("machine learning deep learning")
        self.assertIn("en_keyword.separator", ids)

    def test_space_only_keywords_do_not_get_a_fake_count(self):
        self.assertNotIn("en_keyword.count", self._ids("machine learning deep learning"))

    def test_single_word_still_reports_the_count(self):
        """一个词时数量就是 1，数得准，照报“数量不足”。"""
        ids = self._ids("Transformer")
        self.assertIn("en_keyword.count", ids)
        self.assertNotIn("en_keyword.separator", ids)

    def test_comma_separated_keywords_are_still_counted(self):
        self.assertIn("en_keyword.count", self._ids("one, two"))


class BodyPunctuationCoverageTest(unittest.TestCase):
    """§4.2：正文里的半角句号／省略号／双引号／圆括号也按标点报，小数不算句号。"""

    def _ids(self, text):
        block = _paragraph(0, "body", text, alignment="justify",
                           first_line_indent_pt=24)
        return _rule_ids({"sections": [], "blocks": [block]}, _rules("jlu_cs"))

    def test_halfwidth_parenthesis_next_to_chinese_is_reported(self):
        self.assertIn("body.fullwidth_punct", self._ids("模型(残差网络)由何恺明提出。"))

    def test_halfwidth_quote_in_chinese_is_reported(self):
        self.assertIn("body.fullwidth_punct",
                      self._ids("他称之为 \u0022可插拔性\u0022 的设计。"))

    def test_decimal_point_is_not_a_period_error(self):
        self.assertNotIn("body.fullwidth_punct", self._ids("准确率达到 95.5 个百分点。"))


class TerminologyAdvisoryTest(unittest.TestCase):
    """术语由在线 AI 判定：标“AI 判断，仅供参考”，且不计入问题总数。"""

    def test_terminology_is_separated_from_counted_issues(self):
        from backend.detection.checker_service import _split_advisory

        issues = [
            {"rule_id": "terminology", "source": "rag",
             "message": "名词术语或外文缩写使用不符合要求"},
            {"rule_id": "body.font_size", "source": "rule_engine",
             "message": "正文字号不符合要求"},
        ]
        counted, advisory = _split_advisory(issues)
        self.assertEqual(1, len(counted))
        self.assertEqual(1, len(advisory))
        self.assertTrue(advisory[0].get("advisory"))
        self.assertTrue(advisory[0]["message"].startswith("AI 判断，仅供参考"))
        self.assertFalse(counted[0].get("advisory"))

    def test_compaction_keeps_advisory_out_of_the_total(self):
        """聚合后仍要 total_issues == 计入清单条数，参考条目单独一列。"""
        from backend.detection.issue_compaction import compact_check_result

        result = {
            "issues": [{"rule_id": "body.font_size", "category": "正文格式",
                        "severity": "error",
                        "location": {"type": "paragraph", "index": 0},
                        "actual": "10.5 pt", "expected": "12 pt",
                        "message": "正文字号不符合要求"}],
            "advisory_issues": [{"rule_id": "terminology", "category": "名词术语",
                                 "severity": "warning",
                                 "location": {"type": "document", "index": 0},
                                 "actual": "CNN", "expected": "首次出现注明含义",
                                 "message": "AI 判断，仅供参考：名词术语不符合要求",
                                 "advisory": True}],
        }
        compacted = compact_check_result(result, max_returned=100,
                                         max_location_samples=5)
        self.assertEqual(1, compacted["total_issues"])
        self.assertEqual(1, len(compacted["issues"]))
        self.assertEqual(1, len(compacted["advisory_issues"]))
        self.assertEqual(1, compacted["advisory_issues"][0]["occurrence_count"])


class ChartPositionLayeringTest(unittest.TestCase):
    """图/表位置只由规则引擎判：语义层那两条已撤销。

    理由：语义层的输入是段落文本片段，里面没有图块/表格块，看不到图在哪、表头是否
    重复；判不了就只能猜。撤销后“相隔过远”这一项进未检清单。
    """

    def test_semantic_rules_no_longer_carry_position(self):
        ids = {rule["rule_id"] for rule in SEMANTIC_RULES}
        self.assertNotIn("figure.position", ids)
        self.assertNotIn("table.position", ids)
        # 术语已改为确定性 gate + 残差 LLM，不再属于整组定位规则。
        self.assertEqual({"formula_axis"}, set(LOCATED_SEMANTIC_RULES))

    def test_distance_gap_is_declared_unchecked(self):
        from backend.detection.checker_service import check_document

        blocks = [{"type": "table", "index": 0, "cells": []}]
        result = check_document({"sections": [], "blocks": blocks}, "jlu_cs",
                                rag_service=None, include_semantic=False,
                                rules_dir=RULES_DIR)
        reasons = {item["rule_id"]: item["reason"] for item in result["unchecked"]}
        self.assertIn("table.placement", reasons)
        self.assertIn("相隔过远", reasons["table.placement"])


class TextLayerWiringTest(unittest.TestCase):
    """文字类可修复规则与修复器注册表必须一一对应。"""

    def test_registry_matches_text_layer_rule_ids(self):
        self.assertEqual(set(_DETERMINISTIC_FIXERS), set(_TEXT_LAYER_RULE_IDS))

    def test_every_fixer_is_advertised_fixable(self):
        for rule_id in sorted(_DETERMINISTIC_FIXERS):
            issue = {"rule_id": rule_id, "source": "rule_engine",
                     "location": {"type": "paragraph", "index": 0}}
            with self.subTest(rule_id=rule_id):
                fixable, reason = classify_fixability(issue)
                self.assertTrue(fixable, reason)

    def _assert_fixer_closes_rule(self, kind, text, rule_id, **extra):
        """修复前该规则报错，用注册表里的修复器改完文本后不再报错。"""
        rules = _rules("jlu_cs")
        block = _paragraph(0, kind, text, **extra)
        self.assertIn(rule_id, _rule_ids({"blocks": [block]}, rules))

        fixed = _DETERMINISTIC_FIXERS[rule_id](text)
        self.assertIsNotNone(fixed, f"{rule_id} 未产出修改")
        self.assertNotEqual(text, fixed)
        block["text"] = fixed
        block["runs"] = [dict(run, text=fixed) for run in block["runs"]]
        self.assertNotIn(rule_id, _rule_ids({"blocks": [block]}, rules))

    def test_fixers_close_their_own_rule(self):
        cases = [
            ("heading_1", "1 绪论", "heading.heading_1.number"),
            ("body", "中文,English 123。", "body.fullwidth_punct"),
            ("keyword_body", "关键词一，关键词二。", "keyword.terminal_punct"),
            ("note", "数据来源：某某年鉴", "note.prefix"),
            ("table_caption", "表1-1数据", "caption.number_space"),
            ("reference_entry", "张三. 论文[J]", "reference.entry_period"),
            ("reference_entry", "1 张三. 论文[J].", "reference.entry_number"),
        ]
        for kind, text, rule_id in cases:
            with self.subTest(rule_id=rule_id):
                self._assert_fixer_closes_rule(kind, text, rule_id)


class DeterministicFixerTest(unittest.TestCase):
    """新增修复器的边界：不该改的一律返回 None，交给写回层记为跳过。"""

    def test_reference_entry_number(self):
        fix = _fix_reference_entry_number
        self.assertEqual("［1］ 张三", fix("[1] 张三"))
        self.assertEqual("［2］ Smith J. Neural networks", fix("[2] Smith J. Neural networks"))
        self.assertEqual("［3］ 李四", fix("3、李四"))
        self.assertEqual("［4］ 王五", fix("4 王五"))
        self.assertIsNone(fix("［1］ 张三"))
        self.assertIsNone(fix("张三. 论文[J]."))
        self.assertIsNone(fix("2020. 张三. 论文[J]."))
        self.assertIsNone(fix("[1]"))

    def test_body_fullwidth_punct(self):
        fix = _fix_body_fullwidth_punct
        self.assertEqual("中文，English 123。", fix("中文,English 123。"))
        self.assertEqual("第一，第二：第三", fix("第一,第二:第三"))
        self.assertEqual("，中文，", fix(",中文,"))
        self.assertIsNone(fix("English 123, 456."))
        self.assertIsNone(fix("中文。"))

    def test_keyword_terminal_punct(self):
        fix = _fix_keyword_terminal_punct
        self.assertEqual("关键词一，关键词二", fix("关键词一，关键词二。"))
        self.assertEqual("关键词一，关键词二", fix("关键词一，关键词二；"))
        self.assertIsNone(fix("关键词一，关键词二"))
        self.assertIsNone(fix("。"))
        self.assertIsNone(fix(""))
        self.assertIsNone(fix(None))

    def test_heading_1_number(self):
        fix = _fix_heading_1_number
        self.assertEqual("第1章 绪论", fix("1 绪论"))
        self.assertEqual("第1章 绪论", fix("1. 绪论"))
        self.assertEqual("第2章 相关工作", fix("2、相关工作"))
        self.assertEqual("第10章 结论", fix("10 结论"))
        self.assertIsNone(fix("第1章 绪论"))
        self.assertIsNone(fix("1.2 研究背景"))
        self.assertIsNone(fix("1.1.1 现状"))
        self.assertIsNone(fix("研究背景"))
        self.assertIsNone(fix("2020 年度报告"))

    def test_legacy_fixers_still_behave(self):
        self.assertEqual("张三. 论文[J]．", _fix_reference_period("张三. 论文[J]"))
        self.assertEqual("张三. 论文[J]．", _fix_reference_period("张三. 论文[J]."))
        self.assertIsNone(_fix_reference_period("张三. 论文[J]．"))
        # 一个汉字宽才算够：缺空格、一个半角空格都要改成全角空格。
        self.assertEqual("图3-1\u3000示意", _fix_caption_number_space("图3-1示意"))
        self.assertEqual("图3-1\u3000示意", _fix_caption_number_space("图3-1 示意"))
        self.assertIsNone(_fix_caption_number_space("图3-1\u3000示意"))
        self.assertIsNone(_fix_caption_number_space("图3-1  示意"))
        self.assertEqual("【注】数据来源", _fix_note_prefix("数据来源"))
        self.assertIsNone(_fix_note_prefix("【注】数据来源"))


class SemanticWithoutRagTest(unittest.TestCase):
    """AI 服务不可用时语义层的降级行为。

    6 条零 LLM 的确定性条款必须照常执行（否则 RAG-less 部署会静默漏检整条规范）；
    依赖 AI 的定位类条款整组跳过，且必须由 breakdown.semantic.llm_enabled=False
    显式标明"这层没跑"，不能假装已检查。
    """

    def _semantic_ids(self, result):
        return {issue["rule_id"] for issue in result["issues"] if issue["source"] == "rag"}

    def test_deterministic_semantic_rules_run_without_rag(self):
        result = check_document(
            _semantic_document(), "jlu_cs", rag_service=None,
            include_semantic=True, rules_dir=RULES_DIR,
        )
        self.assertEqual(self._semantic_ids(result), set(DETERMINISTIC_SEMANTIC_RULES))
        self.assertIs(result["breakdown"]["semantic"]["enabled"], True)
        self.assertIs(result["breakdown"]["semantic"]["llm_enabled"], False)

    def test_located_channel_not_entered_without_rag(self):
        with mock.patch.object(
            SemanticChecker, "_run_located_merged",
            side_effect=AssertionError("无 AI 服务时不应进入定位规则通道"),
        ):
            result = check_document(
                _semantic_document(), "jlu_cs", rag_service=None,
                include_semantic=True, rules_dir=RULES_DIR,
            )
        self.assertFalse(self._semantic_ids(result) & LOCATED_SEMANTIC_RULES)

    def test_located_channel_entered_when_rag_present(self):
        with mock.patch.object(
            SemanticChecker, "_run_located_merged", return_value=[],
        ) as patched:
            result = check_document(
                _semantic_document(), "jlu_cs", rag_service=object(),
                include_semantic=True, rules_dir=RULES_DIR,
            )
        patched.assert_called_once()
        self.assertIs(result["breakdown"]["semantic"]["llm_enabled"], True)

    def test_include_semantic_false_skips_whole_layer(self):
        result = check_document(
            _semantic_document(), "jlu_cs", rag_service=None,
            include_semantic=False, rules_dir=RULES_DIR,
        )
        self.assertEqual(self._semantic_ids(result), set())
        self.assertIs(result["breakdown"]["semantic"]["enabled"], False)

    def test_check_api_works_without_rag(self):
        """无 RAG 时 /api/check 不能 503，且语义类问题要标成不可自动修复。"""
        previous_rag = api.rag_service
        previous_llm = api.llm_service
        api.rag_service = None
        api.llm_service = None
        client = TestClient(api.app)
        try:
            response = client.post("/api/check", json={
                "college_id": "jlu_cs",
                "document": _semantic_document(),
            })
        finally:
            client.close()
            api.rag_service = previous_rag
            api.llm_service = previous_llm

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            {issue["rule_id"] for issue in body["issues"] if issue["source"] == "rag"},
            set(DETERMINISTIC_SEMANTIC_RULES),
        )
        self.assertIs(body["breakdown"]["semantic"]["llm_enabled"], False)
        for issue in body["issues"]:
            if issue["source"] == "rag":
                self.assertFalse(issue["fixable"], issue["rule_id"])
                self.assertTrue(issue.get("fix_reason"), issue["rule_id"])


class SemanticFixabilityTest(unittest.TestCase):
    """llm_available 只影响可文本改写的语义类问题，不误伤其他规则。"""

    def _issues(self):
        return [
            {"rule_id": "list.numbering", "source": "rag",
             "location": {"type": "document"}},
            {"rule_id": "punctuation.fullwidth", "source": "rag",
             "location": {"type": "document"}},
            {"rule_id": "reference.entry_period", "source": "rule_engine",
             "location": {"type": "paragraph", "index": 0}},
        ]

    def test_reference_entry_format_is_manual_only(self):
        """参考文献缺的是卷(期)号、起止页码这类实际信息：改写补不出来，只会编造出处，
        所以这条不进自动改写白名单，且不随 AI 是否可用变化。"""
        self.assertNotIn("reference.format", REWRITABLE_SEMANTIC_RULES)
        issue = {"rule_id": "reference.format", "source": "rag",
                 "location": {"type": "document"}}
        reason = MANUAL_ONLY_SEMANTIC_RULES["reference.format"]
        for llm_available in (True, False):
            with self.subTest(llm_available=llm_available):
                self.assertEqual((False, reason),
                                 classify_fixability(issue, llm_available=llm_available))

    def test_rewrite_service_skips_reference_format_with_its_own_reason(self):
        """改写通道也要用同一条原因，不能落到“结构性语义规则”那句笼统话上。"""
        issue = {"rule_id": "reference.format", "category": "参考文献",
                 "source": "rag", "expected": "（样例）",
                 "blocks": [{"index": 0, "text": "［1］张三．综述[J]．学报，2020．"}]}

        result = generate_text_fixes({0: "［1］张三．综述[J]．学报，2020．"}, [issue],
                                     rag_service=object(), college_id="jlu_cs")

        self.assertEqual([], result["changes"])
        self.assertEqual(0, result["llm_calls"])
        self.assertEqual(MANUAL_ONLY_SEMANTIC_RULES["reference.format"],
                         result["skipped"][0]["reason"])

    def test_llm_availability_only_affects_rewritable_semantic_issues(self):
        for issue in self._issues():
            with self.subTest(rule_id=issue["rule_id"]):
                online = classify_fixability(issue, llm_available=True)
                offline = classify_fixability(issue, llm_available=False)
                if issue["rule_id"] in REWRITABLE_SEMANTIC_RULES:
                    self.assertTrue(online[0])
                    self.assertFalse(offline[0])
                    self.assertTrue(offline[1])
                else:
                    self.assertEqual(online, offline)

    def test_annotate_clears_fix_reason_when_fixable(self):
        """先按"无 AI"标注，再按"有 AI"重标，不能留下上一轮的 fix_reason。"""
        issues = [{"rule_id": "list.numbering", "source": "rag",
                   "location": {"type": "document"}}]
        annotate_issues(issues, llm_available=False)
        self.assertFalse(issues[0]["fixable"])
        self.assertTrue(issues[0]["fix_reason"])

        annotate_issues(issues, llm_available=True)
        self.assertTrue(issues[0]["fixable"])
        self.assertNotIn("fix_reason", issues[0])


class KeywordCountFixabilityTest(unittest.TestCase):
    """关键词数量类问题能检出、但不可自动修复，原因须说明是人工决策。"""

    def test_count_issues_are_detected_but_manual_only(self):
        cases = [
            ("keyword_body", "关键词一，关键词二", "keyword.count"),
            ("en_keyword_body", "keyword one, keyword two", "en_keyword.count"),
        ]
        for kind, text, rule_id in cases:
            # 与 backend/api.py 同一条链路：check_document 注入 source，annotate_issues 标注可修复性。
            result = check_document({"sections": [], "blocks": [_paragraph(0, kind, text)]},
                                    "jlu_cs", include_semantic=False, rules_dir=RULES_DIR)
            annotate_issues(result["issues"], llm_available=False)
            found = [i for i in result["issues"] if i["rule_id"] == rule_id]
            with self.subTest(rule_id=rule_id):
                self.assertEqual(1, len(found), "该数量规则未被检出")
                self.assertFalse(found[0]["fixable"])
                reason = found[0]["fix_reason"]
                self.assertIn("人工", reason)
                self.assertNotIn("暂无自动修复映射", reason)


class KeywordLabelRuleTest(unittest.TestCase):
    """§2.3：标签用词，以本院模板为准。

    两院官方模板用词不同——计算机模板写“关键字”，软件模板写“关键词”。
    既然一切以模板为准，各院只认自己模板的那个词：计算机写成“关键词”同样报
    （与本院模板不一致），软件写成“关键字”也报。用词是硬要求，报 error。
    """

    def _issues(self, text, college="jlu_cs"):
        runs = [{"text": text, "font_name": "黑体", "east_asia_font": "黑体",
                 "font_size_pt": 14, "bold": False}]
        block = _paragraph(0, "keyword_title", text, alignment="left", runs=runs)
        document = {"sections": [], "blocks": [block]}
        return [i for i in RuleEngine(_rules(college)).check(document)["issues"]
                if i["rule_id"] == "keyword.title_label"]

    def _ids(self, text, college="jlu_cs"):
        return [i["rule_id"] for i in self._issues(text, college)]

    def test_other_label_is_reported_as_wording_issue(self):
        """“关健词”错字：报用词问题，不牵连字体字号。"""
        ids = self._ids("关健词：深度学习，目标检测，图像分割")
        self.assertIn("keyword.title_label", ids)
        self.assertNotIn("keyword.title.font", ids)
        self.assertNotIn("keyword.title.font_size", ids)

    def test_cs_template_own_label_is_accepted(self):
        self.assertNotIn("keyword.title_label", self._ids("关键字：深度学习，目标检测，图像分割"))

    def test_cs_writing_keyword_is_reported(self):
        """计算机模板写“关键字”，学生写“关键词”＝与本院模板不一致，要报。"""
        issues = self._issues("关键词：深度学习，目标检测，图像分割")
        self.assertEqual(1, len(issues))
        self.assertEqual("error", issues[0]["severity"])
        self.assertEqual("关键词标签应写作“关键字”", issues[0]["message"])
        self.assertIn("关键字", issues[0]["suggestion"])

    def test_software_college_only_accepts_keyword(self):
        self.assertIn("keyword.title_label",
                      self._ids("关键字：深度学习，目标检测，图像分割", "jlu_se"))

    def test_software_college_own_label_is_accepted(self):
        self.assertNotIn("keyword.title_label",
                         self._ids("关键词：深度学习，目标检测，图像分割", "jlu_se"))

    def test_label_from_json_drives_the_check(self):
        """判定取 rules 的 keywords.label：改了配置结论就要跟着变。"""
        rules = _rules("jlu_cs")
        rules["keywords"]["label"] = "关键词"
        runs = [{"text": "关键字：深度学习", "font_name": "黑体", "east_asia_font": "黑体",
                 "font_size_pt": 14, "bold": False}]
        block = _paragraph(0, "keyword_title", "关键字：深度学习", alignment="left", runs=runs)
        ids = _rule_ids({"sections": [], "blocks": [block]}, rules)
        self.assertIn("keyword.title_label", ids)


class EnglishKeywordLabelRuleTest(unittest.TestCase):
    """§2.7：英文关键词标签行（标签词 + 冒号），模板原文是“Keywords”。

    冒号形态也照模板：计算机模板写“Keywords：”（全角），软件模板写“Keywords:”
    （半角）——写错冒号形态同样是与模板不一致，报 error。
    """

    LABEL = {"jlu_cs": "Keywords\uff1a", "jlu_se": "Keywords:"}

    def _label_issues(self, text, college="jlu_cs", rules=None):
        runs = [{"text": text, "font_name": "Times New Roman",
                 "east_asia_font": "Times New Roman", "font_size_pt": 14, "bold": True}]
        block = _paragraph(0, "en_keyword_title", text, alignment="left", runs=runs)
        document = {"sections": [], "blocks": [block]}
        engine = RuleEngine(rules or _rules(college))
        return [i for i in engine.check(document)["issues"]
                if i["rule_id"] == "en_keyword.title_label"]

    def test_template_own_label_is_accepted(self):
        """两院模板都写“Keywords”，冒号分别是全角、半角。"""
        for college, label in self.LABEL.items():
            self.assertEqual([], self._label_issues(f"{label} deep learning, detection", college))

    def test_colon_form_must_match_template(self):
        """计算机模板是全角冒号：学生写半角冒号同样报。"""
        issues = self._label_issues("Keywords: deep learning, detection", "jlu_cs")
        self.assertEqual(1, len(issues))
        self.assertEqual("error", issues[0]["severity"])
        self.assertIn("冒号应为全角冒号", issues[0]["message"])
        self.assertIn("半角冒号", issues[0]["message"])
        self.assertEqual("Keywords:", issues[0]["actual"])
        self.assertEqual("Keywords\uff1a", issues[0]["expected"])

    def test_software_college_requires_halfwidth_colon(self):
        """软件模板是半角冒号：学生写全角冒号同样报。"""
        issues = self._label_issues("Keywords\uff1a deep learning, detection", "jlu_se")
        self.assertEqual(1, len(issues))
        self.assertIn("冒号应为半角冒号", issues[0]["message"])

    def test_space_before_colon_is_reported(self):
        issues = self._label_issues("Keywords : deep learning, detection", "jlu_se")
        self.assertEqual(1, len(issues))
        self.assertIn("多了空白", issues[0]["message"])

    def test_singular_form_is_reported(self):
        self.assertEqual(1, len(self._label_issues("Keyword: deep learning, detection")))

    def test_split_words_are_reported(self):
        self.assertEqual(1, len(self._label_issues("Key words: deep learning, detection")))

    def test_issue_is_error_with_template_wording(self):
        issues = self._label_issues("Keyword: deep learning, detection")
        self.assertEqual("error", issues[0]["severity"])
        self.assertEqual("英文关键词标签应写作“Keywords”", issues[0]["message"])
        self.assertIn("Keywords", issues[0]["suggestion"])
        self.assertIn("Keyword", issues[0]["actual"])

    def test_label_from_json_drives_the_check(self):
        """判定取 rules 的 english_abstract.keyword_label/keyword_colon：改配置结论要跟着变。"""
        rules = _rules("jlu_cs")
        rules["english_abstract"]["keyword_label"] = "Keyword"
        self.assertEqual([], self._label_issues("Keyword\uff1a deep learning", rules=rules))
        self.assertEqual(1, len(self._label_issues("Keywords\uff1a deep learning", rules=rules)))


class ChineseKeywordLabelColonRuleTest(unittest.TestCase):
    """§2.3：中文关键词标签的冒号形态也照模板（两院模板都是全角冒号）。"""

    def _issues(self, text, college="jlu_cs", rules=None):
        runs = [{"text": text, "font_name": "黑体", "east_asia_font": "黑体",
                 "font_size_pt": 14, "bold": False}]
        block = _paragraph(0, "keyword_title", text, alignment="left", runs=runs)
        document = {"sections": [], "blocks": [block]}
        return [i for i in RuleEngine(rules or _rules(college)).check(document)["issues"]
                if i["rule_id"] == "keyword.title_label"]

    def test_template_colon_is_accepted(self):
        for text, college in (("\u5173\u952e\u5b57\uff1a\u6df1\u5ea6\u5b66\u4e60", "jlu_cs"),
                              ("\u5173\u952e\u8bcd\uff1a\u6df1\u5ea6\u5b66\u4e60", "jlu_se")):
            self.assertEqual([], self._issues(text, college))

    def test_halfwidth_colon_is_reported(self):
        issues = self._issues("\u5173\u952e\u5b57: \u6df1\u5ea6\u5b66\u4e60")
        self.assertEqual(1, len(issues))
        self.assertEqual("error", issues[0]["severity"])
        self.assertIn("全角冒号", issues[0]["message"])

    def test_space_before_colon_is_reported(self):
        issues = self._issues("\u5173\u952e\u5b57 \uff1a\u6df1\u5ea6\u5b66\u4e60")
        self.assertEqual(1, len(issues))
        self.assertIn("多了空白", issues[0]["message"])


class EnglishKeywordSeparatorTest(unittest.TestCase):
    """§2.4/§2.5：两院原句不同——计算机只写“逗号间隔”，软件写“英文逗号间隔”。"""

    TEXT_FULLWIDTH = "Image retrieval，ResNet，Swin Transformer"
    TEXT_SEMICOLON = "Image retrieval; ResNet; Swin Transformer"

    def _ids(self, text, college):
        block = _paragraph(0, "en_keyword_body", text, alignment="left")
        return _rule_ids({"sections": [], "blocks": [block]}, _rules(college))

    def test_fullwidth_comma_is_still_a_comma_in_cs(self):
        """计算机模板（§2.4）只写“逗号间隔”，没有“英文”二字：全角逗号不报。"""
        self.assertNotIn("en_keyword.separator",
                         self._ids(self.TEXT_FULLWIDTH, "jlu_cs"))

    def test_fullwidth_comma_is_reported_in_se(self):
        """软件模板（§2.5）写的是“英文逗号间隔”：全角逗号要报。"""
        self.assertIn("en_keyword.separator",
                      self._ids(self.TEXT_FULLWIDTH, "jlu_se"))

    def test_semicolon_is_reported_in_both_colleges(self):
        """分号不是逗号，两院都报。"""
        for college in ("jlu_cs", "jlu_se"):
            with self.subTest(college=college):
                self.assertIn("en_keyword.separator",
                              self._ids(self.TEXT_SEMICOLON, college))


class ChineseKeywordSeparatorTest(unittest.TestCase):
    """§2.2：两院原句都是“关键词分隔：逗号间隔”——全角半角都是逗号。"""

    def _ids(self, text, college):
        block = _paragraph(0, "keyword_body", text, alignment="left")
        return _rule_ids({"sections": [], "blocks": [block]}, _rules(college))

    def test_halfwidth_comma_is_still_a_comma(self):
        for college in ("jlu_cs", "jlu_se"):
            with self.subTest(college=college):
                self.assertNotIn("keyword.separator",
                                 self._ids("反欺诈, 机器学习, 逻辑回归", college))

    def test_semicolon_and_dunhao_are_reported(self):
        for text in ("反欺诈；机器学习；逻辑回归", "反欺诈、机器学习、逻辑回归"):
            with self.subTest(text=text[:6]):
                self.assertIn("keyword.separator", self._ids(text, "jlu_cs"))


class KeywordRegionKindsTest(unittest.TestCase):
    """关键词区域的 kind 分流：标签行之后是英文题目还是关键词内容。

    计软模板把关键词排在标签行上（§2.4/§2.7），标签行之后紧接着的是居中的英文题目
    （§2.5）。若一律当成关键词内容，英文题目会被按关键词查（报“数量不足 1 个”
    与“关键词内容字号不符”），而学生一个字都没写错。
    """

    @staticmethod
    def _block(index, text, *, align="left", size=14, cjk="黑体", bold=False):
        return {
            "index": index, "type": "paragraph", "kind": classify_kind(text),
            "text": text, "alignment": align, "first_line_indent_pt": None,
            "line_spacing_multiple": 1.5,
            "runs": [{"text": text, "font_name": "Times New Roman",
                      "east_asia_font": cjk, "font_size_pt": size, "bold": bold}],
        }

    def test_english_title_after_the_keyword_label_is_not_keyword_content(self):
        blocks = [
            self._block(0, "关键字：深度学习，知识图谱，论文格式"),
            self._block(1, "Design and Implementation of a Thesis Format Checker",
                        align="center", size=15, cjk="宋体"),
            self._block(2, "Abstract", align="center", size=15, cjk="Times New Roman"),
        ]
        apply_contextual_kinds(blocks)
        self.assertEqual(["keyword_title", "en_title", "en_abstract_title"],
                         [b["kind"] for b in blocks])

    def test_keyword_content_on_the_next_line_still_becomes_keyword_body(self):
        blocks = [
            self._block(0, "关键词："),
            self._block(1, "深度学习，知识图谱", cjk="宋体", size=12),
            self._block(2, "第1章 绪论", align="center", size=16),
        ]
        apply_contextual_kinds(blocks)
        self.assertEqual(["keyword_title", "keyword_body", "heading_1"],
                         [b["kind"] for b in blocks])

    def test_english_keywords_on_the_next_line_stay_keyword_content(self):
        blocks = [
            self._block(0, "关键字："),
            self._block(1, "RAG, LLM, Transformer"),
            self._block(2, "第1章 绪论", align="center", size=16),
        ]
        apply_contextual_kinds(blocks)
        self.assertEqual("keyword_body", blocks[1]["kind"])

    def test_centered_keyword_like_line_stays_keyword_content(self):
        """学生把英文关键词居中了：带分隔符的行仍按关键词内容处理。"""
        blocks = [
            self._block(0, "关键字："),
            self._block(1, "RAG, LLM, Transformer", align="center"),
            self._block(2, "第1章 绪论", align="center", size=16),
        ]
        apply_contextual_kinds(blocks)
        self.assertEqual("keyword_body", blocks[1]["kind"])

    def _keyword_issues(self, blocks):
        document = {"sections": [], "blocks": blocks}
        return [i for i in RuleEngine(_rules("jlu_cs")).check(document)["issues"]
                if i["rule_id"].startswith("keyword.")]

    def test_english_keywords_on_the_next_line_are_not_reported_as_missing(self):
        """真实组合：标签行 + 下一行英文关键词，解析层判成内容行，不报“0 个”。"""
        blocks = [
            self._block(0, "关键字："),
            self._block(1, "RAG, LLM, Transformer"),
            self._block(2, "第1章 绪论", align="center", size=16),
        ]
        apply_contextual_kinds(blocks)
        self.assertNotIn("keyword.count",
                         [i["rule_id"] for i in self._keyword_issues(blocks)])

    def test_empty_label_before_the_english_title_reports_zero_keywords(self):
        """真实组合：标签行空着、后面跟着英文题目 → 按 0 个报数量不足。"""
        blocks = [
            self._block(0, "关键字："),
            self._block(1, "Design and Implementation of a Thesis Format Checker",
                        align="center", size=15, cjk="宋体"),
            self._block(2, "第1章 绪论", align="center", size=16),
        ]
        apply_contextual_kinds(blocks)
        counts = [i for i in self._keyword_issues(blocks)
                  if i["rule_id"] == "keyword.count"]
        self.assertEqual(1, len(counts))
        self.assertEqual("0 个", counts[0]["actual"])
        self.assertEqual(0, counts[0]["location"]["index"])


class KeywordLineSpacingTest(unittest.TestCase):
    """关键词行的行距：规范原文写了的才查。

    软院 §2.4 明写“关键词内容：宋体小四号字，1.5倍行距”，所以关键词内容行要查
    行距；计软 §2.4/§2.7 只写字体字号与位置（模板整行标注为“黑体4号字居左，
    单独一行”），没写行距——不能自造一条去报学生。
    """

    def _ids(self, kind, *, college, multiple):
        block = _paragraph(0, kind, "关键词内容行", line_spacing_multiple=multiple)
        document = {"sections": [], "blocks": [block]}
        return [i["rule_id"] for i in RuleEngine(_rules(college)).check(document)["issues"]
                if i["rule_id"].endswith(".line_spacing")]

    def test_rule_files_match_the_keyword_line_spacing_text(self):
        cs, se = _rules("jlu_cs"), _rules("jlu_se")
        self.assertNotIn("line_spacing_multiple", cs["keywords"]["body"])
        self.assertNotIn("line_spacing_multiple", cs["english_abstract"]["keyword_body"])
        self.assertEqual(1.5, se["keywords"]["body"]["line_spacing_multiple"])
        self.assertEqual(1.5, se["english_abstract"]["keyword_body"]["line_spacing_multiple"])

    def test_rule_files_match_the_keyword_alignment_text(self):
        """计软 §2.4/§2.7 明写关键词内容“居左”，规则文件必须声明它，不能漏查。"""
        cs = _rules("jlu_cs")
        self.assertEqual("left", cs["keywords"]["body"]["alignment"])
        self.assertEqual("left", cs["english_abstract"]["keyword_body"]["alignment"])

    def test_computer_college_checks_keyword_content_alignment(self):
        block = _paragraph(0, "keyword_body", "深度学习，知识图谱",
                           alignment="center")
        document = {"sections": [], "blocks": [block]}
        ids = [i["rule_id"] for i in RuleEngine(_rules("jlu_cs")).check(document)["issues"]]
        self.assertIn("keyword.body.alignment", ids)

    def test_software_college_checks_keyword_line_spacing(self):
        self.assertIn("keyword.body.line_spacing",
                      self._ids("keyword_body", college="jlu_se", multiple=1.0))
        self.assertEqual([], self._ids("keyword_body", college="jlu_se", multiple=1.5))

    def test_computer_college_does_not_require_a_keyword_line_spacing(self):
        """计软没写这条要求：即使关键词另起一行、用了单倍行距也不报行距。"""
        self.assertEqual([], self._ids("en_keyword_body", college="jlu_cs", multiple=1.0))
        self.assertEqual([], self._ids("keyword_body", college="jlu_cs", multiple=1.0))


class PledgePageIgnoredTest(unittest.TestCase):
    """§2：承诺书自身的文字不检查；承诺书页上的其他内容与其他部分照常检查。

    计软模板有承诺书页，软院模板同样有，以后新增的学院也会有。免检只覆盖承诺书本身
    的几行文字，识别写在 parser 里、与学院规则无关，因此对所有学院同样生效。
    """

    PLEDGE_TITLE = "吉林大学学士学位论文（设计）承诺书"
    PLEDGE_BODY = "本人郑重承诺：所呈交的学士学位毕业论文（设计），是本人在指导教师的指导下独立完成。"
    PLEDGE_SIGN = "              学士学位论文（设计）作者签名："
    PLEDGE_DATE = "                              2020年5月20日"
    # 承诺书页上学生另外加的一行：属于“这一页上的别的东西”，要查
    EXTRA_LINE = "学号：2019123456"
    # 承诺书页之后是封面页
    COVER_LINES = ("吉林大学", "本科毕业论文（设计）")
    MAIN_TITLE = "基于深度学习的论文格式校验系统设计与实现"

    # 承诺书自身的四行：排版刻意全部写错（楷体 10.5pt、左对齐），仍不应被检查
    PLEDGE_ROWS = (
        (PLEDGE_TITLE, dict(align="center", cjk="楷体", size=10.5)),
        (PLEDGE_BODY, dict(align="left", cjk="楷体", size=10.5)),
        (PLEDGE_SIGN, dict(align="left", cjk="楷体", size=10.5)),
        (PLEDGE_DATE, dict(align="left", cjk="楷体", size=10.5)),
    )
    PLEDGE_INDEXES = set(range(len(PLEDGE_ROWS)))

    def _rows(self, *, extra_on_pledge_page=False):
        rows = list(self.PLEDGE_ROWS)
        if extra_on_pledge_page:
            rows.append((self.EXTRA_LINE, dict(align="center", cjk="宋体", size=12)))
        rows += [(line, dict(align="center", cjk="宋体", size=22))
                 for line in self.COVER_LINES]
        rows.append((self.MAIN_TITLE, dict(align="center", cjk="黑体", size=15)))
        # 摘要锚点：真实论文都有，也是“前面这一段确实是封面”的判据之一
        rows.append(("摘要", dict(align="center", cjk="黑体", size=16)))
        return rows

    @staticmethod
    def _paragraph_block(index, text, options):
        return {
            "type": "paragraph", "index": index, "kind": classify_kind(text),
            "text": text, "alignment": options["align"],
            "first_line_indent_pt": None, "line_spacing_multiple": 1.5,
            "runs": [{"text": text, "font_name": "Times New Roman",
                      "east_asia_font": options["cjk"],
                      "font_size_pt": options["size"], "bold": False}],
        }

    def _blocks(self, rows, *, image_after=None):
        """按真实链路构造：kind 由 classify_kind 判定，再经 context 区域状态机校正。"""
        blocks = []
        for position, (text, options) in enumerate(rows):
            if image_after is not None and position == image_after:
                blocks.append(self._image_block(0))
            blocks.append(self._paragraph_block(0, text, options))
        for index, block in enumerate(blocks):
            block["index"] = index
        apply_contextual_kinds(blocks)
        return blocks

    @staticmethod
    def _image_block(index):
        """承诺书页上贴的一张扫描图。"""
        return {"type": "image", "index": index, "path": "images/img_0000.png",
                "width_inch": 6.0, "height_inch": 8.0, "align": "center"}

    def _issues(self, blocks, college="jlu_cs"):
        return RuleEngine(_rules(college)).check(
            {"sections": [], "blocks": blocks})["issues"]

    @staticmethod
    def _paragraph_issue_indexes(issues):
        """只看落在具体段落上的问题（节级问题也有 index，不能混进来）。"""
        return {i["location"].get("index") for i in issues
                if i["location"].get("type") == "paragraph"}

    def test_pledge_text_itself_is_never_checked(self):
        """承诺书标题、承诺正文、署名行、日期行排版全错也一条都不报。"""
        issues = self._issues(self._blocks(self._rows()))
        self.assertEqual(set(),
                         self._paragraph_issue_indexes(issues) & self.PLEDGE_INDEXES)

    def test_software_college_also_skips_the_pledge_text(self):
        """软院模板同样有承诺书页，识别与免检都不依赖学院规则。"""
        issues = self._issues(self._blocks(self._rows()), "jlu_se")
        self.assertEqual(set(),
                         self._paragraph_issue_indexes(issues) & self.PLEDGE_INDEXES)

    def test_extra_line_on_the_pledge_page_belongs_to_the_cover(self):
        """承诺书页上另加的字段行（如“学号：…”）属封面字段，不套正文规则。"""
        blocks = self._blocks(self._rows(extra_on_pledge_page=True))
        extra = len(self.PLEDGE_ROWS)
        self.assertEqual("cover_info", blocks[extra]["kind"])
        self.assertNotIn(extra, self._paragraph_issue_indexes(self._issues(blocks)))

    def test_image_on_the_pledge_page_is_not_treated_as_a_figure(self):
        """承诺书页/封面上的图片是版式元素（校徽、整页扫描），不按插图规则量。"""
        blocks = self._blocks(self._rows(), image_after=len(self.PLEDGE_ROWS))
        self.assertEqual("image", blocks[len(self.PLEDGE_ROWS)]["type"])
        self.assertTrue(blocks[len(self.PLEDGE_ROWS)].get("front_matter"))
        self.assertEqual([], [i["rule_id"] for i in self._issues(blocks)
                              if i["rule_id"].startswith(("figure.", "table."))])

    def test_cover_lines_after_the_pledge_page_are_not_charged_to_the_body(self):
        """承诺书页之后的封面行归 cover_info，不再被正文规则误报。"""
        blocks = self._blocks(self._rows())
        cover_kinds = [b["kind"] for b in blocks if b.get("text") in self.COVER_LINES]
        self.assertEqual(["cover_info", "cover_info"], cover_kinds)
        self.assertEqual(set(), self._paragraph_issue_indexes(self._issues(blocks))
                         & {4, 5})

    def test_main_title_still_gets_the_title_slot(self):
        """承诺书标题不能让中文题目落空。"""
        blocks = self._blocks(self._rows())
        title = next(b for b in blocks if b.get("text") == self.MAIN_TITLE)
        self.assertEqual("title", title["kind"])


class CoverInfoTest(unittest.TestCase):
    """封面字段行不套正文规则，封面之后的部分照常检查。

    规范里只规定了“中文题目”。学校名、论文类型、学院、专业、学号、姓名、日期这些行
    没有任何条目，留成 body 会被正文规则逐条量（字号/对齐/缩进/行距），结论与事实不符。
    """

    TITLE = "基于深度学习的论文格式校验系统设计与实现"
    # (内容, 对齐, 中文字体, 字号)
    COVER_ROWS = (
        ("吉林大学", "center", "宋体", 22),
        ("本科毕业论文（设计）", "center", "宋体", 22),
        (TITLE, "center", "黑体", 15),
        ("学院：计算机科学与技术学院", "center", "宋体", 12),
        ("专业：计算机科学与技术", "center", "宋体", 12),
        ("学号：2019123456", "center", "宋体", 12),
        ("姓名：张三", "center", "宋体", 12),
        ("2020年6月", "center", "宋体", 12),
    )
    TAIL_ROWS = (
        ("摘要", "center", "黑体", 16),
        ("本文研究了论文格式校验问题。", "justify", "宋体", 12),
    )

    @staticmethod
    def _blocks(rows):
        """按真实链路构造：kind 由 classify_kind 判定，再经 context 区域状态机校正。"""
        blocks = []
        for index, (text, align, cjk, size) in enumerate(rows):
            blocks.append({
                "type": "paragraph", "index": index, "kind": classify_kind(text),
                "text": text, "alignment": align, "first_line_indent_pt": None,
                "line_spacing_multiple": 1.5,
                "runs": [{"text": text, "font_name": "Times New Roman",
                          "east_asia_font": cjk, "font_size_pt": size,
                          "bold": False}],
            })
        apply_contextual_kinds(blocks)
        return blocks

    def _check(self, rows, college="jlu_cs"):
        blocks = self._blocks(rows)
        document = {"sections": [], "blocks": blocks}
        return blocks, RuleEngine(_rules(college)).check(document)["issues"]

    @staticmethod
    def _paragraph_issue_indexes(issues):
        return {i["location"].get("index") for i in issues
                if i["location"].get("type") == "paragraph"}

    def test_cover_field_rows_are_not_charged_to_the_body(self):
        """学校名/论文类型/学院/专业/学号/姓名/日期都不再套正文规则。"""
        blocks, issues = self._check(self.COVER_ROWS + self.TAIL_ROWS)
        self.assertEqual(
            ["cover_info", "cover_info", "title"] + ["cover_info"] * 5
            + ["abstract_title", "abstract_body"],
            [b["kind"] for b in blocks],
        )
        self.assertEqual(set(), self._paragraph_issue_indexes(issues))

    def test_title_and_abstract_are_still_checked(self):
        """封面字段免套正文规则，不等于放过中文题目与摘要。"""
        rows = (
            ("吉林大学", "center", "宋体", 22),
            (self.TITLE, "center", "宋体", 15),      # 中文题目字体写错
            ("摘要", "center", "楷体", 10.5),         # 摘要标题字体字号写错
        )
        blocks, issues = self._check(rows)
        self.assertEqual(["cover_info", "title", "abstract_title"],
                         [b["kind"] for b in blocks])
        ids = {i["rule_id"] for i in issues}
        self.assertIn("title.font", ids)
        self.assertIn("abstract.title.font", ids)
        self.assertIn("abstract.title.font_size", ids)

    def test_document_without_cover_boundary_is_still_checked_as_body(self):
        """没有摘要/章节标题的片段文档整篇落在 FRONT 区，不能整篇当封面放过。"""
        rows = (
            ("这是第一段正文，行文里没有任何章节结构。", "justify", "楷体", 10.5),
            ("这是第二段正文，同样没有章节结构。", "justify", "楷体", 10.5),
        )
        blocks, issues = self._check(rows)
        self.assertEqual(["body", "body"], [b["kind"] for b in blocks])
        self.assertIn("body.east_asia_font", {i["rule_id"] for i in issues})

    def test_software_college_still_checks_author_info(self):
        """软院对作者、导师信息行有明文规范（居右、Times New Roman 小三），仍要查。"""
        rows = (
            ("吉林大学", "center", "宋体", 22),
            (self.TITLE, "center", "黑体", 15),
            ("摘要", "center", "黑体", 16),
            ("指导教师：李四", "center", "宋体", 12),
        )
        blocks, issues = self._check(rows, "jlu_se")
        kinds = {b["text"]: b["kind"] for b in blocks}
        self.assertEqual("cover_info", kinds["吉林大学"])
        self.assertEqual("author_info", kinds["指导教师：李四"])
        self.assertIn("author_info.alignment", {i["rule_id"] for i in issues})


class FrontMatterMediaTest(unittest.TestCase):
    """前置区（承诺书页、封面、目录之前）的图片/表格不按图表规则查。

    校徽、整页扫描的承诺书、封面信息表都是版式元素，要求它们配“图1-1/表1-1”题注
    属于报错内容与事实不符；正文里的图片照旧按图表规则查。
    """

    TITLE = "基于深度学习的论文格式校验系统设计与实现"

    @staticmethod
    def _paragraph(index, text, *, align="justify", cjk="宋体", size=12, indent=None):
        return {
            "type": "paragraph", "index": index, "kind": classify_kind(text),
            "text": text, "alignment": align, "first_line_indent_pt": indent,
            "line_spacing_multiple": 1.5,
            "runs": [{"text": text, "font_name": "Times New Roman",
                      "east_asia_font": cjk, "font_size_pt": size, "bold": False}],
        }

    @staticmethod
    def _image(index):
        return {"type": "image", "index": index, "path": "images/img_0000.png",
                "width_inch": 2.0, "height_inch": 2.0, "align": "center"}

    def _blocks(self, *, image_in_body, with_index=True):
        """前置区放一张校徽（承诺书页之后），正文里可选放一张插图。

        with_index=False 复现真实解析顺序：解析层先校正 kind、后统一编号。
        """
        blocks = [
            self._paragraph(0, "吉林大学学士学位论文（设计）承诺书",
                            align="center", size=16),
            self._paragraph(1, "本人郑重承诺：所呈交的学位论文独立完成。", size=14),
            self._image(2),
            self._paragraph(3, self.TITLE, align="center", cjk="黑体", size=15),
            self._paragraph(4, "摘要", align="center", cjk="黑体", size=16),
            self._paragraph(5, "第1章 绪论", align="center", cjk="黑体", size=16),
            self._paragraph(6, "本文围绕论文格式校验展开研究。", indent=24),
        ]
        if image_in_body:
            blocks.append(self._image(7))
        if with_index:
            for index, block in enumerate(blocks):
                block["index"] = index
        apply_contextual_kinds(blocks)
        return blocks

    def _ids(self, blocks):
        return _rule_ids({"sections": [], "blocks": blocks}, _rules("jlu_cs"))

    def test_cover_media_is_marked_as_front_matter(self):
        blocks = self._blocks(image_in_body=False)
        self.assertTrue(blocks[2].get("front_matter"))

    def test_body_image_is_checked_while_front_matter_image_is_not(self):
        """"缺图题"撤销后，改用 figure.placement（随文排）钉住这道闸。

        图排在正文首次提及之前 → 报；标了 front_matter（图片属版式元素）→ 整块跳过。
        """
        def blocks(*, front_matter):
            image = self._image(0)
            if front_matter:
                image["front_matter"] = True
            return [
                image,
                self._paragraph(1, "图1-1 系统架构", align="center", cjk="黑体", size=10.5),
                self._paragraph(2, "系统由三个模块组成，如图1-1所示。"),
            ]

        self.assertIn("figure.placement", self._ids(blocks(front_matter=False)))
        self.assertNotIn("figure.placement", self._ids(blocks(front_matter=True)))

    def test_image_without_a_caption_is_no_longer_reported(self):
        """模板只写“图号和图题名**排于图的下方**”（排在哪侧），没有一句“每张图都必须有
        图题”——这条推断出来的检查已撤销，不许以任何形式复活。"""
        blocks = [
            self._image(0),
            self._paragraph(1, "系统由三个模块组成。"),
        ]
        for index, block in enumerate(blocks):
            block["index"] = index
        self.assertEqual([], [x for x in self._ids(blocks)
                              if x.endswith("caption_missing")])

    def test_marking_does_not_depend_on_block_index(self):
        """解析层先校正 kind、后编号，打标记时段落块上还没有 index。"""
        blocks = self._blocks(image_in_body=True, with_index=False)
        self.assertTrue(blocks[2].get("front_matter"))
        self.assertFalse(blocks[7].get("front_matter"))

    def test_scan_of_the_pledge_page_is_front_matter_too(self):
        """整页扫描的承诺书就是前置区的图片，同样不按插图要求题注。"""
        blocks = [
            self._paragraph(0, "吉林大学学士学位论文（设计）承诺书",
                            align="center", size=16),
            self._image(1),
            self._paragraph(2, "第1章 绪论", align="center", cjk="黑体", size=16),
            self._paragraph(3, "本文围绕论文格式校验展开研究。", indent=24),
        ]
        for index, block in enumerate(blocks):
            block["index"] = index
        apply_contextual_kinds(blocks)
        self.assertTrue(blocks[1].get("front_matter"))
        self.assertEqual([], [x for x in self._ids(blocks) if x.startswith("figure.")])

    def test_document_without_front_matter_boundary_keeps_the_image_checked(self):
        """没有前置区边界（例如只给了一段正文的片段）时不能把图片当版式元素放过。"""
        blocks = [
            self._image(0),
            self._paragraph(1, "图1-1 系统架构", align="center", cjk="黑体", size=10.5),
            self._paragraph(2, "系统由三个模块组成，如图1-1所示。"),
        ]
        for index, block in enumerate(blocks):
            block["index"] = index
        apply_contextual_kinds(blocks)
        self.assertFalse(blocks[0].get("front_matter"))
        self.assertIn("figure.placement", self._ids(blocks))


class CaptionSentenceVsBodyTest(unittest.TestCase):
    """正文里“表1-1给出了……”这类句子不是题注，不该被题注规则量。"""

    @staticmethod
    def _paragraph(index, text, *, align="justify", cjk="宋体", size=12, indent=None):
        return {
            "type": "paragraph", "index": index, "kind": classify_kind(text),
            "text": text, "alignment": align, "first_line_indent_pt": indent,
            "line_spacing_multiple": 1.5,
            "runs": [{"text": text, "font_name": "Times New Roman",
                      "east_asia_font": cjk, "font_size_pt": size, "bold": False}],
        }

    @staticmethod
    def _table(index):
        return {"type": "table", "index": index, "header_repeat": True,
                "style": None, "align": "center",
                "cells": [{"row": 0, "col": 0, "text": "列", "font_name": "Times New Roman",
                           "east_asia_font": "宋体", "font_size_pt": 10.5,
                           "runs": [{"text": "列", "font_name": "Times New Roman",
                                     "east_asia_font": "宋体", "font_size_pt": 10.5}]}]}

    def _blocks(self, *, with_table):
        blocks = [
            self._paragraph(0, "第1章 绪论", align="center", cjk="黑体", size=16),
            self._paragraph(1, "本文围绕论文格式校验展开研究。", indent=24),
            # 正文句：以表号开头、以句号结尾，上下都不挨着表格
            self._paragraph(2, "表1-1给出了本文使用的数据集规模。", align="left", size=12),
        ]
        if with_table:
            # 真题注：紧邻它的表格（表题在表格上方），字号字体故意写错以验证仍被检查
            blocks.append(self._paragraph(3, "表1-2 数据集规模", align="center",
                                          cjk="宋体", size=12))
            blocks.append(self._table(4))
        for index, block in enumerate(blocks):
            block["index"] = index
        apply_contextual_kinds(blocks)
        return blocks

    def _ids(self, blocks):
        return _rule_ids({"sections": [], "blocks": blocks}, _rules("jlu_cs"))

    def test_sentence_starting_with_a_table_number_is_body(self):
        blocks = self._blocks(with_table=False)
        self.assertEqual("body", blocks[2]["kind"])
        self.assertEqual([], [i for i in self._ids(blocks)
                              if i.startswith("table.caption")])

    def test_real_caption_next_to_its_table_is_still_checked(self):
        blocks = self._blocks(with_table=True)
        self.assertEqual("table_caption", blocks[3]["kind"])
        ids = self._ids(blocks)
        self.assertIn("table.caption.font", ids)


class ReferenceTypeMarkTest(unittest.TestCase):
    """文献类型标识的方括号半角/全角都认，真的没有标识才报。

    规范的条目序号本身写作全角“［1］”，学生顺着写全角“［J］”是自然写法。
    """

    RULE = next(r for r in SEMANTIC_RULES if r["rule_id"] == "reference.format")

    def _conclusions(self, text):
        blocks = {"reference_entry": [{"index": 0, "text": text}]}
        return [i["conclusion"] for i in check_reference(self.RULE, blocks)]

    def test_halfwidth_type_mark_is_accepted(self):
        self.assertEqual([], self._conclusions(
            "［1］张三．深度学习综述[J]．计算机学报，2020，29(12)：1-10．"))

    def test_fullwidth_type_mark_is_accepted(self):
        self.assertEqual([], self._conclusions(
            "［1］张三．深度学习综述［J］．计算机学报，2020，29(12)：1-10．"))

    def test_missing_pages_of_a_journal_entry_is_reported(self):
        """§6.3 连续出版物样例：卷号(期号)后的起止页码是必备部件。"""
        self.assertEqual(
            ["参考文献条目的著录项与模板样例不符：缺卷(期)号后的起止页码"],
            self._conclusions("［1］张三．深度学习综述[J]．计算机学报，2020．"))

    def test_detached_page_range_is_reported_as_layout_problem(self):
        """页码写了但没按样例接在“卷号(期号)：”后（樊静文式）→ 报“未按样例接排”，
        不能报“缺页码”——学生可见的话必须与条目事实相符。"""
        self.assertEqual(
            ["参考文献条目的著录项与模板样例不符："
             "起止页码未按“卷号(期号)：起止页码”的样例接排"],
            self._conclusions(
                "［2］Abadi M, Barham P. TensorFlow: A system for large-scale "
                "machine learning[J]. USENIX Association, 2016. 265–283."))

    def test_missing_type_mark_is_still_reported(self):
        self.assertEqual(["参考文献条目缺少文献类型标识"], self._conclusions(
            "［1］张三．深度学习综述．计算机学报，2020．"))

    def test_composite_type_marks_from_the_spec_table_are_accepted(self):
        """规范 §7.2 表里列了 [DB/OL]、[CP/DK]、[M/CD] 这类“类型/载体”组合。"""
        for mark in ("[DB/OL]", "[CP/DK]", "[M/CD]", "[EB/OL]"):
            with self.subTest(mark=mark):
                self.assertEqual([], self._conclusions(
                    f"［1］张三．网络资源{mark}．2020．"))


class ReferenceEntryFormatTest(unittest.TestCase):
    """§6.3：按模板逐类给出的样例核对条目的著录项（齐全、顺序）。

    样例本身必须零报——既要真查，又不能查得比模板宽。
    """

    RULE = next(r for r in SEMANTIC_RULES if r["rule_id"] == "reference.format")

    # 模板「各类参考文献条目的编排格式及示例如下」中的“例如”行，原文照录。
    TEMPLATE_EXAMPLES = (
        "例如：［1］毛峡，丁玉宽．图像的情感特征分析及其和谐感评价[J] ．电子学报, 2001，29(12A) ：1923-1927．",
        "例如：[3] 刘国钧，王连成．图书馆史研究[M] ．北京：高等教育出版社，1979：15-18 ，31．",
        "例如：[4] 毛 峡．绘画的音乐表现[A] ．中国人工智能学会2001年全国学术年会论文集[C] ．北京：北京邮电大学出版社, 2001：739-740．",
        "例如：[5] 张和生．地质力学系统理论[D] ．太原：太原理工大学，1998．",
        "例如：[6] 冯西桥．核反应堆压力容器的LBB分析[R] ．北京：清华大学核能技术设计研究院，1997．",
        "例如：［7］ 姜锡洲．一种温热外敷药制备方案[P] ．中国专利：881056078，1983-08-12．",
        "例如：[8] GB/T 16159—1996，汉语拼音正词法基本规则[S] ．北京：中国标准出版社，1996．",
        "例如：[9] 毛 峡．情感工学破解‘舒服’之迷[N] ．光明日报，2000-4-17(B1)．",
        "例如：［10］王明亮．中国学术期刊标准化数据库系统工程的[EB/OL] ．"
        "http://www.cajcd.cn/pub/wml.txt/9808 10-2.html,1998-08-16/1998-10-04．",
    )

    def _conclusions(self, text, **extra):
        blocks = {"reference_entry": [{"index": 0, "text": text, **extra}]}
        return [i["conclusion"] for i in check_reference(self.RULE, blocks)]

    def test_template_examples_are_accepted(self):
        for text in self.TEMPLATE_EXAMPLES:
            with self.subTest(text=text[:20]):
                self.assertEqual([], self._conclusions(text))

    def test_missing_publication_place_is_reported(self):
        """专著样例是“出版地：出版者，出版年”，只写出版者就是缺项。"""
        self.assertEqual(
            ["参考文献条目的著录项与模板样例不符：缺“：”分隔的出版地／保存地等项"],
            self._conclusions(
                "［2］Manning C D, Raghavan P, Schütze H. Introduction to "
                "Information Retrieval[M]. Cambridge University Press, 2008."))

    def test_year_before_the_type_mark_is_reported(self):
        """样例的次序是“责任者．题名[标识]．…，年份”，年份写到标识前就是顺序不对。"""
        self.assertEqual(
            ["参考文献条目的著录项与模板样例不符：出版年份排在文献类型标识之前"],
            self._conclusions(
                "［5］杭州市数据资源管理局. (2019). 杭州城市大脑发展报告[Z]. "
                "杭州：杭州城市大脑有限公司."))

    def test_electronic_entry_without_date_is_reported(self):
        """电子文献样例的“发表或更新的期/引用日期(任选)”至少要有一种日期。"""
        self.assertEqual(
            ["参考文献条目的著录项与模板样例不符：缺出版年份"],
            self._conclusions(
                "［3］Django Documentation[EB/OL]. "
                "https://docs.djangoproject.com/en/4.0/"))

    def test_missing_period_after_the_type_mark_is_reported(self):
        self.assertEqual(
            ["参考文献条目的著录项与模板样例不符：文献类型标识后面缺句点"],
            self._conclusions(
                "［4］Ganglberger F. Spatial-data-driven layouting for brain "
                "network visualization[J]  Computers & Graphics, 2022:105."))

    def test_parts_on_the_continuation_line_are_not_reported_missing(self):
        """长条目在 Word 里折成两段（reference_continuation）：页码在续行上也算有。"""
        blocks = {
            "reference_entry": [{
                "index": 0,
                "text": "［10］王明亮．中国学术期刊标准化数据库系统工程的[EB/OL]．",
            }],
            "reference_continuation": [{
                "index": 1,
                "text": "http://www.cajcd.cn/pub/wml.txt,1998-08-16/1998-10-04．",
            }],
        }
        self.assertEqual([], [i["conclusion"]
                              for i in check_reference(self.RULE, blocks)])

    def test_entry_samples_are_quoted_from_the_rules_txt(self):
        """样例只能来自真标准文本，不能自己编。"""
        with open(path_of("jlu_cs", "corpus", RULES_DIR), encoding="utf-8") as handle:
            txt = handle.read()
        for key, sample in self.RULE["entry_samples"].items():
            with self.subTest(sample=key):
                self.assertIn(sample, txt)

    def test_every_sampled_type_has_its_own_features(self):
        """有样例的类型标识都要有部件要求，否则样例形同虚设。"""
        for key in self.RULE["entry_samples"]:
            with self.subTest(key=key):
                self.assertTrue(self.RULE["entry_features"].get(key))
                self.assertIn("mark_period", self.RULE["entry_features"][key])


class KeywordTitleOwnLineTest(unittest.TestCase):
    """§2.2/§2.4：关键词标题须单独一行（模板：“…居左，单独一行”）。

    关键词的标签、数量、分隔符、字体检查都以“这一段以关键词标签开头”为前提：
    学生把关键词粘在摘要正文末尾时，整组检查会一条都不报。所以“没单独一行”
    本身要报出来，同时不能冤枉正文里正常提到“关键词”的句子。
    """

    def _ids(self, kind, text, college="jlu_cs"):
        document = {"sections": [], "blocks": [_paragraph(0, kind, text)]}
        return _rule_ids(document, _rules(college))

    def test_keywords_glued_to_the_abstract_are_reported(self):
        for college in _COLLEGES:
            label = "关键字" if college == "jlu_cs" else "关键词"
            with self.subTest(college=college):
                ids = self._ids(
                    "abstract_body",
                    f"针对上述问题，本文提出了研究方法。实验证明该方法有效。"
                    f"{label}：机器学习，癌症检测，深度学习", college)
                self.assertIn("keyword.title.own_line", ids)

    def test_keywords_on_their_own_line_are_not_reported(self):
        for college in _COLLEGES:
            label = "关键字" if college == "jlu_cs" else "关键词"
            with self.subTest(college=college):
                ids = self._ids("keyword_title",
                                f"{label}：机器学习，癌症检测，深度学习", college)
                self.assertNotIn("keyword.title.own_line", ids)

    def test_a_sentence_that_mentions_keywords_is_not_reported(self):
        """冒号后接的是句子（句中出现句末标点）时不是关键词清单。"""
        ids = self._ids(
            "abstract_body",
            "系统会根据关键词：主题、情感倾向来做分类，这一部分由第三章展开。")
        self.assertNotIn("keyword.title.own_line", ids)

    def test_a_short_list_without_the_label_colon_is_not_reported(self):
        """冒号后不足模板要求的关键词个数时，是正文里的冒号，不是关键词清单。"""
        ids = self._ids("abstract_body",
                        "本文围绕关键词：算法，模型展开讨论。")
        self.assertNotIn("keyword.title.own_line", ids)

    def test_soft_break_counts_as_its_own_line(self):
        """段内软换行之后另起的一行，视觉上已经单独一行。"""
        ids = self._ids("abstract_body",
                        "实验证明该方法有效。\n关键词：机器学习，癌症检测，深度学习")
        self.assertNotIn("keyword.title.own_line", ids)

    def test_english_keywords_glued_to_the_abstract_are_reported(self):
        ids = self._ids(
            "en_abstract_body",
            "This paper proposes a method and the experiments prove it works. "
            "Keywords: machine learning, cancer detection, deep learning")
        self.assertIn("en_keyword.title.own_line", ids)


class UnitsWordingTest(unittest.TestCase):
    """俗称单位（公里/公斤）只是名称不规范，不能说成“非法定计量单位”。

    规范原文只说“量和单位采用 GB3100～GB3102-93”，而 km/kg 正是法定计量单位，
    公里/公斤只是它们的俗称；报错话术与事实不符时学生会拿规范原文来质疑。
    """

    RULE = next(r for r in SEMANTIC_RULES if r["rule_id"] == "units")

    def _conclusions(self, text):
        return [i["conclusion"] for i in
                check_units(self.RULE, {"body": [{"index": 0, "text": text}]})]

    def test_colloquial_unit_names_are_still_reported(self):
        self.assertEqual(["计量单位应使用国家标准名称与符号"],
                         self._conclusions("试验路段长度 12 公里。"))

    def test_city_units_are_still_reported(self):
        self.assertEqual(["计量单位应使用国家标准名称与符号"],
                         self._conclusions("称重 3 市斤。"))

    def test_conclusion_does_not_call_them_illegal_units(self):
        for conclusion in self._conclusions("试验路段长度 12 公里，载重 3 公斤。"):
            self.assertNotIn("非法定计量单位", conclusion)


class ChapterTitleStyleRuleTest(unittest.TestCase):
    """参考文献标题与 Abstract 标题的字号对齐（模板注释里写明的部分）。

    目录标题与致谢标题的字号/对齐已撤销（模板里“目　　录”“致　　谢”只有词本身，
    没有任何排版说明），所以这两项不在这里断言；致谢/参考文献标题的**文字**逐字
    比对由 FixedTitleTextRuleTest 覆盖。
    """

    # (kind, 模板原文标题, 计算机字体, 软件字体)
    # 标题文字一律用模板原文：条文字逐字比对（*_title_text）也在这条链路上走，
    # 写成别的空白形式会多报一条"文字与模板不一致"，干扰样式断言。
    TITLES = {
        "abstract_title": ("摘要", "黑体", "宋体"),
        "en_abstract_title": ("Abstract", "Times New Roman", "Times New Roman"),
        "reference_title": ("参考文献", "黑体", "宋体"),
    }

    def _ids(self, kind, size_pt, college="jlu_cs", alignment="center", bold=True):
        text, cs_font, se_font = self.TITLES[kind]
        font = cs_font if college == "jlu_cs" else se_font
        block = _paragraph(0, kind, text, alignment=alignment,
                           runs=[{"text": text, "font_name": font,
                                  "east_asia_font": font, "font_size_pt": size_pt,
                                  "bold": bold}])
        return _rule_ids({"sections": [], "blocks": [block]}, _rules(college))

    def _title_ids(self, ids, prefix):
        return [i for i in ids if i.startswith(prefix)]


    def test_acknowledgement_title_skips_empty_runs(self):
        """模板里“致    谢”的首个 run 是空 run（12pt），它不渲染字符，不算字号错。

        这里拿“摘要”标题验证同一件事（致谢标题的字号已撤销，没有可断言的样式项）：
        首个空 run 是 12pt、真正显示文字的那个 run 是 16pt，不该报字号错。
        """
        runs = [{"text": "", "font_name": "宋体", "east_asia_font": "宋体",
                 "font_size_pt": 12, "bold": False},
                {"text": "摘要", "font_name": "黑体", "east_asia_font": "黑体",
                 "font_size_pt": 16, "bold": False}]
        block = _paragraph(0, "abstract_title", "摘要", alignment="center", runs=runs)
        ids = _rule_ids({"sections": [], "blocks": [block]}, _rules("jlu_cs"))
        self.assertEqual([], [i for i in ids if i.startswith("abstract.title.")])


class FixedTitleTextRuleTest(unittest.TestCase):
    """固定组成部件的标题文字：照抄模板原文即可，写成别的形式要报，理由要说清。

    模板里“致    谢”“目    录”的空白是模板自己排的（各 4 个半角空格），所以严格
    逐字比对：少空、多空、换成全角空格、换成别的字都算与模板不一致。报错时必须
    说清差的是空白（几个、哪种），不能只丢一句“标题不符合要求”。
    模板里每个样例行都带排版说明括注（“摘要（黑体三号字、居中）”），比对前要剥掉。
    """

    CASES = {
        "abstract_title": (("abstract",), "abstract.title_text", "摘要"),
        "en_abstract_title": (("english_abstract",), "en_abstract.title_text", "Abstract"),
        "reference_title": (("references",), "reference.title_text", "参考文献"),
        "acknowledgement_title": (("acknowledgement",), "acknowledgement.title_text",
                                  TPL_ACK_TITLE),
    }

    def _issues(self, kind, text, college="jlu_cs"):
        runs = [{"text": text, "font_name": "黑体", "east_asia_font": "黑体",
                 "font_size_pt": 16, "bold": True}]
        block = _paragraph(0, kind, text, alignment="center", runs=runs)
        document = {"sections": [], "blocks": [block]}
        rule_id = self.CASES[kind][1]
        return [i for i in RuleEngine(_rules(college)).check(document)["issues"]
                if i["rule_id"] == rule_id]

    def test_all_fixed_titles_match_the_template(self):
        for kind, (_, rule_id, canonical) in self.CASES.items():
            for college in _COLLEGES:
                with self.subTest(kind=kind, college=college):
                    self.assertEqual([], self._issues(kind, canonical, college),
                                     f"{rule_id} 对模板原文误报")

    def test_canonical_text_is_written_in_rules(self):
        """模板原文必须落在 rules 里，代码不硬编码（改模板即改 rules）。"""
        for college in _COLLEGES:
            rules = _rules(college)
            for kind, (path, _, canonical) in self.CASES.items():
                with self.subTest(college=college, kind=kind):
                    self.assertEqual(canonical, rules[path[0]]["title_text"])

    def test_missing_space_is_reported_with_space_reason(self):
        issues = self._issues("acknowledgement_title", "致谢")
        self.assertEqual(1, len(issues))
        self.assertEqual("error", issues[0]["severity"])
        self.assertIn("致谢标题文字与模板不一致", issues[0]["message"])
        self.assertIn("4 个半角空格", issues[0]["suggestion"])
        self.assertIn("0 个空格", issues[0]["suggestion"])


    def test_many_extra_spaces_are_reported(self):
        issues = self._issues("acknowledgement_title", "致" + " " * 100 + "谢")
        self.assertEqual(1, len(issues))
        self.assertIn("100 个半角空格", issues[0]["suggestion"])

    def test_wrong_wording_is_reported(self):
        issues = self._issues("reference_title", "参考资料")
        self.assertEqual(1, len(issues))
        self.assertIn("「参考资料」", issues[0]["actual"])
        self.assertIn("「参考文献」", issues[0]["expected"])
        # 文字本身不同，说不了"空几格"，只给模板原文让用户照抄
        self.assertNotIn("个半角空格", issues[0]["suggestion"])

    def test_sample_line_note_is_stripped_before_comparison(self):
        """模板样例行“摘要（黑体三号字、居中）”的括注是排版说明，不算标题文字。"""
        self.assertEqual([], self._issues("abstract_title", "摘要（黑体三号字、居中）"))
        self.assertEqual([], self._issues("reference_title", "参考文献(黑体三号字、居中)"))


class NoteSingleItemInlineRuleTest(unittest.TestCase):
    """§4.6 第 4 条：只有一条注意事项时应紧跟标志书写，多条才换行编号。"""

    def _ids(self, blocks):
        return _rule_ids({"sections": [], "blocks": blocks}, _rules("jlu_cs"))

    def test_lone_marker_followed_by_body_is_reported(self):
        blocks = [_paragraph(0, "note", "【注意】"),
                  _paragraph(1, "body", "只有一条注意事项时应紧跟在标志之后。")]
        self.assertIn("note.single_item_inline", self._ids(blocks))

    def test_numbered_multi_item_notes_are_not_reported(self):
        blocks = [_paragraph(0, "note", "【注意】"),
                  _paragraph(1, "list_item", "1）标志同与之对应的标题对齐；"),
                  _paragraph(2, "list_item", "2）字号同正文。")]
        self.assertNotIn("note.single_item_inline", self._ids(blocks))

    def test_inline_content_on_the_marker_paragraph_is_not_reported(self):
        blocks = [_paragraph(0, "note", "【注意】内容直接写在标志后面。")]
        self.assertNotIn("note.single_item_inline", self._ids(blocks))


class FontNameEquivalenceTest(unittest.TestCase):
    """模板 document.xml 里字体写成英文名（SimSun），rules 写“宋体”，必须视为同一种。"""

    def _ids(self, east_asia_font):
        text = "正文段落。"
        block = _paragraph(0, "body", text,
                           runs=[{"text": text, "font_name": east_asia_font,
                                  "east_asia_font": east_asia_font,
                                  "font_size_pt": 12, "bold": False}])
        return _rule_ids({"sections": [], "blocks": [block]}, _rules("jlu_cs"))

    def test_english_alias_of_songti_is_equivalent(self):
        self.assertNotIn("body.east_asia_font", self._ids("SimSun"))

    def test_chinese_name_still_matches(self):
        self.assertNotIn("body.east_asia_font", self._ids("宋体"))

    def test_different_font_is_still_reported(self):
        self.assertIn("body.east_asia_font", self._ids("SimHei"))


class KeywordLabelClassifyTest(unittest.TestCase):
    """分类层与规则层对“关键字”的判定不能互斥：否则该检查永不触发。"""

    def test_jian_zi_label_is_classified_as_keyword_title(self):
        self.assertEqual("keyword_title", classify_kind("关键字：深度学习，目标检测"))
        self.assertEqual("keyword_title", classify_kind("关键词：深度学习，目标检测"))


class TemplateSampleLineClassifyTest(unittest.TestCase):
    """模板样例行自带排版说明括注（“（黑体三号字、居中）”），分类前必须先剥掉。

    官方模板的每个样例行都写成“锚点＋（排版说明）”，例如“摘要（黑体三号字、居中）”。
    若括注参与锚点比对，这行就落不到对应的 kind 上：摘要标题会被当成摘要正文
    （报“摘要正文字体/字号不符合要求”），而区域状态机也会跟着走偏。
    """

    def test_cs_abstract_title_sample(self):
        self.assertEqual("abstract_title", classify_kind("摘要（黑体三号字、居中）"))

    def test_cs_english_abstract_title_sample(self):
        self.assertEqual("en_abstract_title",
                         classify_kind("Abstract（Times New Roman小三号字、加粗，居中）"))

    def test_cs_chinese_title_sample(self):
        """模板标题页写“论文题目（黑体小三号字、居中）”，与规范“中文题目”同义。"""
        self.assertEqual("title", classify_kind("论文题目（黑体小三号字、居中）"))
        self.assertEqual("title", classify_kind("中文题目（黑体小三号字、居中）"))

    def test_cs_english_title_sample(self):
        self.assertEqual("en_title", classify_kind("英文题目（Times New Roman小三号字、居中）"))

    def test_author_info_line(self):
        """题名页信息行（“指导教师：…”）不是正文，不能按正文字号查。"""
        self.assertEqual("author_info", classify_kind("指导教师：张三　　教授"))

    def test_long_text_line_is_not_author_info(self):
        """只有短行才算信息行，“作者简介：……”这类正文句仍走正文检查。"""
        self.assertNotEqual("author_info", classify_kind("作者简介：" + "本文研究了" * 12))

    def test_trailing_note_is_stripped_for_keyword_samples(self):
        self.assertEqual("keyword_title",
                         classify_kind("关键字： ，  ，  （黑体4号字居左，单独一行，3-5个关键字）"))


class TemplateSampleKeywordRuleTest(unittest.TestCase):
    """模板样例关键词行的用词与数量不能按模板自己的说明文字去数。"""

    def _count_issues(self, block, college="jlu_cs"):
        issues = RuleEngine(_rules(college)).check(
            {"sections": [], "blocks": [block]})["issues"]
        counts = [i for i in issues if i["rule_id"] == "keyword.count"]
        others = [i["rule_id"] for i in issues
                  if i["rule_id"] in ("keyword.separator", "keyword.terminal_punct")]
        return counts, others

    def test_cs_sample_keyword_line_counts_slots_not_the_note(self):
        """空槽本身算“没写关键词”，但括注里的逗号不能被数成关键词。"""
        block = _paragraph(
            0, "keyword_title",
            "关键字： ，  ，  （黑体4号字居左，单独一行，3-5个关键字，逗号间隔，末尾无标点）",
            alignment="justify",
            runs=[{"text": "关键字： ，  ，  （黑体4号字居左，单独一行，3-5个关键字，"
                           "逗号间隔，末尾无标点）",
                   "font_name": "SimHei", "east_asia_font": "黑体",
                   "font_size_pt": 14, "bold": False}],
        )
        counts, others = self._count_issues(block)
        self.assertEqual(1, len(counts))
        self.assertEqual("0 个", counts[0]["actual"])
        self.assertEqual([], others)

    def test_software_college_sample_keyword_line_behaves_the_same(self):
        """软件学院模板的关键词标签是宋体四号加粗，用词必须是“关键词”。"""
        block = _paragraph(
            0, "keyword_title", "关键词： ，  ，  （宋体，4号字，加粗居左）",
            alignment="justify",
            runs=[{"text": "关键词： ，  ，  （宋体，4号字，加粗居左）", "font_name": "SimSun",
                   "east_asia_font": "宋体", "font_size_pt": 14, "bold": True}],
        )
        counts, others = self._count_issues(block, "jlu_se")
        self.assertEqual(1, len(counts))
        self.assertEqual("0 个", counts[0]["actual"])
        self.assertEqual([], others)


class AlignmentEquivalenceTest(unittest.TestCase):
    """规范写“居左”而模板写成“两端对齐”时不能报错，反向则必须报。"""

    def _align_issues(self, actual, expected_rule, kind="en_keyword_body"):
        rules = _rules("jlu_cs")
        rules["english_abstract"]["keyword_body"] = dict(
            rules["english_abstract"]["keyword_body"], alignment=expected_rule)
        block = _paragraph(0, kind, "Keywords: deep learning, object detection",
                           alignment=actual)
        return [i for i in _rule_ids({"sections": [], "blocks": [block]}, rules)
                if i.endswith(".alignment")]

    def test_left_expected_accepts_justify(self):
        self.assertEqual([], self._align_issues("justify", "left"))

    def test_left_expected_still_accepts_left(self):
        self.assertEqual([], self._align_issues("left", "left"))

    def test_left_expected_rejects_center(self):
        self.assertEqual(["en_keyword.body.alignment"], self._align_issues("center", "left"))

    def test_justify_expected_rejects_left(self):
        """正文要求两端对齐时不能放宽：写成左对齐行末仍会参差不齐。"""
        self.assertEqual(["en_keyword.body.alignment"], self._align_issues("left", "justify"))

    def test_center_expected_rejects_justify(self):
        self.assertEqual(["en_keyword.body.alignment"], self._align_issues("justify", "center"))


class ParagraphMetricsWiringTest(unittest.TestCase):
    """首行缩进/行距写在 rules 里就必须真的被查（此前只有正文字体字号被查）。"""

    def _ids(self, block, college="jlu_cs"):
        return _rule_ids({"sections": [], "blocks": [block]}, _rules(college))

    def test_body_line_spacing_follows_rules(self):
        block = _paragraph(0, "abstract_body", "摘要正文内容。", line_spacing_multiple=1.0)
        self.assertIn("abstract.body.line_spacing", self._ids(block))

    def test_correct_body_line_spacing_is_clean(self):
        block = _paragraph(0, "abstract_body", "摘要正文内容。", line_spacing_multiple=1.5)
        self.assertNotIn("abstract.body.line_spacing", self._ids(block))

    def test_indent_is_checked_against_rules(self):
        rules = _rules("jlu_cs")
        rules["abstract"]["body"] = dict(rules["abstract"]["body"], first_line_indent_pt=24)
        block = _paragraph(0, "abstract_body", "摘要正文内容。", first_line_indent_pt=0,
                           line_spacing_multiple=1.5)
        ids = _rule_ids({"sections": [], "blocks": [block]}, rules)
        self.assertIn("abstract.body.first_line_indent", ids)

    def test_indent_tolerance_is_respected(self):
        rules = _rules("jlu_cs")
        rules["abstract"]["body"] = dict(rules["abstract"]["body"], first_line_indent_pt=24)
        block = _paragraph(0, "abstract_body", "摘要正文内容。", first_line_indent_pt=24.5,
                           line_spacing_multiple=1.5)
        ids = _rule_ids({"sections": [], "blocks": [block]}, rules)
        self.assertNotIn("abstract.body.first_line_indent", ids)


class KeywordTitleAlignmentScopeTest(unittest.TestCase):
    """关键词标题的对齐方式只在规范写了“居左”的学院查（计软 §2.3/§2.7 写了）。"""

    def _ids(self, college):
        return _rule_ids({"sections": [], "blocks": [
            _paragraph(0, "keyword_title", "关键词：", alignment="center")]},
            _rules(college))

    def test_cs_keyword_title_must_be_left(self):
        self.assertIn("keyword.title.alignment", self._ids("jlu_cs"))

    def test_se_keyword_title_alignment_is_not_required(self):
        """软院 §2.3 只写“单独一行，标题上方空一行”，没规定对齐方式。"""
        self.assertNotIn("keyword.title.alignment", self._ids("jlu_se"))

    def test_se_english_keyword_title_alignment_is_not_required(self):
        ids = _rule_ids({"sections": [], "blocks": [
            _paragraph(0, "en_keyword_title", "Keywords:", alignment="center")]},
            _rules("jlu_se"))
        self.assertNotIn("en_keyword.title.alignment", ids)

    def test_cs_english_keyword_title_must_be_left(self):
        """计软 §2.7 明写“Keywords标题 …居左”。"""
        ids = _rule_ids({"sections": [], "blocks": [
            _paragraph(0, "en_keyword_title", "Keywords：", alignment="center")]},
            _rules("jlu_cs"))
        self.assertIn("en_keyword.title.alignment", ids)


class NoteTopAlignedTest(unittest.TestCase):
    """§4.6 只有「注释起排：另起一行，用【注】开头」，**没有**"顶格/不缩进"的字样。

    所以 note.first_line_indent 这条判据没有模板原句，config/rules 的 notes 里不放
    first_line_indent_pt，该判定不生效（下面这条断言因此是恒真的"关着"状态，
    不是"检查通过"）。真正钉住它的是 test_defaults_consistency 里的
    test_rules_do_not_enable_checks_without_a_template_sentence。
    """

    def _ids(self, alignment=None, indent=None, college="jlu_cs"):
        block = _paragraph(0, "note", "【注意】表中数据为三次平均值。")
        if alignment:
            block["alignment"] = alignment
        if indent is not None:
            block["first_line_indent_pt"] = indent
        return _rule_ids({"sections": [], "blocks": [block]}, _rules(college))

    def test_top_aligned_note_is_clean(self):
        self.assertNotIn("note.first_line_indent", self._ids(alignment="left"))


    def test_alignment_is_not_judged_without_a_heading(self):
        """对齐基准来自最近的标题；没有标题可依，就不判对齐。"""
        for alignment in ("left", "justify", "center"):
            self.assertNotIn("note.alignment", self._ids(alignment=alignment))


class NoteFollowHeadingAlignmentTest(unittest.TestCase):
    """模板说明「5．注释」第 1 条：注意标志同与之对应的标题对齐。

    基准只取标题（说明写的是“标题”，图表题注不在其列）；左对齐与两端对齐视为
    同一种，只报明确不同的情形。
    """

    def _ids(self, note_alignment, heading_kind="heading_2", heading_alignment="left",
             college="jlu_cs", extra=()):
        blocks = [
            _paragraph(0, heading_kind, "2.1 二级标题", alignment=heading_alignment),
        ]
        blocks.extend(extra)
        blocks.append(_paragraph(len(blocks), "note", "【注意】表中数据为三次平均值。",
                                 alignment=note_alignment))
        return _rule_ids({"sections": [], "blocks": blocks}, _rules(college))

    def test_declared_in_both_colleges(self):
        """依据是两份模板里逐字相同的说明，所以两院都要查。"""
        for college in _COLLEGES:
            self.assertEqual("follow_heading", _rules(college)["notes"].get("alignment"))

    def test_justified_note_under_left_heading_is_clean(self):
        self.assertNotIn("note.alignment", self._ids("justify"))

    def test_left_aligned_note_under_left_heading_is_clean(self):
        self.assertNotIn("note.alignment", self._ids("left"))

    def test_note_under_centered_heading_must_be_centered(self):
        ids = self._ids("left", heading_kind="heading_1", heading_alignment="center")
        self.assertIn("note.alignment", ids)

    def test_centered_note_under_centered_heading_is_clean(self):
        self.assertNotIn("note.alignment", self._ids(
            "center", heading_kind="heading_1", heading_alignment="center"))

    def test_caption_is_not_an_alignment_baseline(self):
        """表题居中不构成“对应的标题”：其下顶格注释不该被报（老实现正是在这里误报）。"""
        caption = _paragraph(1, "table_caption", "表2-1  数据表", alignment="center")
        self.assertNotIn("note.alignment", self._ids("left", extra=[caption]))

    def test_nearest_heading_wins(self):
        """往前取最近的标题：中间隔着一个小节时，以小节标题（居左）为准。"""
        section = _paragraph(1, "heading_3", "2.1.1 三级标题", alignment="left")
        self.assertNotIn("note.alignment", self._ids(
            "justify", heading_kind="heading_1", heading_alignment="center",
            extra=[section]))


class NumberFormatExpectedTest(unittest.TestCase):
    """编号格式报错时，“应为”必须保留实际章节/层级，不能写死图1-1/1.1。"""

    def _issues(self, blocks, rule_id):
        return [issue for issue in RuleEngine(_rules("jlu_cs")).check(
            {"sections": [], "blocks": blocks})["issues"]
            if issue["rule_id"] == rule_id]

    def test_figure_caption_expected_uses_chapter(self):
        blocks = [
            _paragraph(0, "heading_1", "第4章 结果"),
            _paragraph(1, "body", "见图4-1。", first_line_indent_pt=24),
            {"type": "image", "index": 2, "path": "media/image1.png",
             "width_inch": 3.0, "height_inch": 2.0, "align": "center"},
            _paragraph(3, "figure_caption", "图4.1 系统框架", alignment="center"),
        ]
        issues = self._issues(blocks, "figure.caption_number")
        reported = [issue for issue in issues
                    if issue["message"] == "图题编号格式不符合要求"]
        self.assertEqual(1, len(reported))
        self.assertEqual("图4-1", reported[0]["expected"])

    def test_table_caption_expected_uses_chapter(self):
        blocks = [
            _paragraph(0, "heading_1", "第3章 实验"),
            _paragraph(1, "body", "见表3-1。", first_line_indent_pt=24),
            _paragraph(2, "table_caption", "表3.1 数据集", alignment="center"),
            {"type": "table", "index": 3, "header_repeat": True, "align": "center",
             "cells": [{"row": 0, "col": 0, "text": "列", "east_asia_font": "宋体",
                        "font_size_pt": 10.5,
                        "runs": [{"text": "列", "east_asia_font": "宋体",
                                  "font_size_pt": 10.5}]}]},
        ]
        issues = self._issues(blocks, "table.caption_number")
        reported = [issue for issue in issues
                    if issue["message"] == "表题编号格式不符合要求"]
        self.assertEqual(1, len(reported))
        self.assertEqual("表3-1", reported[0]["expected"])

    def test_heading_expected_uses_actual_chapter(self):
        reported = self._issues(
            [_paragraph(0, "heading_1", "第 3 章 结果", alignment="center")],
            "heading.heading_1.number")
        self.assertEqual(1, len(reported))
        self.assertEqual("第3章", reported[0]["expected"])

    def test_heading_expected_uses_actual_numbering(self):
        reported = self._issues(
            [_paragraph(0, "heading_2", "3. 3 结果", alignment="left")],
            "heading.heading_2.number")
        self.assertEqual(1, len(reported))
        self.assertEqual("3.3", reported[0]["expected"])


class CaptionNumberContinuityTest(unittest.TestCase):
    """§5.1：图、表编号按章节依序连续编号（此前只有公式查了连续性）。"""

    HEADING = _paragraph(0, "heading_1", "第1章 绪论")

    def _table(self, index):
        return {"type": "table", "index": index, "header_repeat": True, "style": None,
                "align": "center",
                "cells": [{"row": 0, "col": 0, "text": "列", "font_name": "宋体",
                           "east_asia_font": "宋体", "font_size_pt": 10.5,
                           "runs": [{"text": "列", "font_name": "宋体",
                                     "east_asia_font": "宋体", "font_size_pt": 10.5}]}]}

    def _image(self, index):
        return {"type": "image", "index": index, "path": "media/image1.png",
                "width_inch": 3.0, "height_inch": 2.0, "align": "center"}

    def _caption(self, index, text):
        return _paragraph(index, "table_caption" if text.startswith("表")
                          else "figure_caption", text, alignment="center")

    def _docs(self, second, first="表1-1 对比结果"):
        """两处题注：第一处编号正常，第二处按用例给。"""
        return [
            self.HEADING,
            _paragraph(1, "body", f"{first.split()[0]}和{second.split()[0]}给出了结果。",
                       first_line_indent_pt=24),
            self._caption(2, first),
            self._table(3),
            _paragraph(4, "body", "下面是补充数据。", first_line_indent_pt=24),
            self._caption(5, second),
            self._table(6),
        ]

    def test_out_of_order_table_number_is_reported(self):
        issues = RuleEngine(_rules("jlu_cs")).check(
            {"sections": [], "blocks": self._docs("表1-3 补充结果")})["issues"]
        found = [i for i in issues if i["rule_id"] == "table.caption_number"]
        self.assertEqual(1, len(found))
        self.assertEqual("表1-3", found[0]["actual"])
        self.assertEqual("表1-2", found[0]["expected"])

    def test_continuous_table_numbers_are_clean(self):
        ids = _rule_ids({"sections": [], "blocks": self._docs("表1-2 补充结果")},
                        _rules("jlu_cs"))
        self.assertNotIn("table.caption_number", ids)

    def test_out_of_order_figure_number_is_reported(self):
        blocks = [
            self.HEADING,
            _paragraph(1, "body", "图1-1和图1-4给出了结果。", first_line_indent_pt=24),
            self._image(2),
            self._caption(3, "图1-1 系统框架"),
            self._image(4),
            self._caption(5, "图1-4 流程图"),
        ]
        issues = RuleEngine(_rules("jlu_cs")).check(
            {"sections": [], "blocks": blocks})["issues"]
        found = [i for i in issues if i["rule_id"] == "figure.caption_number"]
        self.assertEqual(1, len(found))
        self.assertEqual("图1-4", found[0]["actual"])

    def test_numbering_is_checked_per_chapter(self):
        """每章重新从 1 开始编号：第 2 章的第一个表就是表 2-1。"""
        blocks = [
            self.HEADING,
            _paragraph(1, "body", "表1-1给出了结果。", first_line_indent_pt=24),
            self._caption(2, "表1-1 对比结果"),
            self._table(3),
            _paragraph(4, "heading_1", "第2章 方法"),
            _paragraph(5, "body", "表2-1给出了方法对比。", first_line_indent_pt=24),
            self._caption(6, "表2-1 方法对比"),
            self._table(7),
        ]
        ids = _rule_ids({"sections": [], "blocks": blocks}, _rules("jlu_cs"))
        self.assertNotIn("table.caption_number", ids)

    def test_another_chapter_number_is_not_treated_as_a_gap(self):
        """附录里的表常按另一套章号排（表1-1 排在表2-1 之后），不能算跳号。"""
        blocks = [
            self.HEADING,
            _paragraph(1, "body", "表2-1给出了结果。", first_line_indent_pt=24),
            self._caption(2, "表2-1 对比结果"),
            self._table(3),
            _paragraph(4, "body", "附录表1-1给出原始数据。", first_line_indent_pt=24),
            self._caption(5, "表1-1 原始数据"),
            self._table(6),
        ]
        ids = _rule_ids({"sections": [], "blocks": blocks}, _rules("jlu_cs"))
        self.assertNotIn("table.caption_number", ids)

    def test_duplicated_number_is_reported(self):
        """重号（表1-1 出现两次）等价于第二处编号不连续。"""
        issues = RuleEngine(_rules("jlu_cs")).check(
            {"sections": [], "blocks": self._docs("表1-1 补充结果")})["issues"]
        self.assertIn("table.caption_number",
                      [i["rule_id"] for i in issues if i["message"] == "表题编号不连续"])

    def test_cascade_points_to_the_first_break(self):
        """同一章后续跟着错位的题注，标“受前一处断号影响”，不逐条叫人改。"""
        blocks = [
            self.HEADING,
            _paragraph(1, "body", "见表1-1、表1-3与表1-4。", first_line_indent_pt=24),
            self._caption(2, "表1-1 对比结果"),
            self._table(3),
            self._caption(4, "表1-3 补充结果"),
            self._table(5),
            self._caption(6, "表1-4 更多结果"),
            self._table(7),
        ]
        issues = [i for i in RuleEngine(_rules("jlu_cs")).check(
            {"sections": [], "blocks": blocks})["issues"]
            if i["rule_id"] == "table.caption_number"]
        messages = {i["actual"]: i["message"] for i in issues}
        self.assertEqual("表题编号不连续", messages.get("表1-3"))
        self.assertIn("受前一处断号影响", messages.get("表1-4", ""))

    def test_placement_hint_counts_segments_from_one(self):
        """位置提示里的段号从 1 数起（原先从 0 数，比实际少 1）。"""
        issues = RuleEngine(_rules("jlu_cs")).check(
            {"sections": [], "blocks": [
                self._image(0),
                self._caption(1, "图1-1 系统框架"),
                _paragraph(2, "body", "图1-1给出了结果。", first_line_indent_pt=24),
            ]})["issues"]
        found = [i for i in issues if i["rule_id"] == "figure.placement"]
        self.assertEqual(1, len(found))
        self.assertIn("第 3 段", found[0]["actual"])

    def test_unrecognizable_number_head_is_left_to_the_format_rule(self):
        """写成“图1.1”时本条不判，由题注编号格式那条报。"""
        issues = RuleEngine(_rules("jlu_cs")).check(
            {"sections": [], "blocks": [
                self.HEADING,
                _paragraph(1, "body", "图1.1给出了结果。", first_line_indent_pt=24),
                self._image(2),
                self._caption(3, "图1.1 系统框架"),
            ]})["issues"]
        self.assertEqual([], [i for i in issues if i["message"] == "图题编号不连续"])
        self.assertIn("figure.caption_number",
                      [i["rule_id"] for i in issues if i["message"] == "图题编号格式不符合要求"])

    def test_caption_number_prefix_spacing_is_not_a_format_error(self):
        """模板只规定编号本身（“章号-半角-序列号”）：
        “图 4-1”这类“图/表”与数字之间的空白不在判定范围内（实测 5 条误报）。"""
        for text in ("图 4-1 系统框架", "图４-１ 系统框架"):
            issues = RuleEngine(_rules("jlu_cs")).check(
                {"sections": [], "blocks": [
                    self.HEADING,
                    _paragraph(1, "body", "见图4-1。", first_line_indent_pt=24),
                    self._image(2),
                    self._caption(3, text),
                ]})["issues"]
            self.assertEqual(
                [], [i for i in issues if i["message"] == "图题编号格式不符合要求"],
                text)

    def test_table_caption_prefix_cixie_is_not_a_format_error(self):
        """前缀写成“表格”不是编号错误（模板没规定前缀写法，识别层认这种写法）。"""
        issues = RuleEngine(_rules("jlu_cs")).check(
            {"sections": [], "blocks": [
                self.HEADING,
                _paragraph(1, "body", "见表3-1。", first_line_indent_pt=24),
                self._caption(2, "表格 3-1 对比结果"),
                self._table(3),
            ]})["issues"]
        self.assertEqual(
            [], [i for i in issues if i["message"] == "表题编号格式不符合要求"])

    def test_caption_number_inner_space_or_fullwidth_dash_or_dot_is_reported(self):
        """编号内部出现空白/全角短横/点号：不属于“用半角 - 隔开”，照报。"""
        for text in ("图3- 15 流程", "图3－15 流程", "图3.15 流程"):
            issues = RuleEngine(_rules("jlu_cs")).check(
                {"sections": [], "blocks": [
                    self.HEADING,
                    _paragraph(1, "body", "见图3-15。", first_line_indent_pt=24),
                    self._image(2),
                    self._caption(3, text),
                ]})["issues"]
            self.assertIn(
                "figure.caption_number",
                [i["rule_id"] for i in issues
                 if i["message"] == "图题编号格式不符合要求"],
                text)

    def test_check_is_configurable(self):
        rules = _rules("jlu_cs")
        rules["tables"].pop("check_continuity")
        ids = _rule_ids({"sections": [], "blocks": self._docs("表1-3 补充结果")}, rules)
        self.assertNotIn("table.caption_number", ids)


class ReferenceCitationOrderTest(unittest.TestCase):
    """§7.6：顺序编码制——按正文中引用出现的先后顺序连续编码。"""

    REFS = [
        _paragraph(2, "reference_title", "参考文献"),
        _paragraph(3, "reference_entry", "［1］张三．题名[J]．刊名，2020．"),
        _paragraph(4, "reference_entry", "［2］李四．题名[J]．刊名，2021．"),
    ]

    def _ids(self, body):
        blocks = [_paragraph(0, "heading_1", "第1章 绪论"),
                  _paragraph(1, "body", body)] + self.REFS
        return _rule_ids({"sections": [], "blocks": blocks}, _rules("jlu_cs"))

    def test_citation_out_of_order_is_reported(self):
        issues = RuleEngine(_rules("jlu_cs")).check({"sections": [], "blocks": [
            _paragraph(0, "heading_1", "第1章 绪论"),
            _paragraph(1, "body", "文献［2］提出了方法，文献［1］给出了证明。"),
        ] + self.REFS})["issues"]
        found = [i for i in issues if i["rule_id"] == "reference.citation_order"]
        self.assertEqual(1, len(found))
        self.assertEqual("［1］", found[0]["expected"])

    def test_citations_in_order_are_clean(self):
        ids = self._ids("文献［1］提出了方法，文献［2］给出了证明。")
        self.assertNotIn("reference.citation_order", ids)

    def test_document_without_citations_is_not_reported(self):
        self.assertNotIn("reference.citation_order", self._ids("本文研究了论文格式校验。"))

    def test_single_citation_is_not_enough_to_judge(self):
        self.assertNotIn("reference.citation_order", self._ids("文献［2］提出了方法。"))

    def test_reference_list_numbers_do_not_participate(self):
        """文献清单本身的序号不是正文引用，不能拿来判顺序。"""
        ids = self._ids("文献［1］提出了方法。")
        self.assertNotIn("reference.citation_order", ids)


class ContinuedTableCaptionTest(unittest.TestCase):
    """§5.4：续表只需“续表”标注 + 重复表头，不该再被要表题。

    官方模板改造的样张里，续表表格被报“表格上方应有表题”，且“下一段是表题就
    提前 return”把同一张表的其余检查（续表表头、单元格字体）一并吞掉。
    """

    def _table(self, index, header_repeat):
        return {"type": "table", "index": index, "header_repeat": header_repeat,
                "style": None, "align": "center",
                "cells": [{"row": 0, "col": 0, "text": "列", "font_name": "宋体",
                           "east_asia_font": "宋体", "font_size_pt": 10.5,
                           "runs": [{"text": "列", "font_name": "宋体",
                                     "east_asia_font": "宋体", "font_size_pt": 10.5}]}]}

    def _ids(self, blocks):
        return _rule_ids({"sections": [], "blocks": blocks}, _rules("jlu_cs"))

    def test_continued_table_without_repeated_header_is_reported(self):
        blocks = [
            _paragraph(0, "table_continued_note", "续表1-1", alignment="left"),
            self._table(1, False),
        ]
        self.assertIn("table.continued_note_header", self._ids(blocks))

    def test_caption_below_does_not_swallow_the_continued_header_check(self):
        """下一段是（另一张表的）表题时，本表的续表表头检查仍要跑。"""
        blocks = [
            _paragraph(0, "table_continued_note", "续表1-1", alignment="left"),
            self._table(1, False),
            _paragraph(2, "table_caption", "表1-2 另一张表", alignment="center"),
            self._table(3, True),
        ]
        ids = self._ids(blocks)
        self.assertIn("table.continued_note_header", ids)

    def test_a_table_without_any_caption_is_no_longer_reported(self):
        """模板只写“表号和表名**排于表的上方**”（排在哪侧），没有一句“每张表都必须有
        表题”——这条推断出来的检查已撤销，不许以任何形式复活。"""
        blocks = [
            _paragraph(0, "body", "下面给出数据。", first_line_indent_pt=24),
            self._table(1, True),
        ]
        self.assertEqual([], [x for x in self._ids(blocks)
                              if x.endswith("caption_missing")])


class ContinuedCaptionInCaptionTest(unittest.TestCase):
    """§5.4：续表标注要单独成行写在续表左上方；写进表题不算新表，但要指出来。"""

    def _table(self, index):
        return {"type": "table", "index": index, "header_repeat": True,
                "style": None, "align": "center",
                "cells": [{"row": 0, "col": 0, "text": "列", "font_name": "宋体",
                           "east_asia_font": "宋体", "font_size_pt": 10.5,
                           "runs": [{"text": "列", "font_name": "宋体",
                                     "east_asia_font": "宋体", "font_size_pt": 10.5}]}]}

    def _ids(self, blocks):
        return _rule_ids({"sections": [], "blocks": blocks}, _rules("jlu_cs"))

    def _numbered_blocks(self, third_caption):
        return [
            _paragraph(0, "table_caption", "表6-1 甲", alignment="center"),
            self._table(1),
            _paragraph(2, "table_caption", "表6-2 乙", alignment="center"),
            self._table(3),
            _paragraph(4, "table_caption", third_caption, alignment="center"),
            self._table(5),
            _paragraph(6, "table_caption", "表6-3 丙", alignment="center"),
            self._table(7),
        ]

    def test_marker_inside_the_table_caption_is_reported(self):
        ids = self._ids(self._numbered_blocks("表6-2 乙（续表）"))
        self.assertIn("table.continued_note_in_caption", ids)

    def test_continued_caption_neither_repeats_nor_shifts_the_numbering(self):
        """续表题注重复的是同一张表的号：不报重号，也不把后面的表跟着报错。"""
        ids = self._ids(self._numbered_blocks("表6-2 乙（续表）"))
        self.assertNotIn("table.caption_number", ids)

    def test_caption_next_to_a_standalone_marker_is_not_counted(self):
        blocks = [
            _paragraph(0, "table_caption", "表6-1 甲", alignment="center"),
            self._table(1),
            _paragraph(2, "table_continued_note", "续表", alignment="left"),
            _paragraph(3, "table_caption", "表6-1 甲", alignment="center"),
            self._table(4),
        ]
        ids = self._ids(blocks)
        self.assertNotIn("table.caption_number", ids)
        self.assertNotIn("table.continued_note_in_caption", ids)


class CaptionNumberSpaceWidthTest(unittest.TestCase):
    """§5.3/§5.5：「编号与题名之间空一个汉字位置」——够一个汉字宽才算（定：甲）。"""

    def _issues(self, text):
        block = _paragraph(0, "table_caption", text, alignment="center")
        return [i for i in RuleEngine(_rules("jlu_cs")).check(
            {"sections": [], "blocks": [block]})["issues"]
            if i["rule_id"] == "caption.number_space"]

    def test_one_halfwidth_space_is_reported(self):
        issues = self._issues("表1-1 数据")
        self.assertEqual(1, len(issues))
        self.assertIn("1 个半角空格", issues[0]["actual"])

    def test_two_halfwidth_spaces_are_accepted(self):
        self.assertEqual([], self._issues("表1-1  数据"))

    def test_one_fullwidth_space_is_accepted(self):
        self.assertEqual([], self._issues("表1-1\u3000数据"))

    def test_no_space_is_reported(self):
        issues = self._issues("表1-1数据")
        self.assertEqual(1, len(issues))
        self.assertIn("没有空格", issues[0]["actual"])


class FormulaNumberCascadeTest(unittest.TestCase):
    """§5.1：同一章后续跟着错位的公式，标“受前一处断号影响”，指向第一处断号。"""

    def _issues(self, numbers):
        blocks = [
            _paragraph(0, "heading_1", "第1章 绪论"),
            _paragraph(1, "body", "见公式（1-1）、（1-3）、（1-4）。",
                       first_line_indent_pt=24),
        ]
        for offset, number in enumerate(numbers, start=2):
            blocks.append(_paragraph(offset, "formula", f"F = m a{number}",
                                     alignment="center", formula_number=number))
        return [i for i in RuleEngine(_rules("jlu_cs")).check(
            {"sections": [], "blocks": blocks})["issues"]
            if i["rule_id"] == "formula.number_continuity"]

    def test_cascade_points_to_the_first_break(self):
        issues = self._issues(["（1-1）", "（1-3）", "（1-4）"])
        messages = {i["actual"]: i["message"] for i in issues}
        self.assertEqual("公式编号不连续，应逐个递增", messages.get("（1-3）"))
        self.assertIn("受前一处断号影响", messages.get("（1-4）", ""))


class FormulaRecognitionTest(unittest.TestCase):
    """公式编号写法差异不该让整段退回正文（否则会按正文对齐/缩进报错）。"""

    def _ids(self, blocks):
        return _rule_ids({"sections": [], "blocks": blocks}, _rules("jlu_cs"))

    @staticmethod
    def _formula(index, text, align="center"):
        return _paragraph(index, "formula", text, alignment=align)

    def test_number_with_spaces_around_dash_is_still_a_formula(self):
        ids = self._ids([self._formula(0, "k + l = m（ 4 - 3 ）")])
        self.assertIn("formula.number_format", ids)
        self.assertNotIn("body.alignment", ids)
        self.assertNotIn("body.first_line_indent", ids)

    def test_number_with_fullwidth_dash_is_reported_as_a_format_problem(self):
        ids = self._ids([self._formula(0, "k + l = m（4－3）")])
        self.assertIn("formula.number_format", ids)

    def test_strict_number_is_clean(self):
        ids = self._ids([self._formula(0, "E = m c²（4-1）")])
        self.assertNotIn("formula.number_format", ids)
        self.assertNotIn("formula.dotted_line", ids)
        self.assertNotIn("body.alignment", ids)


class FormulaDottedLineTest(unittest.TestCase):
    """§6.1：公式与编号之间不加虚线——只看编号紧邻处，公式内部的省略号照留。"""

    def _ids(self, text):
        return _rule_ids({"sections": [], "blocks": [
            _paragraph(0, "formula", text, alignment="center")]}, _rules("jlu_cs"))

    def test_dotted_line_between_formula_and_number_is_reported(self):
        self.assertIn("formula.dotted_line", self._ids("p + q = r ⋯⋯⋯（4-1）"))

    def test_ascii_dots_are_reported(self):
        self.assertIn("formula.dotted_line", self._ids("p + q = r ......（4-1）"))

    def test_math_ellipsis_inside_the_formula_is_not_a_dotted_line(self):
        """a₁+a₂+⋯+aₙ=S 里的省略号是公式内容，不是公式与编号之间的虚线。"""
        self.assertNotIn("formula.dotted_line", self._ids("a₁ + a₂ + ⋯ + aₙ = S（4-1）"))

    def test_formula_without_number_has_nothing_between_to_check(self):
        self.assertNotIn("formula.dotted_line", self._ids("p + q = r ⋯⋯⋯"))


class FormulaDottedLineFixTest(unittest.TestCase):
    """写回层去掉虚线时要与判定口径一致：只删公式与编号之间那段。"""

    def test_only_the_gap_is_stripped(self):
        from backend.parser.formula import strip_dotted_between_formula_and_number
        text = "a₁ + a₂ + ⋯ + aₙ = S⋯⋯⋯（4-1）"
        self.assertEqual("a₁ + a₂ + ⋯ + aₙ = S（4-1）",
                         strip_dotted_between_formula_and_number(text, "（4-1）"))

    def test_without_number_nothing_is_stripped(self):
        from backend.parser.formula import strip_dotted_between_formula_and_number
        text = "a₁ + a₂ + ⋯ + aₙ = S"
        self.assertEqual(text, strip_dotted_between_formula_and_number(text, None))


if __name__ == "__main__":
    unittest.main()
