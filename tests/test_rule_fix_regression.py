import io
import json
import unittest

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from fastapi.testclient import TestClient

import backend.api as api


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _set_run_font(run, latin="Times New Roman", east_asia="宋体", size=12):
    run.font.name = latin
    run.font.size = Pt(size)
    fonts = run._element.get_or_add_rPr().get_or_add_rFonts()
    fonts.set(qn("w:ascii"), latin)
    fonts.set(qn("w:hAnsi"), latin)
    fonts.set(qn("w:eastAsia"), east_asia)


def _base_document(body_text="正文测试", body_size=12):
    document = Document()
    section = document.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.left_margin = Cm(3)
    section.right_margin = Cm(3)
    section.top_margin = Cm(2)
    section.bottom_margin = Cm(2)
    section.header_distance = Cm(1.5)
    section.footer_distance = Cm(1.5)
    section.header.paragraphs[0].text = "需保留的页眉"

    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    paragraph.paragraph_format.first_line_indent = Pt(24)
    paragraph.paragraph_format.line_spacing = 1.5
    run = paragraph.add_run(body_text)
    _set_run_font(run, size=body_size)
    return document


def _bytes(document):
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


class RuleFixRegressionTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(api.app)

    def tearDown(self):
        self.client.close()

    def _check(self, content, college_id="jlu_cs"):
        response = self.client.post(
            "/api/check-file",
            data={"college_id": college_id, "include_semantic": "false"},
            files={"file": ("paper.docx", content, DOCX_MIME)},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _issues(self, result, rule_id):
        return [issue for issue in result["issues"] if issue["rule_id"] == rule_id]

    def _only_issue(self, result, rule_id):
        issues = self._issues(result, rule_id)
        self.assertEqual(len(issues), 1, (rule_id, result["issues"]))
        return issues[0]

    def _fix(self, content, issue, college_id="jlu_cs"):
        response = self.client.post(
            "/api/fix",
            data={"college_id": college_id, "issues_json": json.dumps([issue], ensure_ascii=False)},
            files={"file": ("paper.docx", content, DOCX_MIME)},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["X-Applied-Count"], "1")
        self.assertEqual(response.headers["X-Skipped-Count"], "0")
        self.assertEqual(response.headers["X-Fixed-Indexes"], "0")
        return response.content

    def test_page_left_margin_detect_fix_recheck(self):
        document = _base_document()
        document.sections[0].left_margin = Cm(1)
        original = _bytes(document)

        issue = self._only_issue(self._check(original), "page.left_cm")
        # issue 经 API 序列化后 location 总带 row/col/run/kinds/text_preview 这些
        # 固定键，整字典相等永远不成立；只断言定位到第 0 节。
        self.assertEqual(issue["location"]["type"], "section")
        self.assertEqual(issue["location"]["index"], 0)
        self.assertTrue(issue["fixable"])

        fixed = self._fix(original, issue)
        self.assertFalse(self._issues(self._check(fixed), "page.left_cm"))
        result = Document(io.BytesIO(fixed))
        self.assertAlmostEqual(result.sections[0].left_margin.cm, 3.0, places=1)
        self.assertEqual(result.sections[0].header.paragraphs[0].text, "需保留的页眉")

    def test_body_font_size_detect_fix_recheck_for_both_colleges(self):
        original = _bytes(_base_document(body_size=9))
        for college_id in ("jlu_cs", "jlu_se"):
            with self.subTest(college_id=college_id):
                issue = self._only_issue(self._check(original, college_id), "body.font_size")
                self.assertEqual(issue["location"]["type"], "paragraph")
                self.assertEqual(issue["location"]["index"], 0)
                self.assertEqual(issue["location"]["run"], 0)
                self.assertTrue(issue["fixable"])

                fixed = self._fix(original, issue, college_id)
                self.assertFalse(self._issues(self._check(fixed, college_id), "body.font_size"))
                self.assertAlmostEqual(Document(io.BytesIO(fixed)).paragraphs[0].runs[0].font.size.pt, 12)

    def _table_document(self):
        document = _base_document()
        caption = document.add_paragraph("表1-1 测试数据")
        caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _set_run_font(caption.runs[0], latin="Times New Roman", east_asia="黑体", size=10.5)
        table = document.add_table(rows=1, cols=1)
        cell_run = table.cell(0, 0).paragraphs[0].add_run("数据A1")
        _set_run_font(cell_run, latin="Arial", east_asia="黑体", size=9)
        return _bytes(document)

    def test_table_cell_font_size_detect_fix_recheck(self):
        original = self._table_document()

        result = self._check(original)
        # table.cell_latin_font 已按模板依据撤销：图表那一章只写了
        # “表内字体字号：宋体五号字”（中文 + 字号）；“西文、数字：Times New Roman”
        # 在 §4.3 正文那一节，外推到“表内”是我们自己加的。这里守住“不得复活”。
        self.assertEqual(self._issues(result, "table.cell_latin_font"), [])

        issue = self._only_issue(result, "table.cell_font_size")
        self.assertEqual(issue["location"]["type"], "table_cell")
        self.assertEqual(issue["location"]["row"], 0)
        self.assertEqual(issue["location"]["col"], 0)
        self.assertTrue(issue["fixable"])

        response = self.client.post(
            "/api/fix",
            data={"college_id": "jlu_cs", "issues_json": json.dumps([issue], ensure_ascii=False)},
            files={"file": ("paper.docx", original, DOCX_MIME)},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["X-Applied-Count"], "1")
        self.assertEqual(response.headers["X-Skipped-Count"], "0")
        fixed_result = self._check(response.content)
        self.assertFalse(self._issues(fixed_result, "table.cell_font_size"))
        self.assertEqual(self._issues(fixed_result, "table.cell_latin_font"), [])

    def test_table_cell_east_asia_font_is_detected(self):
        original = self._table_document()
        result = self._check(original)
        issue = self._only_issue(result, "table.cell_east_asia_font")
        self.assertEqual(
            {k: issue['location'][k] for k in ('type', 'index', 'row', 'col')},
            {"type": "table_cell", "index": 2, "row": 0, "col": 0},
        )
        self.assertTrue(issue["fixable"])
        fixed = self._fix(original, issue)
        self.assertFalse(self._issues(self._check(fixed), 'table.cell_east_asia_font'))

    def test_fullwidth_punctuation_is_detected_and_fixable_by_text_layer(self):
        original = _bytes(_base_document(body_text="中文,标点"))
        issue = self._only_issue(self._check(original), "body.fullwidth_punct")
        self.assertEqual(issue["location"]["type"], "paragraph")
        # 文字层有确定性修复器（rag/text_rewrite_service._DETERMINISTIC_FIXERS），
        # fix/_core._TEXT_LAYER_RULE_IDS 也把它交给文字层，所以标点写进报告时就是
        # 可修的（仍要用户在结果页勾选后经 /api/fix 才会改）。
        self.assertTrue(issue["fixable"])
        self.assertFalse(issue["fix_reason"])

        response = self.client.post(
            "/api/fix",
            data={"college_id": "jlu_cs", "issues_json": json.dumps([issue], ensure_ascii=False)},
            files={"file": ("paper.docx", original, DOCX_MIME)},
        )
        self.assertEqual(response.status_code, 200, response.text)
        # 格式层对文字类规则会记一条“交由文字改写层处理”的跳过记录，真正改写发生在
        # 文字层，所以这里断言“应用 1 条”而不是“跳过 0 条”。
        self.assertEqual(response.headers["X-Applied-Count"], "1")
        fixed = response.content
        self.assertFalse(self._issues(self._check(fixed), "body.fullwidth_punct"))
        texts = [paragraph.text for paragraph in Document(io.BytesIO(fixed)).paragraphs]
        self.assertIn("中文，标点", texts)


if __name__ == "__main__":
    unittest.main()
