# -*- coding: utf-8 -*-
"""块级包装元素（内容控件/修订插入等）里的正文必须照样被解析与检查。

Word「引用→目录→自动目录」插入的目录是内容控件：

    <w:sdt><w:sdtPr><w:docPartObj>…Table of Contents…</w:docPartObj></w:sdtPr>
    <w:sdtContent>…“目录”标题 + 目录条目…</w:sdtContent></w:sdt>

python-docx 与旧解析只取 ``w:body`` 的直接子级，整块目录因此看不见，
检测侧报“未检测到目录”——文档明明有目录。修订插入（``w:ins``）、
旧版 ``w:customXml``、``w:smartTag`` 同理。
"""
import copy
import json
import os
import tempfile
import unittest

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from backend.detection.checker_service import check_document
from backend.parser.docx_to_json import docx_to_json
from backend.write_back.patch_docx import patch_docx

TOC_LINES = ["摘要\tI", "第1章　绪论\t1", "参考文献\t20", "致　　谢\t22"]


def _field_begin(paragraph, instruction, result_text):
    """写一段带域代码的段落：begin + 指令 + separate + 域结果（TOC 域的起始段）。"""
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    run._r.append(begin)
    run = paragraph.add_run()
    code = OxmlElement("w:instrText")
    code.text = instruction
    run._r.append(code)
    run = paragraph.add_run()
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    run._r.append(separate)
    paragraph.add_run(result_text)


def _field_end(paragraph):
    """域结束标记（TOC 域的 end 落在最后一个条目段落里）。"""
    run = paragraph.add_run()
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.append(end)


def _sdt_properties():
    properties = OxmlElement("w:sdtPr")
    part_obj = OxmlElement("w:docPartObj")
    gallery = OxmlElement("w:docPartGallery")
    gallery.set(qn("w:val"), "Table of Contents")
    part_obj.append(gallery)
    properties.append(part_obj)
    return properties


def _wrap(element, tag="w:sdt"):
    """把块级元素搬进包装元素（sdt 会补上 docPartObj/sdtContent 结构）。"""
    box = OxmlElement(tag)
    if tag == "w:sdt":
        box.append(_sdt_properties())
        content = OxmlElement("w:sdtContent")
        content.append(element)
        box.append(content)
    else:
        box.append(element)
    return box


def _wrap_body_range(doc, elements, tag="w:sdt"):
    """把 body 里连续的若干元素整体搬进一个包装元素。"""
    body = doc.element.body
    first = min(body.index(element) for element in elements)
    box = OxmlElement(tag)
    if tag == "w:sdt":
        box.append(_sdt_properties())
        content = OxmlElement("w:sdtContent")
        box.append(content)
    else:
        content = box
    for element in elements:
        content.append(element)     # append 会把元素从 body 摘走
    body.insert(first, box)
    return box


class _Fixture(unittest.TestCase):
    """共同的 docx 构造/解析工具。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = self.temp.name

    def tearDown(self):
        self.temp.cleanup()

    def _save(self, name, build):
        path = os.path.join(self.base, f"{name}.docx")
        doc = Document()
        build(doc)
        doc.save(path)
        return path

    def _parse(self, name, build):
        path = self._save(name, build)
        return path, self._blocks(path, name)

    def _blocks(self, path, name):
        out = os.path.join(self.base, f"{name}_out")
        os.makedirs(out, exist_ok=True)
        with open(docx_to_json(path, out), encoding="utf-8") as handle:
            return json.load(handle)


class BlockWrapperParseTest(_Fixture):
    """包装里的段落/表格要出现在解析结果里，且顺序不变。"""

    def test_paragraph_in_content_control_is_parsed(self):
        def build(doc):
            doc.add_paragraph("A 正常段落")
            target = doc.add_paragraph("B 控件内段落")
            doc.add_paragraph("C 正常段落")
            body = doc.element.body
            index = body.index(target._p)
            body.remove(target._p)
            body.insert(index, _wrap(target._p))

        _, document = self._parse("sdt_paragraph", build)
        texts = [b.get("text") for b in document["blocks"]]
        self.assertEqual(["A 正常段落", "B 控件内段落", "C 正常段落"], texts)

    def test_paragraph_in_tracked_insertion_is_parsed(self):
        def build(doc):
            doc.add_paragraph("A 正常段落")
            target = doc.add_paragraph("B 修订插入的段落")
            body = doc.element.body
            body.remove(target._p)
            body.insert(1, _wrap(target._p, "w:ins"))

        _, document = self._parse("ins_paragraph", build)
        self.assertIn("B 修订插入的段落", [b.get("text") for b in document["blocks"]])

    def test_paragraph_in_legacy_wrappers_is_parsed(self):
        for tag in ("w:customXml", "w:smartTag", "w:moveTo"):
            with self.subTest(tag=tag):
                def build(doc, tag=tag):
                    doc.add_paragraph("A 正常段落")
                    target = doc.add_paragraph("B 包装内段落")
                    body = doc.element.body
                    body.remove(target._p)
                    body.insert(1, _wrap(target._p, tag))

                _, document = self._parse(f"wrapper{tag[2:]}", build)
                self.assertIn("B 包装内段落", [b.get("text") for b in document["blocks"]])

    def test_nested_wrappers_are_parsed(self):
        def build(doc):
            target = doc.add_paragraph("B 双层包装内段落")
            body = doc.element.body
            body.remove(target._p)
            body.insert(0, _wrap(_wrap(target._p, "w:ins"), "w:sdt"))

        _, document = self._parse("nested", build)
        self.assertIn("B 双层包装内段落", [b.get("text") for b in document["blocks"]])

    def test_table_in_content_control_is_parsed(self):
        def build(doc):
            target = doc.add_table(rows=2, cols=2)
            target.cell(0, 0).text = "表头"
            target.cell(1, 0).text = "数据"
            body = doc.element.body
            body.remove(target._tbl)
            body.append(_wrap(target._tbl))

        _, document = self._parse("sdt_table", build)
        tables = [b for b in document["blocks"] if b.get("type") == "table"]
        self.assertEqual(1, len(tables))
        self.assertIn("表头", [cell["text"] for cell in tables[0]["cells"]])

    def test_tracked_deletion_stays_out_of_contract(self):
        """修订删除的内容文档里看不到，不进契约（与旧行为一致）。"""
        def build(doc):
            doc.add_paragraph("A 正常段落")
            target = doc.add_paragraph("B 已删除的段落")
            body = doc.element.body
            body.remove(target._p)
            body.insert(1, _wrap(target._p, "w:del"))

        _, document = self._parse("del_paragraph", build)
        self.assertEqual(["A 正常段落"], [b.get("text") for b in document["blocks"]])


class TocInContentControlTest(_Fixture):
    """官方规范要求有目录；目录带内容控件时不能被判成“没有目录”。"""

    def _toc_document(self, wrap):
        def build(doc):
            doc.add_paragraph("摘要")
            doc.add_paragraph("本文研究了论文格式自动检查方法。")
            title = doc.add_paragraph("目录")
            entries = [doc.add_paragraph(line) for line in TOC_LINES]
            if wrap:
                _wrap_body_range(doc, [title._p] + [entry._p for entry in entries])
            doc.add_paragraph("第1章　绪论")

        return self._parse("sdt_toc" if wrap else "bare_toc", build)[1]

    def test_toc_entries_are_recognised_without_wrapper(self):
        document = self._toc_document(wrap=False)
        self.assertTrue([b for b in document["blocks"] if b.get("kind") == "toc"])

    def test_toc_entries_are_recognised_inside_content_control(self):
        document = self._toc_document(wrap=True)
        kinds = [b.get("kind") for b in document["blocks"]]
        self.assertIn("toc", kinds, kinds)
        self.assertIn("目录", [b.get("text") for b in document["blocks"]])

    def test_detection_no_longer_reports_missing_toc(self):
        """目录检查整块已撤销：内容控件里的目录也不该报任何 toc.* 规则。"""
        document = self._toc_document(wrap=True)
        result = check_document(document, "jlu_cs", rag_service=None)
        toc_issues = [issue for issue in result["issues"]
                      if str(issue.get("rule_id", "")).startswith("toc.")]
        self.assertEqual([], toc_issues)


class AutomaticTocInContentControlTest(_Fixture):
    """Word「自动目录」的真实形态：内容控件 + TOC 域 + 域结果条目。

    目录由域生成、整块放在内容控件里，§9.1/§9.2 的目录条款都应通过。
    """

    def _build(self, doc):
        doc.add_paragraph("摘要")
        doc.add_paragraph("本文研究了论文格式自动检查方法。")
        title = doc.add_paragraph("目录")
        first = doc.add_paragraph()
        _field_begin(first, ' TOC \\o "1-3" \\h \\z \\u ', TOC_LINES[0])
        middle = [doc.add_paragraph(line) for line in TOC_LINES[1:-1]]
        last = doc.add_paragraph(TOC_LINES[-1])
        _field_end(last)
        _wrap_body_range(doc, [title._p, first._p]
                         + [entry._p for entry in middle] + [last._p])
        doc.add_paragraph("第1章　绪论")

    def test_automatic_toc_is_recognised(self):
        _, document = self._parse("automatic_toc", self._build)
        kinds = [b.get("kind") for b in document["blocks"]]
        self.assertGreaterEqual(kinds.count("toc"), len(TOC_LINES) + 1, kinds)

    def test_toc_requirements_pass(self):
        """目录条款整块已撤销：自动目录（域 + 内容控件）不该报任何 toc.* 规则。"""
        _, document = self._parse("automatic_toc_ok", self._build)
        result = check_document(document, "jlu_cs", rag_service=None)
        blocking = [issue for issue in result["issues"]
                    if str(issue.get("rule_id", "")).startswith("toc.")]
        self.assertEqual([], blocking)


class ContentControlWriteBackTest(_Fixture):
    """包装里的段落也要能定位并写回（解析层与写回层必须同一口径）。"""

    def _source(self):
        def build(doc):
            doc.add_paragraph("A 正常段落")
            target = doc.add_paragraph("B 控件内段落")
            target.add_run("正文")
            body = doc.element.body
            body.remove(target._p)
            body.insert(1, _wrap(target._p))
            doc.add_paragraph("C 正常段落")

        name = "writeback_sdt"
        source = self._save(name, build)
        return source, os.path.join(self.base, "writeback_out.docx"), self._blocks(source, name)

    def test_run_format_inside_content_control_is_written_back(self):
        source, output, document = self._source()
        source_ids = {id(block): index
                      for index, block in enumerate(document["blocks"])}
        before = copy.deepcopy(document)
        target_index = next(index for index, block in enumerate(document["blocks"])
                            if block.get("text", "").startswith("B 控件内段落"))
        document["blocks"][target_index]["runs"][0]["font_size_pt"] = 16.0

        report = patch_docx(source, output, before, document, source_ids)
        self.assertTrue(report["changed"], report)

        reparsed = self._blocks(output, "writeback_out")
        block = next(b for b in reparsed["blocks"]
                     if b.get("text", "").startswith("B 控件内段落"))
        self.assertEqual(16.0, block["runs"][0]["font_size_pt"])
        self.assertEqual(["A 正常段落", "B 控件内段落正文", "C 正常段落"],
                         [b.get("text") for b in reparsed["blocks"]])


if __name__ == "__main__":
    unittest.main()
