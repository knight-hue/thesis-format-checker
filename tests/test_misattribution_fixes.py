# -*- coding: utf-8 -*-
"""把"报错归因错"的五处修正钉住。

这五处的共同点：模板里的要求句没错，是我们的**判据打错了对象**——
封面日期被当中文题目、正文里的参考文献条目被当正文、字体别名没归一……
每一条都在 24 份真实样张上有实测条数（见各处注释），这里用最小输入复现。
"""
import io
import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

import docx
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from backend.recognition.classification import classify_kind
from backend.recognition.context import apply_contextual_kinds
from backend.parser.docx_to_json import docx_to_json
from backend.parser.font_reader import canonical_font_name, read_font_aliases
from backend.recognition.structure import DEFAULT_STRUCTURE, StructureSpec
from backend.detection.rule_engine import RuleEngine
from backend.detection.rules.formula import is_standalone_formula
from backend.detection.semantic.deterministic import (check_acknowledgement,
                                              check_list_numbering, check_numerals)
from backend.detection.semantic.rules import SEMANTIC_RULES

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
FONT_REL = ("http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
            "fontTable")
FONT_TABLE_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    f'<w:fonts xmlns:w="{W_NS}">'
    '<w:font w:name="宋体(中文)"><w:altName w:val="宋体"/></w:font>'
    '<w:font w:name="Helvetica"><w:altName w:val="Arial"/></w:font>'
    "</w:fonts>"
)


def _paragraph(index, text, *, align="justify", cjk="宋体", size=12, kind=None):
    return {
        "type": "paragraph", "index": index,
        "kind": kind if kind is not None else classify_kind(text),
        "text": text, "alignment": align,
        "runs": [{"text": text, "font_name": "Times New Roman",
                  "east_asia_font": cjk, "font_size_pt": size, "bold": False}],
    }


def _inject_font_table(path):
    """给 python-docx 生成的文档补一个字体表（它自己不生成这个部件）。"""
    with ZipFile(path) as package:
        parts = {name: package.read(name) for name in package.namelist()}

    rels = ET.fromstring(parts["word/_rels/document.xml.rels"])
    rel = ET.SubElement(rels, f"{{{REL_NS}}}Relationship")
    rel.set("Id", "rIdFontTable")
    rel.set("Type", FONT_REL)
    rel.set("Target", "fontTable.xml")
    parts["word/_rels/document.xml.rels"] = ET.tostring(
        rels, encoding="UTF-8", xml_declaration=True)

    content_types = ET.fromstring(parts["[Content_Types].xml"])
    override = ET.SubElement(content_types, f"{{{CT_NS}}}Override")
    override.set("PartName", "/word/fontTable.xml")
    override.set("ContentType",
                 "application/vnd.openxmlformats-officedocument.wordprocessingml."
                 "fontTable+xml")
    parts["[Content_Types].xml"] = ET.tostring(
        content_types, encoding="UTF-8", xml_declaration=True)

    parts["word/fontTable.xml"] = FONT_TABLE_XML.encode("utf-8")
    with ZipFile(path, "w") as package:
        for name, blob in parts.items():
            package.writestr(name, blob)


class CoverDateTest(unittest.TestCase):
    """① 封面日期不许被当成"中文题目"（实测 24 份样张里 22 份中招，共 99 条）。

    模板原句是"论文题目（黑体小三号字、居中）"，问题不在要求，在于我们把封面
    日期行当成了题目，而真正的题目那一行反倒从不检查。
    """

    def test_centered_cover_date_is_not_taken_as_title(self):
        """日期行先出现时，题目位要留给真正的题目（实测孟思远那份就是这样）。"""
        blocks = [
            _paragraph(0, "2022年04月", align="center", cjk="宋体", size=16),
            _paragraph(1, "基于机器学习的癌症检测的实现", align="center",
                       cjk="黑体", size=15),
        ]
        apply_contextual_kinds(blocks)
        self.assertEqual("title", blocks[1]["kind"])
        self.assertNotEqual("title", blocks[0]["kind"])

    def test_date_forms(self):
        spec = StructureSpec(DEFAULT_STRUCTURE)
        for text in ("2022年04月", "2026年4月26日", "2023年 5 月"):
            blocks = [
                _paragraph(0, "本科生毕业论文（设计）", align="center",
                           cjk="黑体", size=26),
                _paragraph(1, text, align="center", cjk="宋体", size=16),
            ]
            apply_contextual_kinds(blocks)
            self.assertNotEqual("title", blocks[1]["kind"], text)
        self.assertTrue(spec is not None)


class TrailingAnchorTest(unittest.TestCase):
    """③ 段落末尾粘着的章节锚点要能切换区域（实测 1 份样张因此错报 131 条）。

    学生少打一次回车，把"参考文献"标题接在了上一段末尾。切不过去的话，后面整片
    参考文献都按正文查宋体小四（条目本来是五号），条条都报"字号不符合要求"。
    """

    def test_trailing_anchor_switches_region(self):
        blocks = [
            _paragraph(0, "第1章 绪论", align="center", cjk="黑体", size=16),
            _paragraph(1, "本文围绕论文格式校验展开研究。"),
            _paragraph(2, "即使这篇论文实现了核心功能。                    参考文献"),
            _paragraph(3, "[1] 陈婷．高职校教务管理系统的现状研究[J]．信息系统工程，"
                          "2011，(2)：106-107．", size=10.5),
            _paragraph(4, "[2] 王芸．民办院校教务管理分析与设计[J]．福建电脑，"
                          "2010，(6)：134．", size=10.5),
        ]
        apply_contextual_kinds(blocks)
        self.assertEqual("body", blocks[2]["kind"])
        self.assertEqual("reference_entry", blocks[3]["kind"])
        self.assertEqual("reference_entry", blocks[4]["kind"])

    def test_single_space_is_not_enough(self):
        """只有一个空格不算"独立写成一段"，避免正文里的"参考文献"字样触发切换。"""
        blocks = [
            _paragraph(0, "第1章 绪论", align="center", cjk="黑体", size=16),
            _paragraph(1, "详见参考文献 [1] 的说明。"),
        ]
        apply_contextual_kinds(blocks)
        self.assertEqual("body", blocks[1]["kind"])


class CaptionPrefixTest(unittest.TestCase):
    """④ "表格 3.1…""图表 2-1…"要认成题注（实测 2 处被当正文）。"""

    def test_common_caption_writings(self):
        self.assertEqual("table_caption", classify_kind("表格 3.1 图像分割训练环境"))
        self.assertEqual("table_caption", classify_kind("图表2-1 系统架构"))
        self.assertEqual("table_caption", classify_kind("表3-1 数据特征"))
        self.assertEqual("figure_caption", classify_kind("图3-1 系统架构"))

    def test_prose_starting_with_the_same_word_stays_body(self):
        self.assertEqual("body", classify_kind("表格中的数据表明该系统性能更优。"))
        self.assertEqual("body", classify_kind("图表说明如下：该系统由三个模块组成。"))


class AbstractAnchorTest(unittest.TestCase):
    """⑤ "中文摘要"是"摘要"的常见写法（与"关键字/关键词"同类），要认成摘要标题。"""

    def test_chinese_abstract_anchor(self):
        self.assertEqual("abstract_title", classify_kind("中文摘要"))
        self.assertEqual("abstract_title", classify_kind("摘要"))


class ContinuedNoteTest(unittest.TestCase):
    """⑤ "注明'续表'"的几种写法都要认（实测 1 处被当正文）。"""

    def test_continued_note_writings(self):
        for text in ("续表", "（续表）", "(续表)", "续表 3-1 数据特征"):
            self.assertEqual("table_continued_note", classify_kind(text), text)


class DerivedStyleTest(unittest.TestCase):
    """⑤ WPS 派生样式名：只在正文没有编号时兜底，有编号时以编号为准。

    派生名（"样式 标题 2 + 宋体 小四 行距: 多倍行距 1.3 字行"）只说明"照哪个样式
    改的"。实测有文档把三级标题（"4.2.6 …"）排在这种样式上，若拿名字定级会被抬成
    二级并报出与事实不符的字号错（孙天羽 32 条、孙晟 4 条）。
    """

    DERIVED = "样式 标题 2 + 宋体 小四 行距: 多倍行距 1.3 字行"

    def test_used_as_fallback_when_no_numbering(self):
        self.assertEqual("heading_2", classify_kind("机器学习基础",
                                                    style_name=self.DERIVED))

    def test_numbering_wins_over_derived_style(self):
        self.assertEqual("heading_3", classify_kind("4.2.6 随机梯度下降模型",
                                                    style_name=self.DERIVED,
                                                    outline_lvl=2))
        self.assertEqual("heading_3", classify_kind("3.2.1数据爬取功能的需求分析",
                                                    style_name=self.DERIVED))

    def test_plain_style_name_still_wins(self):
        self.assertEqual("heading_2", classify_kind("机器学习基础",
                                                    style_name="标题 2"))


class OutlineBodyLevelTest(unittest.TestCase):
    """⑤ w:outlineLvl=9 是 OOXML 的"正文级别"：套着 Heading 样式也不是标题。

    实测有草稿把成片正文套了 Heading 3 样式且带这个值，只看样式名会把正文
    当三级标题漏检。
    """

    def test_outline_body_level_is_not_a_heading(self):
        self.assertEqual("body", classify_kind("本文围绕论文格式校验展开研究。",
                                                style_name="Heading 3",
                                                outline_lvl=9))
        self.assertEqual("heading_3", classify_kind("本文围绕论文格式校验展开研究。",
                                                    style_name="Heading 3"))


class FontAliasTest(unittest.TestCase):
    """② 只归一"带括注、且文档自己声明它等于括注前那个字体"的名字。

    实测 1 份样张 78 条：文档字体表里写着
    `<w:font w:name="宋体(中文)"><w:altName w:val="宋体"/>`，
    本机又没装这个名字的字体；不归一就报"正文字体不是宋体"。
    反过来，"Helvetica→Arial""方正楷体_GB2312→宋体"是**替换建议**，
    两者是不同的字体，照单归一等于把真错藏掉。
    """

    ALIASES = {
        "宋体(中文)": "宋体",
        "Helvetica": "Arial",
        "楷体_GB2312": "楷体",
        "Times New Roman (标题 CS)": "宋体",
    }

    def test_qualifier_alias_is_normalized(self):
        self.assertEqual("宋体", canonical_font_name("宋体(中文)", self.ALIASES))

    def test_substitution_hints_are_not_normalized(self):
        for name in ("Helvetica", "楷体_GB2312", "Times New Roman (标题 CS)"):
            self.assertEqual(name, canonical_font_name(name, self.ALIASES), name)

    def test_without_alias_table_nothing_changes(self):
        self.assertEqual("宋体(中文)", canonical_font_name("宋体(中文)", None))
        self.assertEqual("宋体", canonical_font_name("宋体", self.ALIASES))

    def test_font_table_is_read_and_applied_to_runs(self):
        temp = tempfile.TemporaryDirectory()
        try:
            source = Path(temp.name) / "alias.docx"
            document = docx.Document()
            aliased = document.add_paragraph().add_run("这是别名写法的中文")
            aliased.font.name = "宋体(中文)"
            r_pr = aliased._element.get_or_add_rPr()
            r_fonts = r_pr.find(qn("w:rFonts"))
            if r_fonts is None:
                r_fonts = OxmlElement("w:rFonts")
                r_pr.append(r_fonts)
            r_fonts.set(qn("w:eastAsia"), "宋体(中文)")
            hinted = document.add_paragraph().add_run("Helvetica hint")
            hinted.font.name = "Helvetica"
            document.save(str(source))
            _inject_font_table(source)

            reopened = docx.Document(str(source))
            self.assertEqual({"宋体(中文)": "宋体", "Helvetica": "Arial"},
                             read_font_aliases(reopened))
            outdir = Path(temp.name) / "parsed"
            json_path = docx_to_json(str(source), str(outdir))
            with io.open(json_path, encoding="utf-8") as handle:
                blocks = json.load(handle)["blocks"]
            fonts = {}
            for block in blocks:
                for run in block.get("runs") or []:
                    fonts[(run.get("text") or "").strip()] = (run.get("font_name"),
                                                              run.get("east_asia_font"))
            self.assertEqual(("宋体", "宋体"), fonts["这是别名写法的中文"])
            # 西文槽仍是 Helvetica（替换建议 Arial 不许生效），中文字体走文档默认
            self.assertEqual("Helvetica", fonts["Helvetica hint"][0])
        finally:
            temp.cleanup()


class SemanticRuleTargetingTest(unittest.TestCase):
    """语义规则也不许打错对象：术语不是"测量统计数据"，文献卷期不是"列表序号"。

    两条模板原句都很窄——"毕业设计（论文）中的**测量统计数据**一律用阿拉伯数字"、
    "对分项中的小项采用的序号或数字加括号，括号后不再加其他标点"。实测 24 份样张里
    "二元分类/三元组/千亿稀疏特征"被当成钱数 25 条，"1(10), e2."（卷期号）、
    "34(1):277"（卷期:页码）、"_.gt(90)。"（函数实参）被当成列表序号 3 条。
    """

    NUMERALS = next(r for r in SEMANTIC_RULES if r["rule_id"] == "numerals")
    LIST = next(r for r in SEMANTIC_RULES if r["rule_id"] == "list.numbering")

    def _numerals(self, text):
        return [i["conclusion"] for i in check_numerals(
            self.NUMERALS, {"body": [{"index": 0, "text": text}]})]

    def _list(self, text):
        return [i["conclusion"] for i in check_list_numbering(
            self.LIST, {"body": [{"index": 0, "text": text}]})]

    def test_terms_are_not_measurement_data(self):
        for text in ("以描述二元分类问题。", "通常使用 Siamese 或三元组网络架构。",
                     "可以实现千亿稀疏特征、万亿参数、数百节点并行训练。",
                     "用于基于决策树的分类器和其他二元分类器的推广。"):
            with self.subTest(text=text[:12]):
                self.assertEqual([], self._numerals(text), text)

    def test_real_measurements_are_still_reported(self):
        for text in ("其航运货运量约为十亿吨。", "项目总投入达到五亿元。",
                     "其中平时成绩占百分之三十。", "线缆长度约三米。"):
            with self.subTest(text=text[:12]):
                self.assertTrue(self._numerals(text), text)

    def test_reference_and_code_are_not_list_numbers(self):
        for text in ("t-SNE Effectively. Distill, 1(10), e2.",
                     "知识图谱嵌入技术研究综述.软件学报,2023,34(1):277-311.",
                     "最终可使用简化的_.gt(90)。"):
            with self.subTest(text=text[:16]):
                self.assertEqual([], self._list(text), text)

    def test_real_list_markers_are_still_reported(self):
        for text in ("总结就是：1、扩大硬件计算集群；2、增加参数。",
                     "（1）、第一项内容。", "（2），第二项内容。"):
            with self.subTest(text=text[:12]):
                self.assertTrue(self._list(text), text)

    def test_time_range_needs_real_dates(self):
        """模板"请不要书写带有时间段的文字"指"从2020年3月到2021年5月"这种具体区间。

        不限数字的话，"…常常让我感到恍惚，四年的时间…"会被跨词匹配（"感**到**"+"**四年**"），
        实测 1 条这样的误报。
        """
        rule = next(r for r in SEMANTIC_RULES
                    if r["rule_id"] == "acknowledgement.content")

        def conclusions(text):
            return [i["conclusion"] for i in check_acknowledgement(
                rule, {"acknowledgement_body": [{"index": 0, "text": text}]})]

        self.assertEqual([], conclusions(
            "日夜的循环常常让我感到恍惚，四年的时间如湍流般向我面前涌来。"))
        self.assertTrue(conclusions("从2020年3月到2021年5月，我在吉林大学度过了四年。"))


class StandaloneFormulaTest(unittest.TestCase):
    """公式的"居中""编号放在行末"只对**独立成行**的公式成立。

    含 OMML/OLE 公式对象的**句子**也会被解析成公式段，于是被按显示公式量：实测 24 份
    样张里这样报出 71 条与事实不符的错（"公式应居中书写" 65 条、"编号置于行末" 3 条、
    编号不连续 3 条）。模板原句是"公式应居中书写，公式的编号用圆括号括起放在公式右边
    行末"——说的是排成独立一行的公式。
    """

    PROSE = {
        "text": "逻辑回归用一组权重w=[w1，w2，w3]以及偏置b来得到最终的融合概率"
                "如公式（3-5）所示。",
        "formula_number": "（3-5）",
        "formula_text": "w1，w2，w3",
    }
    DISPLAY = {
        # 裸行：没有制表位/空白等居中装置，右/两端对齐即显式贴边 → 应报未居中。
        "text": "Fm(x)=Fm-1(x)+ηhm(x)（2-4）",
        "formula_number": "（2-4）",
        "formula_text": "Fm(x)=Fm-1(x)+ηhm(x)",
    }
    LATIN_LABEL = {
        "text": "Precision                       (3-7)",
        "formula_number": "(3-7)",
        "formula_text": "",
    }

    def test_prose_with_inline_formula_is_not_a_display_formula(self):
        self.assertFalse(is_standalone_formula(self.PROSE))

    def test_formula_with_only_its_number_is_a_display_formula(self):
        self.assertTrue(is_standalone_formula(self.DISPLAY))

    def test_latin_labelled_formula_counts_words_not_letters(self):
        """中文按字、西文按词："Precision (3-7)" 只有 1 个词，仍算独立公式。"""
        self.assertTrue(is_standalone_formula(self.LATIN_LABEL))

    def test_alignment_is_reported_only_for_the_display_formula(self):
        rules = {"formula": {"alignment": "center", "number_position": "end",
                             "check_continuity": False,
                             "number_pattern": r"[(（]\d+-\d+[)）]"}}
        blocks = [
            {"type": "paragraph", "index": 0, "kind": "formula",
             "alignment": "justify", **self.PROSE},
            {"type": "paragraph", "index": 1, "kind": "formula",
             "alignment": "justify", **self.DISPLAY},
        ]
        ids = [i["rule_id"] for i in RuleEngine(rules).check({"blocks": blocks})["issues"]]
        self.assertEqual(["formula.alignment"], ids)
        self.assertEqual(1, len(ids))


class ListNumberingTest(unittest.TestCase):
    """列表序号：模板给的总项序号是"1．、2．"，没有 0 开头的序号。

    实测 2 条 `list.numbering` 里 1 条是 `0、1、Accuracy、Macro avg、weighted avg`——
    那是二分类的类别 + 指标名（图例说明），不是列表序号；另一条 `1、扩大硬件计算集群`
    是学生真把"1．"写成了顿号，必须保留。
    """

    LIST = next(r for r in SEMANTIC_RULES if r["rule_id"] == "list.numbering")

    def _list(self, text):
        return [i["conclusion"] for i in check_list_numbering(
            self.LIST, {"body": [{"index": 0, "text": text}]})]

    def test_zero_led_line_is_not_a_list_number(self):
        text = ("分别代表精确率、召回率。0、1、Accuracy、Macro avg、weighted avg"
                "分别代表为二分类中的不同类、准确率、宏平均、加权平均。")
        self.assertEqual([], self._list(text))

    def test_plain_number_with_comma_is_still_reported(self):
        text = "提升模型能力的方法大致分为两类。1、扩大硬件计算集群；2、增加参数规模。"
        self.assertTrue(self._list(text))


class ChapterNumberFormTest(unittest.TestCase):
    """章号只能用阿拉伯数字（用户澄清：模板写的是"第1章"）。

    "第一章"（中文数字）必须报出来；识别层仍照收中文数字，这样报的是
    "一级标题编号不符合"，而不是把它降成正文去量正文字号（错误归因）。
    """

    RULES = {"headings": {"heading_1": {"pattern": r"^第\d+章"}}}

    def _number_issue(self, text):
        blocks = [{"type": "paragraph", "index": 0, "kind": "heading_1", "text": text,
                   "alignment": "center"}]
        return [i["rule_id"] for i in
                RuleEngine(self.RULES).check({"blocks": blocks})["issues"]]

    def test_arabic_number_passes(self):
        self.assertEqual([], self._number_issue("第1章 绪论"))

    def test_chinese_number_is_reported(self):
        self.assertEqual(["heading.heading_1.number"], self._number_issue("第一章 绪论"))

    def test_chinese_number_is_still_a_heading(self):
        """识别层不收紧：不然"第一章"会掉成正文，报的错就不是编号问题了。"""
        self.assertEqual("heading_1", classify_kind("第一章 绪论"))


class OutlineOnlyHeadingTest(unittest.TestCase):
    """级别只来自 w:outlineLvl（样式名不是标题样式）时，长句/著录条目不算标题。

    实测有样张给参考文献条目套了 List Paragraph + outlineLvl=0，17 段条目被当一级
    标题查编号/字体/字号（58 条）。真标题走样式名判据，不受影响。
    """

    def test_long_citation_with_outline_level_is_not_a_heading(self):
        text = ("孔令军,王茜雯,包云超,等.基于深度学习的医疗图像分割综述[J]."
                "无线电通信技术,2021,47(06):660-671.")
        self.assertEqual("body", classify_kind(text, style_name="List Paragraph",
                                               outline_lvl=0))

    def test_sentence_with_outline_level_is_not_a_heading(self):
        self.assertEqual("body", classify_kind("本节给出的方法可以用于分割。",
                                               outline_lvl=0))

    def test_short_title_with_outline_level_is_still_a_heading(self):
        self.assertEqual("heading_1", classify_kind("绪论", outline_lvl=0))

    def test_heading_style_wins_regardless_of_length(self):
        """样式名自己是标题样式时，长度/标点兜底不生效（真标题照旧认）。"""
        self.assertEqual("heading_1", classify_kind("基于知识图谱的中学生物问答系统"
                                                    "的设计与实现的总体说明。",
                                                    style_name="标题 1"))


class SentenceStartingWithNumberTest(unittest.TestCase):
    """句首是数字+空格（年号、数值+单位）的正文句不是标题。

    实测"2017 Goole[12]推出了TensorFlow Lite，…""50 Hz工频干扰是电源电磁耦合引入的
    窄带噪声。…"两段被当成一级标题，连带 35 条编号/字体/字号错。数字后跟空格原先
    直接豁免兜底，于是句首的年号和数值都成了"章号"。
    """

    def test_year_led_sentence_is_body(self):
        text = "2017 Goole[12]推出了TensorFlow Lite，它是专门为移动设备优化的版本。"
        self.assertEqual("body", classify_kind(text))

    def test_number_unit_led_sentence_is_body(self):
        text = ("50 Hz工频干扰是电源电磁耦合引入的窄带噪声。本系统采用二阶IIR"
                "滤波器将其滤除。")
        self.assertEqual("body", classify_kind(text))

    def test_short_space_separated_heading_survives(self):
        self.assertEqual("heading_1", classify_kind("1 引言"))
        self.assertEqual("heading_2", classify_kind("2.1 卷积神经网络"))


class CoverTitleNotHeadingTest(unittest.TestCase):
    """封面题目被套了"标题 1"样式时，它不是章标题（封面区里没有章号）。

    实测 4 段封面题目因此被按一级标题查编号/字体/字号（12 条）。
    """

    @staticmethod
    def _blocks():
        return [
            {"type": "paragraph", "index": 0, "kind": "heading_1",
             "text": "基于机器学习的癌症检测的实现", "alignment": "center",
             "runs": [{"text": "基于机器学习的癌症检测的实现",
                       "east_asia_font": "黑体", "font_size_pt": 16}]},
            {"type": "paragraph", "index": 1, "kind": "abstract_title",
             "text": "摘 要", "alignment": "center"},
            {"type": "paragraph", "index": 2, "kind": "body",
             "text": "本文研究了癌症检测。"},
        ]

    def test_cover_title_before_any_anchor_is_not_a_heading(self):
        blocks = self._blocks()
        apply_contextual_kinds(blocks)
        self.assertNotIn(blocks[0]["kind"], ("heading_1", "heading_2", "heading_3"))
        # 交给封面分支后按封面行处理：居中、字号在题目区间的题目行 → title
        self.assertEqual("title", blocks[0]["kind"])

    def test_chapter_heading_before_anchor_stays_a_heading(self):
        """片段文档（没有正文锚点）里的一级标题不许降级。"""
        blocks = [
            {"type": "paragraph", "index": 0, "kind": "heading_1",
             "text": "第1章 绪论", "alignment": "center"},
            {"type": "paragraph", "index": 1, "kind": "body", "text": "正文段落。"},
        ]
        apply_contextual_kinds(blocks)
        self.assertEqual("heading_1", blocks[0]["kind"])

    def test_numbered_heading_in_front_region_stays_a_heading(self):
        """封面区里出现带章号的标题时照旧进正文区（不降级）。"""
        blocks = [
            {"type": "paragraph", "index": 0, "kind": "heading_1",
             "text": "第1章 绪论", "alignment": "center"},
            {"type": "paragraph", "index": 1, "kind": "abstract_title",
             "text": "摘 要", "alignment": "center"},
            {"type": "paragraph", "index": 2, "kind": "body", "text": "正文段落。"},
        ]
        apply_contextual_kinds(blocks)
        self.assertEqual("heading_1", blocks[0]["kind"])

    def test_english_title_after_keywords_is_not_a_heading(self):
        """英文题名排在中文关键词之后（该份文档封面就是这个顺序）→ 英文题目。

        实测这份（张权辉）的英文题名用了"标题 1"样式，被当一级标题报"编号不符合"。
        它不在封面区（中文摘要之后），所以前置区域要整体认，不能只看封面区。
        """
        blocks = [
            {"type": "paragraph", "index": 0, "kind": "abstract_title",
             "text": "中文摘要", "alignment": "center"},
            {"type": "paragraph", "index": 1, "kind": "keyword_title",
             "text": "关键词：病理影像；肺癌", "alignment": "justify"},
            {"type": "paragraph", "index": 2, "kind": "heading_1",
             "text": "Implementation of Cancer Detection Based on Machine Learning",
             "alignment": "center",
             "runs": [{"text": "Implementation of Cancer Detection",
                       "latin_font": "Times New Roman", "font_size_pt": 16}]},
        ]
        apply_contextual_kinds(blocks)
        self.assertEqual("en_title", blocks[2]["kind"])


if __name__ == "__main__":
    unittest.main()
