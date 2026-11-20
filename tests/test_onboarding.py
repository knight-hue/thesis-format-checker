# -*- coding: utf-8 -*-
"""学院上架流水线（目标 3 · M1）的守护测试。

覆盖三块：
- corpus：两个学院的现役规范文本解析（条目量快照 + roundtrip 恒等）；
- coverage：反向完整性（依据原句零漂移）+ 未归宿清单快照 + decisions 台账机制；
- extract：构造规范文档 → 章/节/条目抽取 → 两个产物文件。

未归宿清单是"每句三选一"门禁的**当前欠账快照**：清单变化必须是有意为之
（补判据或补 decisions），不允许静默变化。
"""
import json
import os
import tempfile
import unittest

from docx import Document

from backend.onboarding.corpus import (HEADER_RE, build_corpus_text, normalize,
                                       parse_corpus, split_fragments)
from backend.onboarding.coverage import render_markdown, run_coverage
from backend.onboarding.extract import extract_docx, write_outputs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_DIR = os.path.join(ROOT, "config", "rules")

# 现役规范文本的条目量快照（改动 corpus 时同步更新——有意的变化才允许）。
CORPUS_ITEM_COUNTS = {"jlu_cs": 98, "jlu_se": 82}

# 未归宿条目快照：行号 -> 片段（当前欠账，拍板后补判据/decisions 再更新）。
# 注："Abstract标题文字：Abstract" 已通过 basis 补行转覆盖（2026-10-14），不在欠账里。
ORPHAN_SNAPSHOT = {
    "jlu_cs": {
        75: "注意事项标志：【注意】",
        89: "图所在页空白不够时",
        90: "图采用照片时的要求",
        91: "图采用照片时的质量",
        92: "图采用照片时的相纸",
        93: "金相显微组织照片",
        130: "电子文献类型标识",
        133: "连续出版物著录格式",
        134: "专著著录格式",
        135: "会议论文集著录格式",
        136: "学位论文著录格式",
        137: "报告著录格式",
        138: "专利文献著录格式",
        139: "标准著录格式",
        140: "报纸文章著录格式",
        141: "电子文献著录格式",
        142: "外国作者姓名",
        151: "致谢内容",
    },
    "jlu_se": {
        79: "注意事项标志：【注意】",
        93: "图所在页空白不够时",
        94: "图采用照片时的要求",
        95: "图采用照片时的质量",
        96: "图采用照片时的相纸",
        97: "金相显微组织照片",
        133: "致谢内容",
    },
}


class CorpusFormatTest(unittest.TestCase):
    """规范文本解析：条目台账 + roundtrip 恒等（解析不动内容）。"""

    def test_parse_and_roundtrip_identity(self):
        for college, count in CORPUS_ITEM_COUNTS.items():
            path = os.path.join(RULES_DIR, "jlu", college.split("_")[1],
                                f"{college}.txt")
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            document = parse_corpus(text)
            with self.subTest(college=college):
                self.assertEqual(count, len(document.items))
                self.assertEqual(text, document.render())
                self.assertGreaterEqual(len(document.headers), 10)
                for header in document.headers:
                    self.assertRegex(header, HEADER_RE)

    def test_items_carry_chapter_and_section(self):
        path = os.path.join(RULES_DIR, "jlu", "cs", "jlu_cs.txt")
        with open(path, encoding="utf-8") as handle:
            document = parse_corpus(handle.read())
        first = document.items[0]
        self.assertEqual("第1章 页面设置", first.chapter)
        self.assertEqual("1.1 页边距", first.section)
        self.assertTrue(first.text.startswith("左页边距"))

    def test_normalize_and_split_pieces(self):
        # 归一化：去全部空白（含全角）与尾部句点
        self.assertEqual("左页边距：3厘米", normalize(" 左页边距 ：3厘米。 "))
        # 依据值按"；"拆段（实证 page. 把六条页边距拼成一串）
        fragments = split_fragments("左页边距：3厘米；右页边距：3厘米")
        self.assertEqual(["左页边距：3厘米", "右页边距：3厘米"], fragments)

    def test_build_corpus_text_renders_groups(self):
        text = build_corpus_text([
            {"chapter": "第1章 页面设置", "section": "1.1 页边距",
             "items": ["左页边距：3厘米"]},
            {"chapter": "第1章 页面设置", "section": "",
             "items": ["无节条目"]},
        ])
        self.assertIn("【第1章 页面设置 / 1.1 页边距】\n左页边距：3厘米", text)
        self.assertIn("【第1章 页面设置】", text)  # 章/节不完整时的单栏标题


class CoverageTest(unittest.TestCase):
    """覆盖率对账：反向零漂移 + 未归宿快照 + decisions 机制。"""

    def test_basis_citations_all_resolve(self):
        """反方向完整性：依据表引用的原句必须全部能在规范文本命中（当前零漂移）。"""
        for college in CORPUS_ITEM_COUNTS:
            report = run_coverage(college, rules_dir=RULES_DIR)
            with self.subTest(college=college):
                self.assertEqual([], report["unresolved_basis"])

    def test_orphan_items_match_snapshot(self):
        """未归宿欠账与快照一致（有意的变化才允许更新快照）。"""
        for college, expected in ORPHAN_SNAPSHOT.items():
            report = run_coverage(college, decisions={}, rules_dir=RULES_DIR)
            actual = {item["line_no"]: item["text"][:24]
                      for item in report["orphan"]}
            with self.subTest(college=college):
                self.assertEqual(len(expected), len(actual))
                for line_no, fragment in expected.items():
                    self.assertIn(line_no, actual)
                    self.assertIn(fragment, actual[line_no])

    def test_decisions_ledger_accounts_orphans_and_opens_gate(self):
        """config 里的决策台账把两院未归宿全部认领（自动读取），门禁转绿。"""
        for college in CORPUS_ITEM_COUNTS:
            raw = run_coverage(college, decisions={}, rules_dir=RULES_DIR)
            healed = run_coverage(college, rules_dir=RULES_DIR)  # 自动读台账
            with self.subTest(college=college):
                self.assertFalse(raw["gate_passed"])
                self.assertTrue(healed["gate_passed"])
                self.assertEqual([], healed["orphan"])
                self.assertEqual(len(raw["orphan"]), len(healed["excluded"]))
                self.assertTrue(healed["decisions_source"].endswith(
                    f"{college}_decisions.json"))

    def test_en_abstract_title_text_citation_is_covered(self):
        """补账守卫：'Abstract标题文字：Abstract' 由 en_abstract.title_text 覆盖。"""
        for college, line_no in (("jlu_cs", 25), ("jlu_se", 29)):
            report = run_coverage(college, decisions={}, rules_dir=RULES_DIR)
            by_line = {item["line_no"]: item for item in report["covered"]}
            with self.subTest(college=college):
                self.assertIn("en_abstract.title_text", by_line[line_no]["rules"])

    def test_markdown_report_lists_orphans(self):
        report = run_coverage("jlu_se", decisions={}, rules_dir=RULES_DIR)
        text = render_markdown(report)
        self.assertIn("未归宿条目", text)
        # 补账后 Abstract 已转覆盖；选仍在欠账里的条目做守卫。
        self.assertIn("注意事项标志", text)


class ExtractTest(unittest.TestCase):
    """规范文档 → 条目台账 + corpus 草案（构造输入；真实材料到位后走回放）。"""

    def _build_docx(self, path):
        document = Document()
        document.add_paragraph("第1章 页面设置", style="Heading 1")
        document.add_paragraph("1.1 页边距", style="Heading 2")
        document.add_paragraph("左页边距：3厘米")
        document.add_paragraph("右页边距：3厘米")
        document.add_paragraph("1.1.1 补充说明", style="Heading 3")  # 三级按条目保留
        document.add_paragraph("1.2 页眉", style="Heading 2")
        document.add_paragraph("页眉边距：1.5厘米")
        document.save(path)

    def test_extract_structure_and_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            docx_path = os.path.join(tmp, "spec.docx")
            self._build_docx(docx_path)
            result = extract_docx(docx_path)
            self.assertEqual([], result["warnings"])
            self.assertEqual(2, len(result["sections"]))
            first, second = result["sections"]
            self.assertEqual("第1章 页面设置", first["chapter"])
            self.assertEqual("1.1 页边距", first["section"])
            self.assertEqual(["左页边距：3厘米", "右页边距：3厘米", "1.1.1 补充说明"],
                             [item["text"] for item in first["items"]])
            self.assertEqual("1.2 页眉", second["section"])
            files = write_outputs(result, os.path.join(tmp, "out"))
            with open(files["spec_items"], encoding="utf-8") as handle:
                spec = json.load(handle)
            self.assertEqual(4, spec["items_total"])
            with open(files["corpus"], encoding="utf-8") as handle:
                corpus = handle.read()
            self.assertIn("【第1章 页面设置 / 1.1 页边距】", corpus)
            self.assertIn("左页边距：3厘米", corpus)

    def test_chapter_without_section_warns_and_renders_single_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            docx_path = os.path.join(tmp, "spec2.docx")
            document = Document()
            document.add_paragraph("第1章 总则", style="Heading 1")
            document.add_paragraph("统一要求：全文用宋体")
            document.save(docx_path)
            result = extract_docx(docx_path)
            self.assertTrue(any("章/节不完整" in w for w in result["warnings"]))
            files = write_outputs(result, os.path.join(tmp, "out2"))
            with open(files["corpus"], encoding="utf-8") as handle:
                corpus = handle.read()
            self.assertIn("【第1章 总则】", corpus)


if __name__ == "__main__":
    unittest.main()
