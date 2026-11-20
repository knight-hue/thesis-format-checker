# -*- coding: utf-8 -*-
"""「公式独占一行、编号另起一行」的解析与判定。

这种排版在 Word 里很常见，但公式行本身不含编号，只看单段认不出是公式，
会掉进正文条款被判（要求两端对齐、首行缩进 2 字符），而 jlu_cs.txt 6.1
只要求公式居中、编号置于公式右边行末。这里把解析口径与判定钉下来：
公式行按公式判（居中），编号行不再套正文条款，编号没跟公式同一行则报
formula.number_position 一条。
"""
import json
import os
import tempfile
import unittest

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

from backend.detection.rule_engine import RuleEngine
from backend.fix.fix_blocks import fix_document
from backend.parser.docx_to_json import docx_to_json

from backend.college_registry import path_of

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_DIR = os.path.join(ROOT, "config", "rules")


def _rules(college_id="jlu_cs"):
    with open(path_of(college_id, "rules", RULES_DIR), encoding="utf-8") as handle:
        return json.load(handle)


def _parse(build):
    """build(doc) 往空文档里写内容；返回解析后的 document 字典。"""
    with tempfile.TemporaryDirectory() as tmp:
        source = os.path.join(tmp, "case.docx")
        out = os.path.join(tmp, "out")
        doc = Document()
        build(doc)
        doc.save(source)
        with open(docx_to_json(source, out), encoding="utf-8") as handle:
            return json.load(handle)


def _kinds(document):
    return [block.get("kind") for block in document["blocks"]]


def _rule_ids(document, rules=None):
    engine = RuleEngine(rules or _rules())
    return [issue["rule_id"] for issue in engine.check(document)["issues"]]


class FormulaOwnLineTest(unittest.TestCase):
    def _split_formula(self, doc):
        para = doc.add_paragraph("y = ax + b")
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        number = doc.add_paragraph("(2-1)")
        number.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    def test_formula_and_number_lines_are_tagged(self):
        document = _parse(self._split_formula)
        self.assertEqual(_kinds(document), ["formula", "formula_number"])
        formula = document["blocks"][0]
        self.assertEqual(formula["formula_type"], "text")
        self.assertEqual(formula["formula_text"], "y = ax + b")
        self.assertEqual(formula["formula_number"], "(2-1)")

    def test_body_clauses_are_not_applied_to_formula_lines(self):
        ids = _rule_ids(_parse(self._split_formula))
        self.assertNotIn("body.alignment", ids)
        self.assertNotIn("body.first_line_indent", ids)

    def test_number_on_its_own_line_reports_number_position(self):
        ids = _rule_ids(_parse(self._split_formula))
        self.assertIn("formula.number_position", ids)
        # 公式居中、编号格式都没问题，唯一的毛病是编号没跟公式同一行。
        self.assertNotIn("formula.alignment", ids)
        self.assertNotIn("formula.number_format", ids)

    def test_short_sentence_before_number_is_not_a_formula(self):
        def build(doc):
            doc.add_paragraph("其中系数 a = 3。")
            doc.add_paragraph("(2-1)")

        document = _parse(build)
        self.assertNotIn("formula", _kinds(document))
        self.assertNotIn("formula.number_position", _rule_ids(document))

    def test_number_in_same_line_still_matches(self):
        def build(doc):
            para = doc.add_paragraph("E=mc^2（1-1）")
            para.alignment = WD_ALIGN_PARAGRAPH.CENTER

        document = _parse(build)
        self.assertEqual(document["blocks"][0]["kind"], "formula")
        self.assertEqual(document["blocks"][0]["formula_number"], "（1-1）")
        self.assertNotIn("formula.number_position", _rule_ids(document))

    def test_number_line_without_preceding_formula_stays_untouched(self):
        def build(doc):
            doc.add_paragraph("正文段落。")
            doc.add_paragraph("(2-1)")

        document = _parse(build)
        self.assertNotIn("formula_number", _kinds(document))

    def test_autofix_reports_manual_merge_instead_of_silently_skipping(self):
        """编号独占一行时自动修复无法只改公式段，必须如实说明要手动处理。"""
        document = _parse(self._split_formula)
        issues = [{**issue, "source": "rule_engine"}
                  for issue in RuleEngine(_rules()).check(document)["issues"]]
        result = fix_document(document, issues)["format"]
        self.assertEqual([item["rule_id"] for item in result["applied"]
                          if item["rule_id"].startswith("formula.")], [])
        skipped = {item["rule_id"]: item["reason"] for item in result["skipped"]}
        self.assertIn("编号独占一行", skipped.get("formula.number_position", ""))
        self.assertEqual(document["blocks"][0]["text"], "y = ax + b")


if __name__ == "__main__":
    unittest.main()
