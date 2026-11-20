# -*- coding: utf-8 -*-
"""Word 自动编号（w:numPr）的解析与判定。

官方模板的标题用自动编号：编号由 Word 生成、不在段落文本里（XML 证据：
``<w:numPr><w:ilvl w:val="2"/><w:numId w:val="1"/></w:numPr>``）。判定层若只看
文本，会把“编号由 Word 生成”当成“没写编号”，把模板自己的标题报成编号格式错。
"""
import json
import os
import tempfile
import unittest

from docx import Document
from docx.oxml.ns import nsdecls, qn
from docx.oxml.parser import parse_xml

from backend.detection.rule_engine import RuleEngine
from backend.recognition.context import apply_contextual_kinds
from backend.parser.docx_to_json import docx_to_json
from backend.parser.numbering import NumberingDefinitions, NumberingRenderer

from backend.college_registry import path_of

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_DIR = os.path.join(ROOT, "config", "rules")


def _rules(college_id="jlu_cs"):
    with open(path_of(college_id, "rules", RULES_DIR), encoding="utf-8") as handle:
        return json.load(handle)


def _parse(build):
    with tempfile.TemporaryDirectory() as tmp:
        source = os.path.join(tmp, "case.docx")
        out = os.path.join(tmp, "out")
        doc = Document()
        build(doc)
        doc.save(source)
        with open(docx_to_json(source, out), encoding="utf-8") as handle:
            return json.load(handle)


def _numbered(paragraph, num_id="1", level="0"):
    paragraph._p.get_or_add_pPr().insert(
        0, parse_xml(f'<w:numPr {nsdecls("w")}>'
                     f'<w:ilvl w:val="{level}"/><w:numId w:val="{num_id}"/></w:numPr>'))


class AutoNumberParseTest(unittest.TestCase):
    """解析层要把“编号由 Word 生成”记下来。"""

    def test_paragraph_with_num_pr_is_marked(self):
        def build(doc):
            paragraph = doc.add_paragraph("三级标题")
            _numbered(paragraph)

        blocks = _parse(build)["blocks"]
        self.assertTrue(blocks[0].get("auto_number"))

    def test_plain_paragraph_is_not_marked(self):
        def build(doc):
            doc.add_paragraph("正文段落")

        blocks = _parse(build)["blocks"]
        self.assertIsNone(blocks[0].get("auto_number"))

    def test_cancelled_numbering_is_not_marked(self):
        """<w:numId w:val="0"/> 表示取消编号，不算自动编号。"""

        def build(doc):
            paragraph = doc.add_paragraph("三级标题")
            _numbered(paragraph, num_id="0")

        blocks = _parse(build)["blocks"]
        self.assertIsNone(blocks[0].get("auto_number"))


class AutoNumberRuleTest(unittest.TestCase):
    """编号不在文本里时，不按“编号格式”判。"""

    def _ids(self, *, auto_number, text="三级标题"):
        block = {
            "type": "paragraph", "index": 0, "kind": "heading_3", "text": text,
            "alignment": "left", "first_line_indent_pt": None,
            "line_spacing_multiple": 1.5, "space_before_pt": 0.0,
            "runs": [{"text": text, "font_name": "黑体", "east_asia_font": "黑体",
                      "font_size_pt": 12.0, "bold": True}],
        }
        if auto_number:
            block["auto_number"] = True
        return [i["rule_id"] for i in
                RuleEngine(_rules()).check({"sections": [], "blocks": [block]})["issues"]]

    def test_auto_numbered_heading_skips_the_number_check(self):
        self.assertNotIn("heading.heading_3.number", self._ids(auto_number=True))

    def test_heading_without_a_number_is_still_reported(self):
        """真的没写编号（也不是自动编号）时照旧报出来。"""
        self.assertIn("heading.heading_3.number", self._ids(auto_number=False))

    def test_auto_number_does_not_disable_other_checks(self):
        """跳过的只有编号格式：字体字号仍要查。"""
        block = {
            "type": "paragraph", "index": 0, "kind": "heading_3", "text": "三级标题",
            "auto_number": True, "alignment": "left", "first_line_indent_pt": None,
            "line_spacing_multiple": 1.5, "space_before_pt": 0.0,
            "runs": [{"text": "三级标题", "font_name": "黑体", "east_asia_font": "黑体",
                      "font_size_pt": 14.0, "bold": True}],
        }
        ids = [i["rule_id"] for i in
               RuleEngine(_rules()).check({"sections": [], "blocks": [block]})["issues"]]
        self.assertIn("heading.heading_3.font_size", ids)


class ReferenceWithoutNumberTest(unittest.TestCase):
    """§7.1（模板原文）：「参考文献的序号左顶格，并用数字加方括号表示」。

    没写序号的著录条目要按“条目”判（报“应以方括号序号开头”），不能当正文查——
    条目是五号、正文是小四，当正文查会报“正文字号不符合要求”。
    而模板里每种文献类型都配的“［序号］…／例如：…”说明/举例行不是待查条目。
    """

    HEADING = {"type": "paragraph", "index": 0, "kind": "heading_1",
               "text": "第1章 绪论", "alignment": "center",
               "first_line_indent_pt": 0.0, "line_spacing_multiple": 1.5,
               "space_before_pt": 0.0,
               "runs": [{"text": "第1章 绪论", "font_name": "黑体",
                         "east_asia_font": "黑体", "font_size_pt": 16.0, "bold": True}]}
    TITLE = {"type": "paragraph", "index": 1, "kind": "reference_title",
             "text": "参考文献", "alignment": "center",
             "first_line_indent_pt": 0.0, "line_spacing_multiple": 1.5,
             "space_before_pt": 0.0,
             "runs": [{"text": "参考文献", "font_name": "黑体",
                       "east_asia_font": "黑体", "font_size_pt": 16.0, "bold": True}]}

    def _entry(self, index, text, size=10.5):
        return {"type": "paragraph", "index": index, "kind": "body", "text": text,
                "alignment": "justify", "first_line_indent_pt": None,
                "line_spacing_multiple": 1.5, "space_before_pt": 0.0,
                "runs": [{"text": text, "font_name": "宋体", "east_asia_font": "宋体",
                          "font_size_pt": size, "bold": False}]}

    def _kinds(self, *texts):
        blocks = [self.HEADING, self.TITLE]
        for i, item in enumerate(texts):
            if isinstance(item, tuple):
                blocks.append(self._entry(len(blocks), item[0], item[1]))
            else:
                blocks.append(self._entry(len(blocks), item))
        return apply_contextual_kinds(blocks)

    def _ids(self, *texts):
        blocks = self._kinds(*texts)
        return [i["rule_id"] for i in
                RuleEngine(_rules()).check({"sections": [], "blocks": blocks})["issues"]]

    def test_entry_without_number_is_an_entry(self):
        blocks = self._kinds("韩家炜, 孟小峰. Web挖掘研究[J]. 计算机研究与发展, 2001, 38(4): 405-410.")
        self.assertEqual("reference_entry", blocks[2].get("kind"))

    def test_entry_without_number_reports_the_missing_number(self):
        self.assertIn("reference.entry_number", self._ids(
            "韩家炜, 孟小峰. Web挖掘研究[J]. 计算机研究与发展, 2001, 38(4): 405-410."))

    def test_entry_without_number_is_not_judged_as_body(self):
        ids = self._ids("韩家炜, 孟小峰. Web挖掘研究[J]. 计算机研究与发展, 2001, 38(4): 405-410.")
        self.assertNotIn("body.font_size", ids)
        self.assertNotIn("body.latin_font", ids)

    def test_format_placeholder_line_is_not_an_entry(self):
        """“［序号］主要责任者．文献题名[J]．…”是格式说明（［序号］是占位符）。"""
        blocks = self._kinds("［序号］主要责任者．文献题名[J] ．刊名，出版年份，卷号(期号) ：起止页码．")
        self.assertEqual("reference_continuation", blocks[2].get("kind"))
        self.assertNotIn("reference.entry_number", self._ids(
            "［序号］主要责任者．文献题名[J] ．刊名，出版年份，卷号(期号) ：起止页码．"))

    def test_example_line_is_not_an_entry(self):
        blocks = self._kinds("例如：［1］毛峡，丁玉宽．图像的情感特征分析及其和谐感评价[J] ．电子学报，2001．")
        self.assertEqual("reference_continuation", blocks[2].get("kind"))

    def test_note_paragraph_in_another_size_is_not_an_entry(self):
        """参考文献区里 12pt 的说明文字不按“条目五号”判（模板 p205/p206 就是这种）。"""
        blocks = self._kinds(("凡在学位论文中有引用他人成果之处，均应列出参考文献[J] 类型标识。", 12.0))
        self.assertNotEqual("reference_entry", blocks[2].get("kind"))

    def test_numbered_entry_is_unchanged(self):
        blocks = self._kinds("［1］毛峡．图像的情感特征分析[J]．电子学报，2001．")
        self.assertEqual("reference_entry", blocks[2].get("kind"))
        self.assertNotIn("reference.entry_number", self._ids(
            "［1］毛峡．图像的情感特征分析[J]．电子学报，2001．"))


def _numbering_element(xml):
    """把一段 `<w:numbering>` XML（`{ns}` 占位）解析成根元素。"""
    return parse_xml(xml.replace("{ns}", nsdecls("w")))


NUMBERING_XML = (
    '<w:numbering {ns}>'
    '<w:abstractNum w:abstractNumId="7">'
    '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/>'
    '<w:lvlText w:val="[%1]"/></w:lvl>'
    '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="decimal"/>'
    '<w:lvlText w:val="%1.%2"/></w:lvl>'
    '<w:lvl w:ilvl="2"><w:start w:val="1"/><w:numFmt w:val="lowerRoman"/>'
    '<w:lvlText w:val="%3."/></w:lvl>'
    '</w:abstractNum>'
    '<w:abstractNum w:abstractNumId="8">'
    '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="chineseCounting"/>'
    '<w:lvlText w:val="第%1章"/></w:lvl>'
    '</w:abstractNum>'
    '<w:abstractNum w:abstractNumId="9">'
    '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="hebrew1"/>'
    '<w:lvlText w:val="%1"/></w:lvl>'
    '</w:abstractNum>'
    '<w:num w:numId="5"><w:abstractNumId w:val="7"/></w:num>'
    '<w:num w:numId="6"><w:abstractNumId w:val="8"/></w:num>'
    '<w:num w:numId="7"><w:abstractNumId w:val="9"/></w:num>'
    '</w:numbering>')


def _install_numbering(doc):
    """把测试用的编号定义装进文档（清掉默认模板自带的那套，避免 numId 撞车）。"""
    root = doc.part.numbering_part.element
    for child in list(root):
        if child.tag in (qn("w:abstractNum"), qn("w:num")):
            root.remove(child)
    for child in list(_numbering_element(NUMBERING_XML)):
        root.append(child)


class NumberingDefinitionsTest(unittest.TestCase):
    """numbering.xml 查表：每一级必须落在自己的 ilvl 上。"""

    def setUp(self):
        self.definitions = NumberingDefinitions.from_root(
            _numbering_element(NUMBERING_XML))

    def test_each_level_keeps_its_own_ilvl(self):
        """`w:ilvl` 是 `w:lvl` 的属性，按子元素读会全落成 0、被最后一级覆盖。"""
        self.assertEqual(("decimal", "[%1]", 1), self.definitions.spec(5, 0))
        self.assertEqual(("decimal", "%1.%2", 1), self.definitions.spec(5, 1))
        self.assertEqual(("lowerRoman", "%3.", 1), self.definitions.spec(5, 2))

    def test_other_num_id_shares_only_what_it_defines(self):
        self.assertEqual("第%1章", self.definitions.spec(6, 0)[1])
        self.assertIsNone(self.definitions.spec(6, 1))

    def test_unknown_num_id_has_no_spec(self):
        self.assertIsNone(self.definitions.spec(99, 0))

    def test_missing_part_gives_an_empty_table(self):
        self.assertEqual({}, NumberingDefinitions.from_root(None)._levels)


class NumberingRenderTest(unittest.TestCase):
    """把编号定义渲染成可见文字。"""

    def setUp(self):
        self.renderer = NumberingRenderer.from_root(
            _numbering_element(NUMBERING_XML))

    def test_bracketed_counter_advances_in_document_order(self):
        self.assertEqual("[1]", self.renderer.render(5, 0))
        self.assertEqual("[2]", self.renderer.render(5, 0))
        self.assertEqual("[3]", self.renderer.render(5, 0))

    def test_chinese_counting_renders_chapter_numbers(self):
        expected = ["第一章", "第二章", "第三章", "第四章", "第五章", "第六章",
                    "第七章", "第八章", "第九章", "第十章", "第十一章",
                    "第十二章", "第二十章"]
        rendered = [self.renderer.render(6, 0) for _ in range(12)]
        self.assertEqual(expected[:12], rendered)
        # 跳到第 20 章看十位进位（中文数字不是简单的数字拼接）
        for _ in range(7):
            self.renderer.render(6, 0)
        self.assertEqual("第二十章", self.renderer.render(6, 0))

    def test_deeper_level_reuses_the_parent_counter(self):
        self.renderer.render(5, 0)
        self.assertEqual("1.1", self.renderer.render(5, 1))
        self.assertEqual("1.2", self.renderer.render(5, 1))
        # 父级前进一级后，子级回到 1
        self.renderer.render(5, 0)
        self.assertEqual("2.1", self.renderer.render(5, 1))

    def test_parent_never_rendered_is_not_guessed(self):
        """没渲染过父级就不知道父级编号是多少，宁可不渲染。"""
        self.assertIsNone(self.renderer.render(5, 1))

    def test_unsupported_number_format_is_not_guessed(self):
        self.assertIsNone(self.renderer.render(7, 0))

    def test_unknown_num_id_is_not_guessed(self):
        self.assertIsNone(self.renderer.render(99, 0))


class AutoNumberRenderWiringTest(unittest.TestCase):
    """解析 → 判定：渲染出的编号要真的被规则层用上。"""

    def _document(self, *, num_id="5"):
        def build(doc):
            _install_numbering(doc)
            paragraph = doc.add_paragraph(
                "Mehmed Kantardzi. 数据挖掘-概念模型方法和算法[M]. 北京: 清华大学出版社.")
            _numbered(paragraph, num_id=num_id)

        return _parse(build)

    def _reference_issues(self, document):
        for index, block in enumerate(document["blocks"]):
            block["kind"] = "reference_entry"
            block["index"] = index
        return RuleEngine(_rules()).check(document)["issues"]

    def test_rendered_number_reaches_the_block(self):
        blocks = [b for b in self._document()["blocks"]
                  if (b.get("text") or "").startswith("Mehmed")]
        self.assertEqual(1, len(blocks))
        self.assertEqual("[1]", blocks[0].get("auto_number_text"))

    def test_rendered_number_is_accepted_by_the_rule(self):
        """自动编号渲染成 [1] 后，半角方括号按“数字加方括号”合格，不再报括号宽窄。"""
        found = [i for i in self._reference_issues(self._document())
                 if i["rule_id"] == "reference.entry_number"]
        self.assertEqual([], found)

    def test_unrenderable_number_does_not_become_a_missing_number(self):
        """编号定义不支持（hebrew1）时跳过该检查，而不是按“没写序号”报。"""
        document = self._document(num_id="7")
        blocks = [b for b in document["blocks"]
                  if (b.get("text") or "").startswith("Mehmed")]
        self.assertNotIn("auto_number_text", blocks[0])
        self.assertNotIn("reference.entry_number",
                         [i["rule_id"] for i in self._reference_issues(document)])


if __name__ == "__main__":
    unittest.main()
