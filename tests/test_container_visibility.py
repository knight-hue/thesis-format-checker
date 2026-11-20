# -*- coding: utf-8 -*-
"""容器里的内容必须看得见：行内内容控件、页眉/页脚内容控件、文本框。

三类都是“解析层看不见”的同一类问题：

* 行内 ``w:sdt``（文本控件）与行内 ``w:ins`` 包住的 run：``CT_P.text`` 只认
  ``xpath("w:r | w:hyperlink")``，包在里面的字整段消失——既不检查字体字号，
  写回时也会被当多余内容丢掉；
* 页眉/页脚里的内容控件：``container.paragraphs`` 同样只取直接子级；
* 文本框（``w:txbxContent``）：内容完全不在 ``w:body`` 里，目录整块放文本框时
  检测会报“未检测到目录”。

只改容器遍历口径，判定逻辑一律不动。
"""
import copy
import json
import os
import tempfile
import unittest

from docx import Document
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import qn

from backend.detection.checker_service import check_document
from backend.parser.docx_to_json import docx_to_json
from backend.write_back._patch.locate import source_elements
from backend.write_back.patch_docx import patch_docx

W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
VML_NS = 'xmlns:v="urn:schemas-microsoft-com:vml"'
ENTRIES = ["第1章　绪论\t1", "1.1　研究背景\t1", "参考文献\t20", "致　　谢\t22"]


def _inline_sdt(text):
    """行内内容控件（包住 run）。"""
    return parse_xml(
        f"<w:sdt {W_NS}><w:sdtPr><w:text/></w:sdtPr>"
        f"<w:sdtContent><w:r><w:t>{text}</w:t></w:r></w:sdtContent></w:sdt>")


def _inline_wrapper(text, tag):
    """行内包装（``w:ins`` 等直接包住 run）。"""
    return parse_xml(f"<{tag} {W_NS}><w:r><w:t>{text}</w:t></w:r></{tag}>")


def _inline_del(text):
    """修订删除的 run：``w:delText`` 不是 ``w:t``，进不了本段文字。"""
    return parse_xml(
        f"<w:del {W_NS}><w:r><w:delText>{text}</w:delText></w:r></w:del>")


def _textbox(lines):
    """文本框：``w:pict`` → ``v:textbox`` → ``w:txbxContent``（Word 的 VML 画法）。"""
    inner = "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in lines)
    return parse_xml(
        f"<w:pict {W_NS} {VML_NS}>"
        f'<v:shape style="width:400pt;height:200pt">'
        f"<v:textbox><w:txbxContent>{inner}</w:txbxContent></v:textbox>"
        f"</v:shape></w:pict>")


class _Fixture(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = self.temp.name

    def tearDown(self):
        self.temp.cleanup()

    def _parse(self, name, build):
        doc = Document()
        build(doc)
        path = os.path.join(self.base, f"{name}.docx")
        doc.save(path)
        out = os.path.join(self.base, f"{name}_out")
        os.makedirs(out, exist_ok=True)
        with open(docx_to_json(path, out), encoding="utf-8") as handle:
            return path, json.load(handle)


class InlineContainerTest(_Fixture):
    """行内内容控件/修订插入里的文字要进段落文字与 run 流。"""

    def test_inline_content_control_text_is_visible(self):
        def build(doc):
            paragraph = doc.add_paragraph("前缀 ")
            paragraph._p.append(_inline_sdt("INLINE"))

        _, document = self._parse("inline_sdt", build)
        block = document["blocks"][0]
        self.assertEqual("前缀 INLINE", block["text"])
        self.assertEqual(block["text"], "".join(run["text"] for run in block["runs"]))
        self.assertIn("INLINE", [run["text"] for run in block["runs"]])

    def test_inline_insertion_is_visible(self):
        def build(doc):
            paragraph = doc.add_paragraph("A")
            paragraph._p.append(_inline_wrapper("B", "w:ins"))

        _, document = self._parse("inline_ins", build)
        self.assertEqual("AB", document["blocks"][0]["text"])

    def test_inline_deletion_stays_hidden(self):
        def build(doc):
            paragraph = doc.add_paragraph("A")
            paragraph._p.append(_inline_del("DELETED"))

        _, document = self._parse("inline_del", build)
        self.assertEqual("A", document["blocks"][0]["text"])

    def test_text_order_across_inline_wrappers(self):
        def build(doc):
            paragraph = doc.add_paragraph("一")
            paragraph._p.append(_inline_sdt("二"))
            paragraph._p.append(_inline_wrapper("三", "w:ins"))

        _, document = self._parse("inline_order", build)
        self.assertEqual("一二三", document["blocks"][0]["text"])


class HeaderFooterContainerTest(_Fixture):
    """页眉/页脚里的内容控件要出现在 sections[].headers/footers 里。"""

    def test_header_paragraph_in_content_control_is_collected(self):
        def build(doc):
            header = doc.sections[0].header
            header.is_linked_to_previous = False
            paragraph = header.paragraphs[0]
            paragraph.text = "吉林大学 计算机科学与技术学院 毕业论文"
            box = OxmlElement("w:sdt")
            properties = OxmlElement("w:sdtPr")
            properties.append(OxmlElement("w:text"))
            box.append(properties)
            content = OxmlElement("w:sdtContent")
            parent = paragraph._p.getparent()
            content.append(paragraph._p)          # append 会把段落从页眉摘走
            box.append(content)
            parent.append(box)
            doc.add_paragraph("正文段落。")

        _, document = self._parse("header_sdt", build)
        headers = document["sections"][0]["headers"]
        texts = [p["text"] for entry in headers for p in entry.get("paragraphs", [])]
        self.assertIn("吉林大学 计算机科学与技术学院 毕业论文", texts)


class TextBoxTest(_Fixture):
    """文本框里的段落要按顺序进契约，目录放文本框也要认得出来。"""

    def _toc_document(self, name, title=None):
        def build(doc):
            doc.add_paragraph("摘要")
            lines = (["目录"] if title else []) + ENTRIES if title is not None else ENTRIES
            anchor = doc.add_paragraph()
            anchor._p.append(_textbox(lines))
            doc.add_paragraph("第1章　绪论", style="Heading 1")
            doc.add_paragraph("本文的研究背景如下所述。")

        return self._parse(name, build)[1]

    def test_textbox_paragraphs_are_parsed_after_anchor(self):
        def build(doc):
            doc.add_paragraph("A 正常段落")
            doc.add_paragraph()._p.append(_textbox(["文本框第一行", "文本框第二行"]))
            doc.add_paragraph("C 正常段落")

        _, document = self._parse("textbox_body", build)
        texts = [b.get("text") for b in document["blocks"] if b.get("type") == "paragraph"]
        self.assertEqual(["A 正常段落", "", "文本框第一行", "文本框第二行", "C 正常段落"],
                         [t for t in texts if t is not None])

    def test_toc_inside_textbox_is_recognised(self):
        document = self._toc_document("textbox_toc")
        self.assertGreaterEqual(len([b for b in document["blocks"] if b.get("kind") == "toc"]),
                                len(ENTRIES))

    def test_toc_inside_textbox_passes_detection(self):
        """目录条款整块已撤销：文本框里的目录也不该报任何 toc.* 规则。"""
        document = self._toc_document("textbox_toc_check")
        result = check_document(document, "jlu_cs", rag_service=None)
        toc_issues = [issue for issue in result["issues"]
                      if str(issue.get("rule_id", "")).startswith("toc.")]
        self.assertEqual([], toc_issues)


class ContainerWriteBackTest(_Fixture):
    """容器里的段落也要能定位、能写回（解析层与写回层同一口径）。"""

    def _source(self):
        def build(doc):
            doc.add_paragraph("A 正常段落")
            doc.add_paragraph()._p.append(_textbox(["文本框段落"]))
            paragraph = doc.add_paragraph("B 行内控件 ")
            paragraph._p.append(_inline_sdt("C"))
            doc.add_paragraph("D 正常段落")

        name = "container_writeback"
        path, document = self._parse(name, build)
        return path, os.path.join(self.base, f"{name}_fix.docx"), document

    def test_source_elements_align_with_contract(self):
        source, _, document = self._source()
        elements = source_elements(Document(source), document["blocks"])
        self.assertEqual(len(document["blocks"]), len(elements))

    def test_fix_inside_textbox_is_written_back(self):
        source, output, document = self._source()
        source_ids = {id(block): index for index, block in enumerate(document["blocks"])}
        before = copy.deepcopy(document)
        index = next(i for i, b in enumerate(document["blocks"])
                     if b.get("text") == "文本框段落")
        document["blocks"][index]["runs"][0]["font_size_pt"] = 16.0

        report = patch_docx(source, output, before, document, source_ids)
        self.assertTrue(report["changed"], report)

        out = os.path.join(self.base, "container_writeback_out")
        os.makedirs(out, exist_ok=True)
        with open(docx_to_json(output, out), encoding="utf-8") as handle:
            reparsed = json.load(handle)
        block = next(b for b in reparsed["blocks"] if b.get("text") == "文本框段落")
        self.assertEqual(16.0, block["runs"][0]["font_size_pt"])


if __name__ == "__main__":
    unittest.main()
