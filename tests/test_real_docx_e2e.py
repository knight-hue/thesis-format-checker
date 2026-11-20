import io
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from zipfile import ZipFile

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from fastapi.testclient import TestClient

import backend.api as api
from backend.document_contract import DocumentContract
from backend.parser import docx_to_json


DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _append_field(paragraph, instruction, result):
    """Append a real Word complex field without flattening it to plain text."""
    begin_run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    begin_run._r.append(begin)

    instruction_run = paragraph.add_run()
    instruction_node = OxmlElement("w:instrText")
    instruction_node.set(qn("xml:space"), "preserve")
    instruction_node.text = f" {instruction} "
    instruction_run._r.append(instruction_node)

    separate_run = paragraph.add_run()
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    separate_run._r.append(separate)
    paragraph.add_run(result)

    end_run = paragraph.add_run()
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    end_run._r.append(end)


def _png():
    """Return a valid 1x1 RGB PNG without adding a test dependency."""
    def chunk(kind, data):
        checksum = zlib.crc32(kind + data)
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x20\x60\xa0"))
        + chunk(b"IEND", b"")
    )


def _build_realistic_paper(path):
    """Build a small but genuine DOCX package with preservation-sensitive parts."""
    document = Document()
    section = document.sections[0]
    # Keep one section-level defect so the test can exercise a cross-layer fix.
    section.left_margin = Cm(2)
    section.right_margin = Cm(3)
    section.top_margin = Cm(2)
    section.bottom_margin = Cm(2)
    section.header_distance = Cm(1.5)
    section.footer_distance = Cm(1.5)
    section.header.paragraphs[0].text = "计算机科学与技术学院"
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _append_field(footer, "PAGE", "1")

    custom_style = document.styles.add_style("ProjectBody", WD_STYLE_TYPE.PARAGRAPH)
    custom_style.font.name = "Times New Roman"
    custom_style.font.size = Pt(12)
    custom_style.element.get_or_add_rPr().get_or_add_rFonts().set(
        qn("w:eastAsia"), "宋体"
    )

    title = document.add_paragraph("第1章 绪论", style="Heading 1")
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    toc = document.add_paragraph()
    _append_field(toc, 'TOC \\o "1-3" \\h \\z \\u', "1.1 研究背景 …… 1")

    body = document.add_paragraph(style="ProjectBody")
    body.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    body.paragraph_format.first_line_indent = Pt(24)
    body.paragraph_format.line_spacing = 1.5
    run = body.add_run("这是用于端到端验收的正文段落，其中只有字号被故意设置错误。")
    run.font.name = "Times New Roman"
    run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")
    run.font.size = Pt(9)

    document.add_picture(io.BytesIO(_png()), width=Cm(1))
    caption = document.add_paragraph("图 1-1 验收图片")
    caption.alignment = WD_ALIGN_PARAGRAPH.CENTER

    table_caption = document.add_paragraph("表 1-1 验收数据")
    table_caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
    table = document.add_table(rows=2, cols=2)
    table.style = "Table Grid"
    for row_index, values in enumerate((("指标", "数值"), ("准确率", "98.2%"))):
        for col_index, value in enumerate(values):
            cell = table.cell(row_index, col_index)
            cell.text = ""
            cell_run = cell.paragraphs[0].add_run(value)
            cell_run.font.name = "Times New Roman"
            cell_run._element.get_or_add_rPr().get_or_add_rFonts().set(
                qn("w:eastAsia"), "宋体"
            )
            # Only the first cell is deliberately wrong; the other cells follow the rule.
            cell_run.font.size = Pt(8 if (row_index, col_index) == (0, 0) else 10.5)
    document.save(path)


def _package_snapshot(payload):
    with ZipFile(io.BytesIO(payload)) as package:
        return {name: package.read(name) for name in package.namelist()}


class RealDocumentEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.source = Path(self.temp.name) / "realistic-paper.docx"
        _build_realistic_paper(self.source)
        self.source_bytes = self.source.read_bytes()
        self.client = TestClient(api.app)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def _check(self, payload):
        return self.client.post(
            "/api/check-file",
            data={"college_id": "jlu_cs", "include_semantic": "false"},
            files={"file": ("paper.docx", payload, DOCX_CONTENT_TYPE)},
        )

    @staticmethod
    def _issue(issues, rule_id, **location):
        return next(
            issue
            for issue in issues
            if issue["rule_id"] == rule_id
            and all(issue["location"].get(key) == value for key, value in location.items())
        )

    def test_detect_fix_reparse_and_recheck_real_docx(self):
        parsed_dir = Path(self.temp.name) / "parsed-before"
        with open(
            docx_to_json(str(self.source), output_dir=str(parsed_dir)), encoding="utf-8"
        ) as stream:
            parsed_before = json.load(stream)
        DocumentContract.model_validate(parsed_before)

        body_index = next(
            index
            for index, block in enumerate(parsed_before["blocks"])
            if block.get("text", "").startswith("这是用于端到端验收")
        )
        self.assertEqual(parsed_before["blocks"][body_index]["kind"], "body")

        checked = self._check(self.source_bytes)
        self.assertEqual(checked.status_code, 200, checked.text)
        issues_before = checked.json()["issues"]
        target = self._issue(
            issues_before, "body.font_size", index=body_index, run=0
        )
        self.assertTrue(target["fixable"])

        fixed = self.client.post(
            "/api/fix",
            data={"college_id": "jlu_cs", "issues_json": json.dumps([target])},
            files={"file": ("paper.docx", self.source_bytes, DOCX_CONTENT_TYPE)},
        )
        self.assertEqual(fixed.status_code, 200, fixed.text)
        self.assertEqual(fixed.headers["X-Applied-Count"], "1")
        self.assertEqual(fixed.headers["X-Skipped-Count"], "0")
        self.assertEqual(fixed.headers["X-Fixed-Indexes"], "0")
        self.assertEqual(fixed.headers.get("X-Writeback-Skip-Count", "0"), "0")

        reopened = Document(io.BytesIO(fixed.content))
        fixed_body = next(
            paragraph
            for paragraph in reopened.paragraphs
            if paragraph.text.startswith("这是用于端到端验收")
        )
        self.assertAlmostEqual(fixed_body.runs[0].font.size.pt, 12.0, places=2)
        self.assertEqual(
            reopened.sections[0].header.paragraphs[0].text,
            "计算机科学与技术学院",
        )
        self.assertEqual(reopened.paragraphs[2].style.name, "ProjectBody")
        self.assertEqual(len(reopened.inline_shapes), 1)

        original_package = _package_snapshot(self.source_bytes)
        fixed_package = _package_snapshot(fixed.content)
        self.assertEqual(set(original_package), set(fixed_package))
        for part_name, content in original_package.items():
            if part_name != "word/document.xml":
                self.assertEqual(content, fixed_package[part_name], part_name)
        self.assertIn(b"TOC", fixed_package["word/document.xml"])
        self.assertIn(b"fldChar", fixed_package["word/document.xml"])

        fixed_path = Path(self.temp.name) / "fixed-paper.docx"
        fixed_path.write_bytes(fixed.content)
        parsed_after_dir = Path(self.temp.name) / "parsed-after"
        with open(
            docx_to_json(str(fixed_path), output_dir=str(parsed_after_dir)), encoding="utf-8"
        ) as stream:
            parsed_after = json.load(stream)
        DocumentContract.model_validate(parsed_after)
        self.assertEqual(len(parsed_before["blocks"]), len(parsed_after["blocks"]))
        self.assertEqual(
            [block.get("text") for block in parsed_before["blocks"]],
            [block.get("text") for block in parsed_after["blocks"]],
        )

        rechecked = self._check(fixed.content)
        self.assertEqual(rechecked.status_code, 200, rechecked.text)
        issues_after = rechecked.json()["issues"]
        self.assertFalse(
            any(
                issue["rule_id"] == "body.font_size"
                and issue["location"]["index"] == body_index
                and issue["location"].get("run") == 0
                for issue in issues_after
            ),
            issues_after,
        )

    def test_fix_multiple_rule_layers_in_one_request(self):
        parsed_dir = Path(self.temp.name) / "parsed-multiple"
        with open(
            docx_to_json(str(self.source), output_dir=str(parsed_dir)), encoding="utf-8"
        ) as stream:
            document = json.load(stream)

        body_index = next(
            index
            for index, block in enumerate(document["blocks"])
            if block.get("text", "").startswith("这是用于端到端验收")
        )
        table_index = next(
            index for index, block in enumerate(document["blocks"])
            if block.get("type") == "table"
        )

        checked = self._check(self.source_bytes)
        self.assertEqual(checked.status_code, 200, checked.text)
        issues_before = checked.json()["issues"]
        selected = [
            self._issue(issues_before, "page.left_cm", index=0),
            self._issue(issues_before, "body.font_size", index=body_index, run=0),
            self._issue(
                issues_before,
                "table.cell_font_size",
                index=table_index,
                row=0,
                col=0,
            ),
        ]
        self.assertTrue(all(issue["fixable"] for issue in selected), selected)

        fixed = self.client.post(
            "/api/fix",
            data={"college_id": "jlu_cs", "issues_json": json.dumps(selected)},
            files={"file": ("paper.docx", self.source_bytes, DOCX_CONTENT_TYPE)},
        )
        self.assertEqual(fixed.status_code, 200, fixed.text)
        self.assertEqual(fixed.headers["X-Applied-Count"], "3")
        self.assertEqual(fixed.headers["X-Skipped-Count"], "0")
        self.assertEqual(fixed.headers["X-Fixed-Indexes"], "0,1,2")
        self.assertEqual(fixed.headers.get("X-Writeback-Skip-Count", "0"), "0")

        reopened = Document(io.BytesIO(fixed.content))
        self.assertAlmostEqual(reopened.sections[0].left_margin.cm, 3.0, places=2)
        fixed_body = next(
            paragraph
            for paragraph in reopened.paragraphs
            if paragraph.text.startswith("这是用于端到端验收")
        )
        self.assertAlmostEqual(fixed_body.runs[0].font.size.pt, 12.0, places=2)
        self.assertAlmostEqual(
            reopened.tables[0].cell(0, 0).paragraphs[0].runs[0].font.size.pt,
            10.5,
            places=2,
        )

        rechecked = self._check(fixed.content)
        self.assertEqual(rechecked.status_code, 200, rechecked.text)
        issues_after = rechecked.json()["issues"]
        repaired_targets = {
            ("page.left_cm", 0, None, None, None),
            ("body.font_size", body_index, 0, None, None),
            ("table.cell_font_size", table_index, None, 0, 0),
        }
        remaining_targets = {
            (
                issue["rule_id"],
                issue["location"].get("index"),
                issue["location"].get("run"),
                issue["location"].get("row"),
                issue["location"].get("col"),
            )
            for issue in issues_after
        }
        self.assertTrue(repaired_targets.isdisjoint(remaining_targets), issues_after)


if __name__ == "__main__":
    unittest.main()
