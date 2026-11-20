# -*- coding: utf-8 -*-
"""2026-10-12 24 份样张审计后一批判定修复的守护测试。

对应六处修复（都指得到模板原句或 Word 实测）：
1. 摘要页重复的“中文题目”行 → kind=title（§2.1 中文题目：黑体小三号字，居中）；
   原按摘要正文判会报“摘要正文字体应为宋体”——与事实不符（6 份样张 14 条）。
2. 英文摘要页重复的“英文题目”行 → kind=en_title（§2.3 英文题目：Times New
   Roman 小三号字，居中）；原按英文摘要正文判会报 12pt。
3. 参考文献条目识别：序号由 Word 自动编号渲染（“[47]”）也算序号；
   认出一条真条目后置位 entry_seen，后面的无类型标识条目按续行处理。
4. 编号级别自带的缩进（w:lvl 的 w:pPr/w:ind）插入格式层：Word 实测
   LeftIndent=22pt、FirstLine=-22pt（悬挂）→ 序号左顶格合规；不插入会按样式链
   报“60pt 未左顶格”（5 份样张 100 条）。
5. 公式对齐：带居中制表位的 right 视为“居中书写”（WPS 公式段落的
   “居中制表位 + 右对齐”写法；24 份样张 10 份 129 条）。
6. 章号推断：一级标题全走自动编号（文本里没有“第N章”）时按已出现的一级标题
   个数定章；旧实现 +1，蒙博第 2 章公式被要求改成 (3-1)（44 条）。
"""
import json
import os
import unittest

from docx.oxml.ns import nsdecls, qn
from docx.oxml.parser import parse_xml

from backend.college_registry import path_of
from backend.detection.numbering import chapter_context, expected_formula_number
from backend.detection.rule_engine import RuleEngine
from backend.parser.font_reader import (_chain_with_numbering, _has_center_tab_stop,
                                        _indent_from_chain, _left_indent_from_chain)
from backend.parser.numbering import NumberingDefinitions
from backend.recognition.context import apply_contextual_kinds

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_DIR = os.path.join(ROOT, "config", "rules")


def _rules(college_id="jlu_cs"):
    with open(path_of(college_id, "rules", RULES_DIR), encoding="utf-8") as handle:
        return json.load(handle)


def _element(xml):
    """把一段 XML 片段（`{ns}` 占位）解析成元素。"""
    return parse_xml(xml.replace("{ns}", nsdecls("w")))


def _paragraph(kind, text, **extra):
    block = {"type": "paragraph", "kind": kind, "text": text}
    block.update(extra)
    return block


def _run(text, east_asia="宋体", latin="Times New Roman", size=12.0):
    return {"text": text, "font_name": latin, "east_asia_font": east_asia,
            "font_size_pt": size, "bold": False}


class ChapterContextFallbackTest(unittest.TestCase):
    """章号推断：全自动编号标题时不再 +1（修复 6）。"""

    @staticmethod
    def _heading(text=""):
        # 一级标题；文本为空表示编号由 Word 自动编号（还原文字不在 text 里）
        return _paragraph("heading_1", text)

    @staticmethod
    def _body(text="正文"):
        return _paragraph("body", text)

    @staticmethod
    def _formula(number):
        return _paragraph("formula", f"x=1（{number}）",
                          formula_number=f"（{number}）")

    def test_unnumbered_headings_count_chapters_by_order(self):
        blocks = [self._heading(), self._body(), self._heading(), self._body(),
                  self._formula("2-1")]
        self.assertEqual((2, 2), chapter_context(blocks, 4))
        self.assertEqual("(2-1)", expected_formula_number(blocks, 4))

    def test_first_chapter_content_is_chapter_one(self):
        blocks = [self._heading(), self._body(), self._formula("1-1")]
        self.assertEqual((1, 0), chapter_context(blocks, 2))
        self.assertEqual("(1-1)", expected_formula_number(blocks, 2))

    def test_text_numbered_headings_path_unchanged(self):
        blocks = [self._heading("第1章 绪论"), self._body(),
                  self._heading("第2章 相关技术介绍"), self._body(),
                  self._formula("2-1")]
        self.assertEqual((2, 2), chapter_context(blocks, 4))
        self.assertEqual("(2-1)", expected_formula_number(blocks, 4))

    def test_unnumbered_inline_formula_does_not_inflate_sequence(self):
        # 句内公式（无编号）也是 kind=formula，不能算进“本章已用掉几个公式号”
        blocks = [self._heading(), self._body(),
                  _paragraph("formula", "其中 w 为法向量。", formula_type="omml"),
                  self._formula("1-1")]
        self.assertEqual("(1-1)", expected_formula_number(blocks, 3))

    def test_prose_mention_with_number_does_not_inflate_sequence(self):
        # 正文句子提到编号（带 formula_number）也不是一条公式；
        # 期望编号的括号风格跟随本章已有编号（这里是全角）。
        prose = _paragraph("formula", "融合概率如公式（1-3）所示。",
                           formula_number="（1-3）")
        blocks = [self._heading(), self._body(),
                  self._formula("1-1"), prose, self._formula("1-2")]
        self.assertEqual("（1-2）", expected_formula_number(blocks, 4))

    def test_numbered_plus_extra_unnumbered_heading_still_advances(self):
        # 第1章（有编号文本）+ 一个无编号一级标题 → 章号 2（原有语义不动）
        blocks = [self._heading("第1章 绪论"), self._body(),
                  self._heading(), self._body(), self._formula("2-1")]
        self.assertEqual(2, chapter_context(blocks, 4)[0])


class ReferenceEntryRecognitionTest(unittest.TestCase):
    """参考文献条目识别：自动编号序号 + entry_seen（修复 3）。"""

    @staticmethod
    def _base():
        return [
            _paragraph("heading_1", "第1章 绪论", alignment="center"),
            _paragraph("reference_title", "参考文献", alignment="center"),
        ]

    @staticmethod
    def _entry(text, **extra):
        block = _paragraph("body", text, alignment="justify",
                           left_indent_pt=0.0, first_line_indent_pt=0.0,
                           line_spacing_multiple=1.5, space_before_pt=0.0,
                           runs=[_run(text, east_asia="宋体", size=10.5)])
        block.update(extra)
        return block

    def test_auto_numbered_entry_is_entry(self):
        # 序号由 Word 自动编号渲染（[47]），段落文字里没有序号
        blocks = self._base() + [self._entry(
            "Tan, Mingxing, et al. Mnasnet: Platform-aware neural architecture "
            "search for mobile.",
            auto_number=True, auto_number_text="[47]")]
        blocks = apply_contextual_kinds(blocks)
        self.assertEqual("reference_entry", blocks[2].get("kind"))

    def test_auto_numbered_entry_is_not_judged_as_body(self):
        blocks = self._base() + [self._entry(
            "Tan, Mingxing, et al. Mnasnet: Platform-aware neural architecture "
            "search for mobile.",
            auto_number=True, auto_number_text="[47]",
            runs=[_run("Tan, Mingxing, et al. Mnasnet: Platform-aware neural "
                       "architecture search for mobile.", east_asia="宋体",
                       latin="宋体", size=10.5)])]
        blocks = apply_contextual_kinds(blocks)
        ids = [i["rule_id"] for i in
               RuleEngine(_rules()).check({"sections": [], "blocks": blocks})["issues"]]
        self.assertNotIn("body.latin_font", ids)
        self.assertNotIn("body.font_size", ids)

    def test_markless_entry_after_typed_entry_is_continuation(self):
        # 前一条带类型标识（[J]）置位 entry_seen；后一条没有类型标识也没有序号
        blocks = self._base() + [
            self._entry("韩家炜, 孟小峰. Web挖掘研究[J]. 计算机研究与发展, 2001, "
                        "38(4): 405-410."),
            self._entry("Tan, Mingxing, et al. Mnasnet: Platform-aware neural "
                        "architecture search for mobile."),
        ]
        blocks = apply_contextual_kinds(blocks)
        self.assertEqual("reference_entry", blocks[2].get("kind"))
        self.assertEqual("reference_continuation", blocks[3].get("kind"))

    def test_markless_entry_is_not_judged_as_body(self):
        blocks = self._base() + [
            self._entry("韩家炜, 孟小峰. Web挖掘研究[J]. 计算机研究与发展, 2001, "
                        "38(4): 405-410."),
            self._entry("Berant J, Chou A, Frostig R, et al. Semantic parsing on "
                        "freebase from question-answer pairs.",
                        runs=[_run("Berant J, Chou A, Frostig R, et al. Semantic "
                                   "parsing on freebase from question-answer "
                                   "pairs.", east_asia="宋体", size=10.5)]),
        ]
        blocks = apply_contextual_kinds(blocks)
        ids = [i["rule_id"] for i in
               RuleEngine(_rules()).check({"sections": [], "blocks": blocks})["issues"]]
        self.assertNotIn("body.font_size", ids)
        self.assertNotIn("body.latin_font", ids)


class AbstractTitleLineTest(unittest.TestCase):
    """摘要页重复的题目行识别（修复 1、2）。"""

    @staticmethod
    def _zh_title_line():
        text = "论文格式规范智能问答系统的设计与实现"
        return _paragraph("body", text, alignment="center",
                          runs=[_run(text, east_asia="黑体", size=15.0)])

    def test_repeated_zh_title_is_title_not_abstract_body(self):
        blocks = [
            _paragraph("abstract_title", "摘要", alignment="center"),
            self._zh_title_line(),
            _paragraph("body", "本科毕业论文在提交前往往要经过多轮格式调整。",
                       alignment="justify", runs=[_run("本科毕业论文……")]),
        ]
        blocks = apply_contextual_kinds(blocks)
        self.assertEqual("title", blocks[1].get("kind"))
        self.assertEqual("abstract_body", blocks[2].get("kind"))

    def test_repeated_zh_title_not_reported_as_abstract_body(self):
        blocks = [
            _paragraph("abstract_title", "摘要", alignment="center"),
            self._zh_title_line(),
            _paragraph("body", "本科毕业论文在提交前往往要经过多轮格式调整。",
                       alignment="justify", runs=[_run("本科毕业论文……")]),
        ]
        blocks = apply_contextual_kinds(blocks)
        ids = [i["rule_id"] for i in
               RuleEngine(_rules()).check({"sections": [], "blocks": blocks})["issues"]]
        self.assertNotIn("abstract.body.font", ids)
        self.assertNotIn("abstract.body.font_size", ids)
        self.assertNotIn("title.font", ids)  # 黑体 15pt 居中，合规

    def test_centered_line_after_body_stays_abstract_body(self):
        # 正文一旦开始，后面的居中行不再当题目（避免把正文里的短行误认成题目）
        blocks = [
            _paragraph("abstract_title", "摘要", alignment="center"),
            _paragraph("body", "正文一句话。", alignment="justify",
                       runs=[_run("正文一句话。")]),
            self._zh_title_line(),
        ]
        blocks = apply_contextual_kinds(blocks)
        self.assertEqual("abstract_body", blocks[2].get("kind"))

    def test_repeated_en_title_is_en_title(self):
        text = ("Design and Implementation of an Intelligent Question-Answering "
                "System")
        blocks = [
            _paragraph("en_abstract_title", "Abstract", alignment="center"),
            _paragraph("body", text, alignment="center",
                       runs=[_run(text, latin="Times New Roman", size=15.0)]),
            _paragraph("body", "Before final submission, undergraduate theses "
                       "usually go through many rounds of formatting.",
                       alignment="justify",
                       runs=[_run("Before final submission……")]),
        ]
        blocks = apply_contextual_kinds(blocks)
        self.assertEqual("en_title", blocks[1].get("kind"))
        self.assertEqual("en_abstract_body", blocks[2].get("kind"))

    def test_cjk_centered_line_in_en_abstract_stays_body(self):
        blocks = [
            _paragraph("en_abstract_title", "Abstract", alignment="center"),
            _paragraph("body", "这是中文行，不是英文题目。", alignment="center",
                       runs=[_run("这是中文行，不是英文题目。", size=15.0)]),
        ]
        blocks = apply_contextual_kinds(blocks)
        self.assertEqual("en_abstract_body", blocks[1].get("kind"))


NUMBERING_XML = (
    '<w:numbering {ns}>'
    '<w:abstractNum w:abstractNumId="0">'
    '<w:lvl w:ilvl="0">'
    '<w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="[%1]"/>'
    '<w:pPr><w:ind w:left="440" w:hanging="440"/></w:pPr>'
    "</w:lvl>"
    "</w:abstractNum>"
    '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
    "</w:numbering>"
)


class NumberingLevelIndentTest(unittest.TestCase):
    """编号级别缩进插入格式层（修复 4）。"""

    def setUp(self):
        self.defs = NumberingDefinitions.from_root(_element(NUMBERING_XML))

    def test_level_ppr_is_read(self):
        ppr = self.defs.level_ppr(1, 0)
        self.assertIsNotNone(ppr)
        ind = ppr.find(qn("w:ind"))
        self.assertEqual("440", ind.get(qn("w:left")))
        self.assertEqual("440", ind.get(qn("w:hanging")))

    def test_missing_level_or_ppr_returns_none(self):
        self.assertIsNone(self.defs.level_ppr(1, 9))
        self.assertIsNone(self.defs.level_ppr(99, 0))

    def test_numbering_layer_overrides_style_chain(self):
        # 样式链给出 left=36pt + first=24pt（旧实现读成 60pt“未左顶格”），
        # 编号级别给出 left=22pt、hanging=22pt → 首行起点 = 0（左顶格）
        num_ppr = self.defs.level_ppr(1, 0)
        style_ppr = _element('<w:pPr {ns}><w:ind w:left="720" w:firstLine="480"/>'
                             "</w:pPr>")
        chain = _chain_with_numbering([style_ppr], None, num_ppr)
        self.assertAlmostEqual(22.0, _left_indent_from_chain(chain, 10.5))
        self.assertAlmostEqual(-22.0, _indent_from_chain(chain, 10.5))
        start = _left_indent_from_chain(chain, 10.5) \
            + _indent_from_chain(chain, 10.5)
        self.assertAlmostEqual(0.0, start)

    def test_direct_format_overrides_numbering_layer(self):
        own = _element('<w:pPr {ns}><w:ind w:left="0" w:firstLine="0"/></w:pPr>')
        num_ppr = self.defs.level_ppr(1, 0)
        chain = _chain_with_numbering([own], own, num_ppr)
        self.assertEqual(0.0, _left_indent_from_chain(chain, 10.5))
        self.assertEqual(0.0, _indent_from_chain(chain, 10.5))

    def test_layer_inserted_after_direct_ppr(self):
        own = _element('<w:pPr {ns}><w:rPr/></w:pPr>')
        style_ppr = _element("<w:pPr {ns}/>")
        num_ppr = self.defs.level_ppr(1, 0)
        chain = _chain_with_numbering([own, style_ppr], own, num_ppr)
        self.assertIs(chain[0], own)
        self.assertIs(chain[1], num_ppr)
        self.assertIs(chain[2], style_ppr)


class HangingFirstLineTest(unittest.TestCase):
    """悬挂与 firstLineChars=0 的三条 Word 实测口径（2026-10-12）。"""

    def test_numbering_hanging_beats_direct_zero_chars(self):
        # 张权辉条目：direct[firstLineChars=0] + 编号[left=420 hanging=420]
        # → Word 实测 Left=21.00 / First=-21.00（[3] 顶格）
        own = _element('<w:pPr {ns}><w:ind w:firstLineChars="0"/></w:pPr>')
        num = _element('<w:pPr {ns}><w:ind w:left="420" w:hanging="420"/></w:pPr>')
        chain = _chain_with_numbering([own], own, num)
        self.assertAlmostEqual(-21.0, _indent_from_chain(chain, 10.5))

    def test_same_element_hanging_beats_zero_chars(self):
        # 蒙博条目：direct[left=425 hanging=425 firstLineChars=0]
        # → Word 实测 Left=21.25 / First=-21.25
        own = _element('<w:pPr {ns}><w:ind w:left="425" w:leftChars="0" '
                       'w:hanging="425" w:firstLineChars="0"/></w:pPr>')
        chain = _chain_with_numbering([own], own, _element("<w:pPr {ns}/>"))
        self.assertAlmostEqual(-21.25, _indent_from_chain(chain, 10.5))

    def test_zero_chars_still_blocks_style_nonzero_first_line(self):
        # 显式 0 仍阻断样式链的非零首行值（历史 151 条误报的口径，不回归）
        own = _element('<w:pPr {ns}><w:ind w:firstLineChars="0"/></w:pPr>')
        style = _element('<w:pPr {ns}><w:ind w:firstLine="420" '
                         'w:firstLineChars="200"/></w:pPr>')
        self.assertEqual(0.0, _indent_from_chain([own, style], 10.5))


class CenterTabStopTest(unittest.TestCase):
    """居中制表位识别（修复 5 的解析侧）。"""

    def test_center_tab_detected(self):
        ppr = _element('<w:pPr {ns}><w:tabs>'
                       '<w:tab w:val="center" w:pos="4800"/>'
                       '<w:tab w:val="right" w:pos="10080"/>'
                       "</w:tabs></w:pPr>")
        self.assertTrue(_has_center_tab_stop([ppr]))

    def test_right_tab_only_is_not_center(self):
        ppr = _element('<w:pPr {ns}><w:tabs>'
                       '<w:tab w:val="right" w:pos="10080"/>'
                       "</w:tabs></w:pPr>")
        self.assertFalse(_has_center_tab_stop([ppr]))

    def test_center_tab_in_style_chain_found(self):
        own = _element("<w:pPr {ns}/>")
        style = _element('<w:pPr {ns}><w:tabs>'
                         '<w:tab w:val="center" w:pos="4800"/>'
                         "</w:tabs></w:pPr>")
        self.assertTrue(_has_center_tab_stop([own, style]))


class FormulaAlignmentExemptionTest(unittest.TestCase):
    """公式对齐新口径：按“视觉居中”的结果豁免（见 formula_layout_centered）。

    Word 实测量得列中心 297.6pt：'＃' 自动编号的公式起点 211.2 = 297.6 − 宽/2
    （精确居中）；OLE 宽对象（362/375pt）中点 272.7/274.8 与列中心相差 25pt 内。
    """

    RULES = {"formula": {"alignment": "center", "number_position": "end",
                         "check_continuity": False,
                         "number_pattern": r"[(（]\d+-\d+[)）]"}}

    @staticmethod
    def _formula(center_tab=False, alignment="right",
                 text="Fm(x)=Fm-1(x)+ηhm(x)（2-4）",
                 formula_text="Fm(x)=Fm-1(x)+ηhm(x)", formula_type=None):
        block = {"type": "paragraph", "index": 0, "kind": "formula",
                 "alignment": alignment,
                 "text": text,
                 "formula_number": "（2-4）",
                 "formula_text": formula_text}
        if center_tab:
            block["center_tab"] = True
        if formula_type:
            block["formula_type"] = formula_type
        return block

    def _ids(self, block, sections=None):
        document = {"blocks": [block]}
        if sections:
            document["sections"] = sections
        return [i["rule_id"] for i in
                RuleEngine(self.RULES).check(document)["issues"]]

    def test_wps_center_tab_formula_is_not_reported(self):
        # WPS 写法：居中制表位 + right → 视为“居中书写”，不再报
        self.assertNotIn("formula.alignment",
                         self._ids(self._formula(center_tab=True)))

    def test_space_padded_right_formula_is_not_reported(self):
        # 右对齐 + 行内空白装置（张心宇式 30 空格把公式推回中部）→ 放过
        block = self._formula(
            text="Fm(x)=Fm-1(x)+ηhm(x)                            （2-4）")
        self.assertNotIn("formula.alignment", self._ids(block))

    def test_tab_padded_formula_is_not_reported(self):
        # 制表位撑排（高子鸿式）→ 放过；两端对齐走同一口径
        block = self._formula(alignment="justify",
                              text="Fm(x)=Fm-1(x)+ηhm(x)\t\t（2-4）")
        self.assertNotIn("formula.alignment", self._ids(block))

    def test_hash_numbered_formula_is_not_reported(self):
        # Word '＃' 自动编号（白皓天式）：实测精确居中，left 对齐也不报
        block = self._formula(alignment="left", text="",
                              formula_text="AF=EΦF(X,Y)#2-8")
        block["formula_number"] = "（2-8）"
        self.assertNotIn("formula.alignment", self._ids(block))

    def test_ole_formula_is_not_reported(self):
        # OLE 对象行（匡瑞式）：位置由对象渲染尺寸决定 → 放过
        block = self._formula(text="（2-12）", formula_text="",
                              formula_type="ole")
        block["formula_number"] = "（2-12）"
        self.assertNotIn("formula.alignment", self._ids(block))

    # 版心 15 cm = 425.2pt（匡瑞实测参数）
    SECTIONS = [{"width_cm": 21.0, "left_cm": 3.0, "right_cm": 3.0}]

    def test_wide_ole_formula_is_not_reported(self):
        # 匡瑞式宽对象（375pt/425pt=0.88）：对象+编号铺满行宽，实测中点≈列中心
        block = self._formula(text="（2-12）", formula_text="",
                              formula_type="ole")
        block["formula_number"] = "（2-12）"
        block["formula_xml"] = ['<v:shape style="width:375.25pt;height:70pt"/>']
        self.assertNotIn("formula.alignment",
                         self._ids(block, self.SECTIONS))

    def test_narrow_ole_formula_without_device_is_reported(self):
        # 窄对象（110pt/425pt=0.26）右对齐且无装置：几何上贴右缘，不是居中
        block = self._formula(text="（2-1）", formula_text="",
                              formula_type="ole")
        block["formula_number"] = "（2-1）"
        block["formula_xml"] = ['<v:shape style="width:110.05pt;height:40pt"/>']
        self.assertIn("formula.alignment", self._ids(block, self.SECTIONS))

    def test_ole_without_width_info_stays_exempt(self):
        # 宽度/版心取不到 → 判不了不报，维持放过
        block = self._formula(text="（2-12）", formula_text="",
                              formula_type="ole")
        block["formula_number"] = "（2-12）"
        self.assertNotIn("formula.alignment", self._ids(block, self.SECTIONS))
        self.assertNotIn("formula.alignment", self._ids(block))

    def test_bare_text_right_formula_is_reported(self):
        # 纯文字公式、右对齐、无任何装置 → 仍是显式贴边，报“未居中”
        block = self._formula(formula_type="text")
        self.assertIn("formula.alignment", self._ids(block))

    def test_bare_text_left_formula_is_reported(self):
        # 左对齐、无装置 → 报
        block = self._formula(alignment="left", formula_type="text")
        self.assertIn("formula.alignment", self._ids(block))

    def test_bare_text_justify_formula_is_reported(self):
        # 两端对齐、无装置 → 报
        block = self._formula(alignment="justify", formula_type="text")
        self.assertIn("formula.alignment", self._ids(block))


class ContinuedHeaderResultTest(unittest.TestCase):
    """§5.4 续表表头：判“表头重复排出”的结果（不卡实现手段）。"""

    @staticmethod
    def _table(index, header, n_cols=None):
        n_cols = n_cols or len(header)
        cells = []
        for col, text in enumerate(header):
            cells.append({"row": 0, "col": col, "text": text,
                          "east_asia_font": "宋体", "font_size_pt": 10.5,
                          "runs": [{"text": text, "east_asia_font": "宋体",
                                    "font_name": "Times New Roman",
                                    "font_size_pt": 10.5}]})
        cells.append({"row": 1, "col": 0, "text": "数据行",
                      "east_asia_font": "宋体", "font_size_pt": 10.5,
                      "runs": [{"text": "数据行", "east_asia_font": "宋体",
                                "font_name": "Times New Roman",
                                "font_size_pt": 10.5}]})
        return {"type": "table", "index": index, "cells": cells,
                "n_rows": 2, "n_cols": n_cols, "header_repeat": False}

    @staticmethod
    def _note(index):
        return {"type": "paragraph", "index": index,
                "kind": "table_continued_note", "text": "续表3-1 示例表"}

    def _ids(self, blocks):
        return [i["rule_id"] for i in
                RuleEngine(_rules()).check({"sections": [], "blocks": blocks})["issues"]]

    def test_copied_header_row_is_accepted(self):
        # 张心宇式：续表首行 = 上表表头（含空格差异的归一）
        blocks = [self._table(0, ["列名", "数据类型", "Default", "是否允许为空"]),
                  self._note(1),
                  self._table(2, ["列名", "数据类型", "Default", "是否允许 为空"])]
        self.assertNotIn("table.continued_note_header", self._ids(blocks))

    def test_data_first_row_is_still_reported(self):
        # 钟浩林式：源表是 ≥3 列表头带，续表首行直接从数据开始
        blocks = [self._table(0, ["演员属性类型", "含义描述", "示例"]),
                  self._note(1),
                  self._table(2, ["职业", "演员的所有职业", "演员 / 作者 / 音乐人"])]
        self.assertIn("table.continued_note_header", self._ids(blocks))

    def test_key_value_two_column_table_is_accepted(self):
        # 高绪哲式：两列"字段｜值"用例表，没有可重复的列表头带
        blocks = [self._table(0, ["用例名称", "老人/家属/员工信息管理"], n_cols=2),
                  self._note(1),
                  self._table(2, ["前置条件", "管理员已登录系统"], n_cols=2)]
        self.assertNotIn("table.continued_note_header", self._ids(blocks))

    def test_header_repeat_property_is_still_accepted(self):
        table = self._table(2, ["列名", "数据类型"])
        table["header_repeat"] = True
        blocks = [self._table(0, ["列名", "数据类型"]), self._note(1), table]
        self.assertNotIn("table.continued_note_header", self._ids(blocks))


class FigureCaptionSideTest(unittest.TestCase):
    """§5.3 图题位置：判方向（题注排在图上方才是可证实的错）。"""

    @staticmethod
    def _image(index, inline=None):
        block = {"type": "image", "index": index}
        if inline is not None:
            block["inline"] = inline
        return block

    @staticmethod
    def _caption(index, text="图3-1 示例图"):
        return {"type": "paragraph", "index": index, "kind": "figure_caption",
                "text": text, "alignment": "center",
                "first_line_indent_pt": 0.0, "left_indent_pt": 0.0,
                "runs": [{"text": text, "font_name": "黑体",
                          "east_asia_font": "黑体", "font_size_pt": 10.5}]}

    @staticmethod
    def _text(index, text="正文描述。"):
        return {"type": "paragraph", "index": index, "kind": "body", "text": text,
                "alignment": "justify", "first_line_indent_pt": 24.0,
                "runs": [{"text": text, "font_name": "Times New Roman",
                          "east_asia_font": "宋体", "font_size_pt": 12.0}]}

    def _ids(self, blocks):
        return [i["rule_id"] for i in
                RuleEngine(_rules()).check({"sections": [], "blocks": blocks})["issues"]]

    def test_caption_below_image_is_accepted(self):
        blocks = [self._image(0), self._caption(1)]
        self.assertNotIn("figure.caption_position", self._ids(blocks))

    def test_caption_above_image_is_reported(self):
        # 蒙博式：题注在 image 之前
        blocks = [self._text(0), self._caption(1), self._image(2)]
        self.assertIn("figure.caption_position", self._ids(blocks))

    def test_sublabel_row_between_is_accepted(self):
        # 樊静文/高绪哲式：图下面隔着子标注行再排图题
        blocks = [self._image(0), self._image(1),
                  self._text(2, "a) 左图                       b) 右图"),
                  self._caption(3)]
        self.assertNotIn("figure.caption_position", self._ids(blocks))

    def test_inline_image_above_counts_as_figure(self):
        # 王福全/杨明旭式：图是行内图（inline），下面隔着图内文字块排图题
        blocks = [self._image(0, inline=True), self._text(1, "ACC"), self._caption(2)]
        self.assertNotIn("figure.caption_position", self._ids(blocks))

    def test_caption_with_figure_only_in_table_is_not_reported(self):
        # 李培金式：截图拼在小表格里，图题直接排在表下；无从证实方向则不报
        table = {"type": "table", "index": 0,
                 "cells": [{"row": 0, "col": 0, "text": "", "runs": []},
                           {"row": 0, "col": 1, "text": "", "runs": []}],
                 "n_rows": 1, "n_cols": 2, "header_repeat": False}
        blocks = [table, self._caption(1)]
        self.assertNotIn("figure.caption_position", self._ids(blocks))

    def test_image_three_blocks_above_is_accepted(self):
        # 后向窗口 3 块覆盖“隔几行再排图题”的排版（实测 437 条里 7 条隔 2~3 块）
        blocks = [self._image(0), self._text(1, "填充一"), self._text(2, "填充二"),
                  self._caption(3)]
        self.assertNotIn("figure.caption_position", self._ids(blocks))

    def test_neighbor_figures_image_is_not_mistaken_as_ours(self):
        # 李培金式：图4-7 下方第 3 块是图4-8 的图（图后紧跟图4-8 题注）——
        # 前向窗口只有 2 块，就是因为这种“邻居的图”不能当成“题注在图上方”的证据
        blocks = [self._text(0, "前文"),
                  self._caption(1, "图4-7 答题结果反馈界面"),
                  self._text(2, "说明文字一"), self._text(3, "说明文字二"),
                  self._image(4), self._caption(5, "图4-8 本次答题情况")]
        self.assertNotIn("figure.caption_position", self._ids(blocks))


class CitationOrderParsingTest(unittest.TestCase):
    """引用顺序新口径：区间引用（[1-2]）要展开；图注/公式段/表格里的引用算正文。

    证据：张权辉 [18-19] 被漏解析 → 报“第 18 处是［20］”；钟浩林 [20] 在图注、
    程昊男 [23] 在公式段说明文字里，都被“只扫正文类块”漏掉 → 假“缺号”。
    同时逗号列举（[3, 768, 768]、[0，1]）不得被当成引用。
    """

    REFS = [
        _paragraph("reference_title", "参考文献"),
        _paragraph("reference_entry", "［1］张三．题名[J]．刊名，2020．"),
        _paragraph("reference_entry", "［2］李四．题名[J]．刊名，2021．"),
        _paragraph("reference_entry", "［3］王五．题名[J]．刊名，2022．"),
    ]

    def _ids(self, blocks):
        engine = RuleEngine(_rules())
        return [i["rule_id"] for i in
                engine.check({"sections": [], "blocks": blocks + self.REFS})["issues"]]

    def test_range_citation_expands_numbers(self):
        # [1-2] 展开后 1、2 都在：后面接 [3] 不构成乱序（张权辉/崔镭明/王福全式）
        blocks = [_paragraph("body", "文献[1-2]提出了方法，文献[3]给出证明。")]
        self.assertNotIn("reference.citation_order", self._ids(blocks))

    def test_fullwidth_and_dash_variants(self):
        blocks = [_paragraph("body", "文献［1–2］提出了方法，文献［3］给出证明。")]
        self.assertNotIn("reference.citation_order", self._ids(blocks))

    def test_figure_caption_citation_participates(self):
        # 钟浩林式：[2] 首次出现在图注
        blocks = [
            _paragraph("body", "文献[1]提出了方法。"),
            _paragraph("figure_caption", "图2-1 构建流程[2]"),
            _paragraph("body", "文献[3]给出了证明。"),
        ]
        self.assertNotIn("reference.citation_order", self._ids(blocks))

    def test_formula_paragraph_citation_participates(self):
        # 程昊男式：[2] 在公式段落的说明文字里
        blocks = [
            _paragraph("body", "文献[1]提出了方法。"),
            _paragraph("formula", "如公式(2-1)所示[2]："),
            _paragraph("body", "文献[3]给出了证明。"),
        ]
        self.assertNotIn("reference.citation_order", self._ids(blocks))

    def test_table_cell_citation_participates(self):
        table = {"type": "table", "kind": "table", "index": 1,
                 "cells": [{"row": 0, "col": 0, "text": "对比[2]"}],
                 "n_rows": 1, "n_cols": 1}
        blocks = [
            _paragraph("body", "文献[1]提出了方法。"),
            table,
            _paragraph("body", "文献[3]给出了证明。"),
        ]
        self.assertNotIn("reference.citation_order", self._ids(blocks))

    def test_comma_lists_are_not_citations(self):
        # [3, 768, 768]（图片尺寸）与 [0，1]（区间）不参与，序列仍须干净
        blocks = [_paragraph(
            "body", "文献[1]给出了方法。图像尺寸为[3, 768, 768]。"
                    "取值区间[0，1]。文献[2]做了验证。")]
        self.assertNotIn("reference.citation_order", self._ids(blocks))

    def test_reversed_range_is_ignored(self):
        # [3-1] 倒序不展开，不产生幽灵号
        blocks = [_paragraph("body", "文献[1]给出了方法。范围[3-1]无意义。文献[2]做了验证。")]
        self.assertNotIn("reference.citation_order", self._ids(blocks))


if __name__ == "__main__":
    unittest.main()
