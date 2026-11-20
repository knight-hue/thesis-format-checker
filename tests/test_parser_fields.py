# -*- coding: utf-8 -*-
"""域代码识别：段落级 fields 必须能看出这一段里有哪些域。

TOC 域的指令在首段、域结果散布在后续段落，只看域结果会把“这一段有没有 TOC 域”
判成没有——§9.2 的“目录用 TOC 域生成”那条检查就依赖这里的判定。
"""
import unittest

from lxml import etree

from backend.parser.fields import paragraph_field_types

NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _paragraph(body):
    return etree.fromstring(f"<w:p {NS}>{body}</w:p>")


class ParagraphFieldTypesTest(unittest.TestCase):

    def test_instruction_only_paragraph_is_detected(self):
        """TOC 的指令与域结果不在同一段（Word 生成的目录就是这种形态）。"""
        paragraph = _paragraph(
            '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText> TOC \\o "1-3" \\h \\z \\u </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>')
        self.assertEqual(["TOC"], paragraph_field_types(paragraph))

    def test_split_instruction_runs_are_detected(self):
        paragraph = _paragraph(
            '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText> TOC </w:instrText></w:r>'
            '<w:r><w:instrText>\\o "1-3"</w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            "<w:r><w:t>第1章 绪论</w:t></w:r>")
        self.assertEqual(["TOC"], paragraph_field_types(paragraph))

    def test_simple_field_is_detected(self):
        paragraph = _paragraph(
            '<w:fldSimple w:instr=\' TOC \\o "1-3" \'>'
            "<w:r><w:t>目录</w:t></w:r></w:fldSimple>")
        self.assertEqual(["TOC"], paragraph_field_types(paragraph))

    def test_page_field_result_run_is_still_detected(self):
        paragraph = _paragraph(
            '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            "<w:r><w:instrText> PAGE </w:instrText></w:r>"
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            "<w:r><w:t>3</w:t></w:r>")
        self.assertEqual(["PAGE"], paragraph_field_types(paragraph))

    def test_paragraph_without_fields_stays_empty(self):
        self.assertEqual([], paragraph_field_types(_paragraph("<w:r><w:t>正文段落。</w:t></w:r>")))


if __name__ == "__main__":
    unittest.main()
