import copy
import io
import json
import tempfile
import unittest
import struct
import zlib
from pathlib import Path
from zipfile import ZipFile

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.section import WD_ORIENT, WD_SECTION_START
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm
from fastapi.testclient import TestClient
from lxml import etree
from pydantic import ValidationError

import backend.api as api
from backend.document_contract import DocumentContract
from backend.fix.fix_blocks import fix_document
from backend.parser import docx_to_json
from backend.parser.media import iter_image_refs
from backend.write_back.patch_docx import patch_docx


def _field(paragraph, instruction):
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    run._r.append(begin)
    run = paragraph.add_run()
    code = OxmlElement("w:instrText")
    code.text = instruction
    run._r.append(code)
    run = paragraph.add_run()
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.append(end)


def _source(path):
    doc = Document()
    doc.styles.add_style("ProjectCustom", WD_STYLE_TYPE.PARAGRAPH)
    doc.sections[0].header.paragraphs[0].text = "学院名称"
    doc.sections[0].footer.paragraphs[0].text = "页码："
    _field(doc.sections[0].footer.paragraphs[0], "PAGE")
    body = doc.add_paragraph("正文测试")
    body.style = "ProjectCustom"
    toc = doc.add_paragraph()
    _field(toc, 'TOC \\o "1-3"')
    doc.save(path)


def _png(red, green, blue):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00" + bytes([red, green, blue])))
        + chunk(b"IEND", b"")
    )


def _parse(path, directory):
    json_path = docx_to_json(str(path), output_dir=str(directory))
    with open(json_path, encoding="utf-8") as source:
        return json.load(source)


def _with_footnote(source, destination):
    """构造带实际脚注关系和正文脚注引用的临时 OOXML 样本。"""
    with ZipFile(source) as package:
        parts = {part.filename: (part, package.read(part)) for part in package.infolist()}
    document = etree.fromstring(parts["word/document.xml"][1])
    first_paragraph = next(child for child in document.find(qn("w:body")) if child.tag == qn("w:p"))
    reference_run = etree.SubElement(first_paragraph, qn("w:r"))
    reference = etree.SubElement(reference_run, qn("w:footnoteReference"))
    reference.set(qn("w:id"), "1")
    parts["word/document.xml"] = (
        parts["word/document.xml"][0], etree.tostring(document, encoding="UTF-8", xml_declaration=True)
    )
    relationships = etree.fromstring(parts["word/_rels/document.xml.rels"][1])
    relationship = etree.SubElement(
        relationships, "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
    )
    relationship.set("Id", "rId999")
    relationship.set("Type", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes")
    relationship.set("Target", "footnotes.xml")
    parts["word/_rels/document.xml.rels"] = (
        parts["word/_rels/document.xml.rels"][0], etree.tostring(relationships, encoding="UTF-8", xml_declaration=True)
    )
    types = etree.fromstring(parts["[Content_Types].xml"][1])
    override = etree.SubElement(
        types, "{http://schemas.openxmlformats.org/package/2006/content-types}Override"
    )
    override.set("PartName", "/word/footnotes.xml")
    override.set("ContentType", "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml")
    parts["[Content_Types].xml"] = (
        parts["[Content_Types].xml"][0], etree.tostring(types, encoding="UTF-8", xml_declaration=True)
    )
    footnotes = etree.Element(qn("w:footnotes"), nsmap={"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"})
    footnote = etree.SubElement(footnotes, qn("w:footnote"))
    footnote.set(qn("w:id"), "1")
    text = etree.SubElement(etree.SubElement(etree.SubElement(footnote, qn("w:p")), qn("w:r")), qn("w:t"))
    text.text = "脚注原文"
    with ZipFile(destination, "w") as package:
        for part, content in parts.values():
            package.writestr(part, content)
        package.writestr("word/footnotes.xml", etree.tostring(footnotes, encoding="UTF-8", xml_declaration=True))


class WritebackFidelityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.source = self.base / "source.docx"
        self.output = self.base / "output.docx"
        _source(self.source)
        self.document = _parse(self.source, self.base)

    def tearDown(self):
        self.temp.cleanup()

    def test_format_fix_preserves_all_other_package_parts(self):
        DocumentContract.model_validate(self.document)
        before = copy.deepcopy(self.document)
        source_ids = {id(block): index for index, block in enumerate(self.document["blocks"])}
        self.document["blocks"][0]["runs"][0]["font_size_pt"] = 16.0
        patch_docx(self.source, self.output, before, self.document, source_ids)
        with ZipFile(self.source) as original, ZipFile(self.output) as fixed:
            self.assertEqual(set(original.namelist()), set(fixed.namelist()))
            for name in original.namelist():
                if name != "word/document.xml":
                    self.assertEqual(original.read(name), fixed.read(name), name)
            self.assertIn(b"TOC", fixed.read("word/document.xml"))
        result = Document(self.output)
        self.assertEqual(result.sections[0].header.paragraphs[0].text, "学院名称")
        self.assertEqual(result.sections[0].footer.paragraphs[0].text, "页码：")
        self.assertEqual(result.paragraphs[0].style.name, "ProjectCustom")
        self.assertEqual(result.paragraphs[0].runs[0].font.size.pt, 16)

    def test_real_footnote_part_and_reference_survive_format_fix(self):
        with_footnote = self.base / "footnote.docx"
        _with_footnote(self.source, with_footnote)
        self.source = with_footnote
        self.document = _parse(self.source, self.base)
        before = copy.deepcopy(self.document)
        source_ids = {id(block): index for index, block in enumerate(self.document["blocks"])}
        self.document["blocks"][0]["runs"][0]["font_size_pt"] = 16.0
        patch_docx(self.source, self.output, before, self.document, source_ids)
        with ZipFile(self.source) as original, ZipFile(self.output) as fixed:
            self.assertEqual(original.read("word/footnotes.xml"), fixed.read("word/footnotes.xml"))
            self.assertEqual(original.read("word/_rels/document.xml.rels"), fixed.read("word/_rels/document.xml.rels"))
            self.assertIn(b"footnoteReference", fixed.read("word/document.xml"))
        self.assertEqual(Document(self.output).paragraphs[0].runs[0].font.size.pt, 16)

    def test_image_relation_merge_keeps_existing_footnote_relation(self):
        source_doc = Document(self.source)
        source_doc.add_picture(io.BytesIO(_png(0, 0, 255)))
        source_doc.save(self.source)
        with_footnote = self.base / "footnote_image.docx"
        _with_footnote(self.source, with_footnote)
        self.source = with_footnote
        self.document = _parse(self.source, self.base)
        before = copy.deepcopy(self.document)
        source_ids = {id(block): index for index, block in enumerate(self.document["blocks"])}
        image_index = len(self.document["blocks"]) - 1
        images_dir = self.base / "images"
        images_dir.mkdir(exist_ok=True)
        replacement = images_dir / f"img_replaced_{image_index:04d}.png"
        replacement.write_bytes(_png(255, 0, 0))
        self.document["blocks"][image_index]["path"] = f"images/{replacement.name}"
        patch_docx(self.source, self.output, before, self.document, source_ids, images_dir=str(images_dir))
        with ZipFile(self.source) as original, ZipFile(self.output) as fixed:
            self.assertEqual(original.read("word/footnotes.xml"), fixed.read("word/footnotes.xml"))
            relationships = fixed.read("word/_rels/document.xml.rels")
            self.assertIn(b"footnotes.xml", relationships)
            self.assertIn(b"image", relationships)
            self.assertIn(b"footnoteReference", fixed.read("word/document.xml"))
        self.assertEqual(len(Document(self.output).inline_shapes), 1)

    def test_no_change_copies_original_byte_for_byte(self):
        before = copy.deepcopy(self.document)
        source_ids = {id(block): index for index, block in enumerate(self.document["blocks"])}
        patch_docx(self.source, self.output, before, self.document, source_ids)
        self.assertEqual(self.source.read_bytes(), self.output.read_bytes())

    def test_field_text_rewrite_is_skipped_with_warning(self):
        """W5：含域的段落不再让整次写回失败，改不动就跳过并告警，输出照常产出。"""
        before = copy.deepcopy(self.document)
        source_ids = {id(block): index for index, block in enumerate(self.document["blocks"])}
        self.document["blocks"][1]["text"] = "改写目录"
        report = patch_docx(self.source, self.output, before, self.document, source_ids)
        self.assertTrue(self.output.exists())
        self.assertFalse(report["changed"])
        self.assertTrue(
            any("不可安全改写" in warning for warning in report["skipped"]), report["skipped"],
        )
        # 域代码 run 必须原样保留：没有任何改动时输出与原文件逐字节一致
        self.assertEqual(self.source.read_bytes(), self.output.read_bytes())
        with ZipFile(self.output) as output:
            self.assertIn(b"fldChar", output.read("word/document.xml"))

    def test_simple_multi_run_text_rewrite_keeps_style_and_header(self):
        doc = Document(self.source)
        paragraph = doc.paragraphs[0]
        paragraph.clear()
        paragraph.add_run("第一段")
        paragraph.add_run("第二段")
        doc.save(self.source)
        self.document = _parse(self.source, self.base)
        before = copy.deepcopy(self.document)
        source_ids = {id(block): index for index, block in enumerate(self.document["blocks"])}
        block = self.document["blocks"][0]
        block["text"] = "改写后正文"
        block["runs"] = [{**block["runs"][0], "text": "改写后正文"}]
        DocumentContract.model_validate(self.document)
        patch_docx(self.source, self.output, before, self.document, source_ids)
        fixed = Document(self.output)
        self.assertEqual(fixed.paragraphs[0].text, "改写后正文")
        self.assertEqual(fixed.paragraphs[0].style.name, "ProjectCustom")
        self.assertEqual(fixed.sections[0].header.paragraphs[0].text, "学院名称")

    def test_field_paragraph_format_change_keeps_field_xml(self):
        before = copy.deepcopy(self.document)
        source_ids = {id(block): index for index, block in enumerate(self.document["blocks"])}
        self.document["blocks"][1]["alignment"] = "center"
        patch_docx(self.source, self.output, before, self.document, source_ids)
        with ZipFile(self.output) as fixed:
            xml = fixed.read("word/document.xml")
            self.assertIn(b"TOC", xml)
            self.assertIn(b"fldChar", xml)

    def test_api_fix_preserves_header_and_custom_style(self):
        issue = {
            "rule_id": "body.font_size", "source": "rule_engine",
            "location": {"type": "paragraph", "index": 0, "run": 0},
            "expected": "16",
        }
        client = TestClient(api.app)
        try:
            # 测试不触发 RAG 初始化；纯规则写回不依赖模型。
            response = client.post(
                "/api/fix",
                data={"college_id": "jlu_cs", "issues_json": json.dumps([issue])},
                files={"file": ("paper.docx", self.source.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            )
        finally:
            client.close()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["X-Applied-Count"], "1")
        result = Document(io.BytesIO(response.content))
        self.assertEqual(result.sections[0].header.paragraphs[0].text, "学院名称")
        self.assertEqual(result.paragraphs[0].style.name, "ProjectCustom")
        self.assertEqual(result.paragraphs[0].runs[0].font.size.pt, 16)

    def test_api_image_replace_preserves_original_image_and_headers(self):
        original_png, replacement_png = _png(0, 0, 255), _png(255, 0, 0)
        doc = Document(self.source)
        doc.add_picture(io.BytesIO(original_png))
        doc.save(self.source)
        image_index = len(_parse(self.source, self.base)["blocks"]) - 1
        issue = {"rule_id": "image.replace", "location": {"type": "image", "index": image_index}}
        client = TestClient(api.app)
        try:
            response = client.post(
                "/api/fix",
                data={"college_id": "jlu_cs", "issues_json": json.dumps([issue])},
                files=[
                    ("file", ("paper.docx", self.source.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
                    ("images", ("new.png", replacement_png, "image/png")),
                ],
            )
        finally:
            client.close()
        self.assertEqual(response.status_code, 200, response.text)
        fixed = Document(io.BytesIO(response.content))
        self.assertEqual(fixed.sections[0].header.paragraphs[0].text, "学院名称")
        references = list(iter_image_refs(fixed.paragraphs[-1], fixed))
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0][0].blob, replacement_png)
        with ZipFile(self.source) as original, ZipFile(io.BytesIO(response.content)) as output:
            for name in original.namelist():
                if name.startswith("word/media/") or name.startswith("word/header"):
                    self.assertEqual(original.read(name), output.read(name), name)

    def test_api_image_insert_preserves_toc_field(self):
        image = _png(255, 0, 0)
        issue = {"rule_id": "image.insert", "location": {"type": "paragraph", "index": 0}}
        client = TestClient(api.app)
        try:
            response = client.post(
                "/api/fix",
                data={"college_id": "jlu_cs", "issues_json": json.dumps([issue])},
                files=[
                    ("file", ("paper.docx", self.source.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
                    ("images", ("new.png", image, "image/png")),
                ],
            )
        finally:
            client.close()
        self.assertEqual(response.status_code, 200, response.text)
        fixed = Document(io.BytesIO(response.content))
        self.assertEqual(fixed.sections[0].header.paragraphs[0].text, "学院名称")
        self.assertEqual(len(fixed.inline_shapes), 1)
        with ZipFile(io.BytesIO(response.content)) as output:
            self.assertIn(b"TOC", output.read("word/document.xml"))

    def test_section_fix_keeps_header_footer_parts(self):
        before = copy.deepcopy(self.document)
        source_ids = {id(block): index for index, block in enumerate(self.document["blocks"])}
        self.document["sections"][0]["left_cm"] = 4.0
        patch_docx(self.source, self.output, before, self.document, source_ids)
        fixed = Document(self.output)
        self.assertAlmostEqual(fixed.sections[0].left_margin.cm, 4.0, places=2)
        with ZipFile(self.source) as original, ZipFile(self.output) as output:
            for name in original.namelist():
                if name.startswith("word/header") or name.startswith("word/footer"):
                    self.assertEqual(original.read(name), output.read(name), name)

    def _two_section_source(self):
        """第一节竖版、第二节横版（宽高已交换），第 2 节左边距 1.5cm 与规范不符。"""
        doc = Document(self.source)
        second = doc.add_section(WD_SECTION_START.NEW_PAGE)
        second.orientation = WD_ORIENT.LANDSCAPE
        width, height = second.page_width, second.page_height
        second.page_width, second.page_height = height, width
        second.left_margin = Cm(1.5)
        doc.add_paragraph("第二节正文")
        doc.save(self.source)
        self.document = _parse(self.source, self.base)
        self.assertEqual(len(self.document["sections"]), 2)
        self.assertEqual(self.document["sections"][1]["orientation"], "landscape")
        return self.document

    def _section_issue(self, rule_id, index, expected):
        key = rule_id[len("page."):]
        return {
            "rule_id": rule_id, "category": "page", "severity": "error",
            "location": {"type": "section", "index": index},
            "actual": f'{self.document["sections"][index].get(key)} cm',
            "expected": expected, "message": "页面设置不符合要求",
            "suggestion": "按规范修改页面设置", "source": "rule_engine",
        }

    def test_section_fix_addresses_the_requested_section_only(self):
        """D1 回归：节级修复必须按 location.index 落到那一节，不能一律写第 1 节。"""
        self._two_section_source()
        result = fix_document(self.document, [self._section_issue("page.left_cm", 1, "3.0 cm")])
        self.assertEqual(result["applied_count"], 1)
        self.assertEqual(result["skipped_count"], 0)
        self.assertEqual(
            [item["index"] for item in result["format"]["applied"]], [1],
        )
        self.assertEqual(self.document["sections"][1]["left_cm"], 3.0)
        self.assertNotEqual(self.document["sections"][0]["left_cm"], 3.0)

    def test_multisection_fix_round_trips_through_writeback(self):
        """D1 回归：第 2 节的页面设置在原位回写后必须真的落盘。"""
        self._two_section_source()
        first_left = self.document["sections"][0]["left_cm"]
        issues = [
            self._section_issue("page.left_cm", 1, "3.0 cm"),
            self._section_issue("page.footer_cm", 1, "1.5 cm"),
        ]
        before = copy.deepcopy(self.document)
        fix_document(self.document, issues)
        self.assertAlmostEqual(self.document["sections"][1]["footer_cm"], 1.5, places=2)
        DocumentContract.model_validate(self.document)
        source_ids = {id(block): index for index, block in enumerate(self.document["blocks"])}
        patch_docx(self.source, self.output, before, self.document, source_ids)
        fixed = Document(self.output)
        self.assertAlmostEqual(fixed.sections[1].left_margin.cm, 3.0, places=2)
        self.assertAlmostEqual(fixed.sections[0].left_margin.cm, first_left, places=2)
        self.assertAlmostEqual(fixed.sections[1].footer_distance.cm, 1.5, places=2)

    def test_section_fix_with_unknown_section_index_is_skipped(self):
        """越界的分节下标必须记跳过，而不是静默改写第 1 节。"""
        self._two_section_source()
        snapshot = copy.deepcopy(self.document["sections"])
        issue = self._section_issue("page.left_cm", 1, "3.0 cm")
        issue["location"] = {"type": "section", "index": 7}
        result = fix_document(self.document, [issue])
        self.assertEqual(result["applied_count"], 0)
        self.assertEqual(result["skipped_count"], 1)
        self.assertTrue(
            any("分节下标 7" in item["reason"] for item in result["format"]["skipped"]),
            result["format"]["skipped"],
        )
        self.assertEqual(self.document["sections"], snapshot)

    def test_structural_caption_insert_keeps_original_blocks(self):
        doc = Document(self.source)
        table = doc.add_table(rows=1, cols=1)
        table.cell(0, 0).text = "单元格"
        doc.save(self.source)
        self.document = _parse(self.source, self.base)
        before = copy.deepcopy(self.document)
        source_ids = {id(block): index for index, block in enumerate(self.document["blocks"])}
        caption = {
            "type": "paragraph", "kind": "table_caption", "text": "表 1 测试",
            "alignment": "center", "runs": [{"text": "表 1 测试"}],
        }
        self.document["blocks"].insert(2, caption)
        for index, block in enumerate(self.document["blocks"]):
            block["index"] = index
        DocumentContract.model_validate(self.document)
        patch_docx(self.source, self.output, before, self.document, source_ids)
        fixed = Document(self.output)
        self.assertEqual(fixed.paragraphs[0].text, "正文测试")
        self.assertEqual(fixed.paragraphs[2].text, "表 1 测试")
        self.assertEqual(fixed.tables[0].cell(0, 0).text, "单元格")
        with ZipFile(self.output) as output:
            self.assertIn(b"TOC", output.read("word/document.xml"))

    def test_multisection_structural_change_is_skipped_with_warning(self):
        """W6：多节文档里做结构性变更时只跳过该变更，输出照常产出且原文逐字节保留。"""
        doc = Document(self.source)
        doc.add_section(WD_SECTION_START.NEW_PAGE)
        doc.add_paragraph("第二节正文")
        doc.save(self.source)
        self.document = _parse(self.source, self.base)
        before = copy.deepcopy(self.document)
        source_ids = {id(block): index for index, block in enumerate(self.document["blocks"])}
        new_block = {"type": "paragraph", "text": "插入内容", "runs": [{"text": "插入内容"}]}
        self.document["blocks"].insert(1, new_block)
        for index, block in enumerate(self.document["blocks"]):
            block["index"] = index
        report = patch_docx(self.source, self.output, before, self.document, source_ids)
        self.assertTrue(self.output.exists())
        self.assertFalse(report["changed"])
        self.assertTrue(any("多节" in warning for warning in report["skipped"]), report["skipped"])
        self.assertEqual(self.source.read_bytes(), self.output.read_bytes())


class ContractTests(unittest.TestCase):
    def test_unknown_version_and_malformed_blocks_are_rejected(self):
        valid = {
            "schema_version": "1.0", "sections": [{"index": 0}],
            "blocks": [{"index": 0, "type": "paragraph", "text": "正文", "runs": [{"text": "正文"}]}],
        }
        for invalid in (
            {**valid, "schema_version": "2.0"},
            {**valid, "blocks": [{"index": 3, "type": "paragraph", "text": "正文"}]},
            {**valid, "blocks": [{"index": 0, "type": "unknown"}]},
            {**valid, "blocks": [{"index": 0, "type": "paragraph", "text": "正文", "runs": [42]}]},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    DocumentContract.model_validate(invalid)
        self.assertEqual(DocumentContract.model_validate(valid).schema_version, "1.0")
        legacy = {key: value for key, value in valid.items() if key != "schema_version"}
        self.assertEqual(DocumentContract.model_validate(legacy).schema_version, "1.0")

    def test_check_endpoint_returns_422_for_malformed_document(self):
        client = TestClient(api.app)
        try:
            response = client.post(
                "/api/check",
                json={"college_id": "jlu_cs", "include_semantic": False,
                      "document": {"schema_version": "2.0", "sections": [], "blocks": []}},
            )
        finally:
            client.close()
        self.assertEqual(response.status_code, 422)

    def test_check_and_rebuild_accept_valid_current_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.docx"
            _source(path)
            document = _parse(path, directory)
            client = TestClient(api.app)
            try:
                checked = client.post(
                    "/api/check",
                    json={"college_id": "jlu_cs", "include_semantic": False, "document": document},
                )
                checked_file = client.post(
                    "/api/check-file",
                    data={"college_id": "jlu_cs", "include_semantic": "false"},
                    files={"file": ("paper.docx", path.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                )
                rebuilt = client.post(
                    "/api/rebuild",
                    files={"file": ("paper.docx", path.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                )
            finally:
                client.close()
            self.assertEqual(checked.status_code, 200, checked.text)
            self.assertEqual(checked_file.status_code, 200, checked_file.text)
            self.assertEqual(rebuilt.status_code, 200, rebuilt.text)
            self.assertTrue(Document(io.BytesIO(rebuilt.content)).paragraphs)


if __name__ == "__main__":
    unittest.main()
