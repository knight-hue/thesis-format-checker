# -*- coding: utf-8 -*-
"""检测输出的两件事：每条附模板依据，且依据必须是模板原句的转录。

依据表放在 `config/rules/<学校>/<学院>/<college>_basis.json`：键是规则号（或规则号前缀家族），
值是从同目录的 `<college>.txt` 规范原文摘下来的原句。判定配置（`*_rules.json`）保持纯净，
依据文案单独一份，改文案不影响判定。
"""
import json
import os
import unittest

from backend.detection.rule_engine import RuleEngine

from backend.college_registry import path_of

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_DIR = os.path.join(ROOT, "config", "rules")
COLLEGES = ("jlu_cs", "jlu_se")

# 两院真实语料（samples/ 共 22 份）上实际出现过的规则号，冻结在这里：
# 以后新增规则号必须同时补依据，否则这里红。
OBSERVED = {
    "jlu_cs": (
        "abstract.body.font", "abstract.body.font_size", "abstract.title.font",
        "abstract.title.font_size", "acknowledgement.title_text",
        "body.east_asia_font", "body.font_size", "body.fullwidth_punct",
        "body.latin_font", "caption.number_space", "en_abstract.body.font_size",
        "en_abstract.title.font_size", "en_keyword.body.font_size",
        "en_keyword.count", "en_keyword.separator", "en_keyword.title.font_size",
        "en_keyword.title_label", "figure.caption.alignment", "figure.caption.font",
        "figure.caption.font_size",
        "figure.caption_number", "figure.caption_position", "figure.placement",
        "formula.alignment", "formula.number_continuity", "formula.number_position",
        "heading.heading_1.alignment", "heading.heading_1.font",
        "heading.heading_1.font_size", "heading.heading_1.number",
        "heading.heading_2.alignment", "heading.heading_2.font",
        "heading.heading_2.font_size", "heading.heading_2.number",
        "heading.heading_3.font", "heading.heading_3.number",
        "keyword.body.font", "keyword.body.font_size", "keyword.count",
        "keyword.separator", "keyword.title.font", "keyword.title.font_size",
        "keyword.title_label", "list.numbering", "list_item.number_format",
        "numerals", "page.footer_cm", "page.header_cm",
        "reference.citation_order", "reference.entry_font_size",
        "reference.entry_period", "table.caption.alignment", "table.caption.font",
        "table.caption.font_size", "table.caption_number",
        "table.cell_font_size",
        "table.continued_note_alignment", "table.continued_note_header",
        "title.font", "title.font_size",
    ),
    "jlu_se": (
        "acknowledgement.content", "acknowledgement.title_text",
        "body.east_asia_font", "body.font_size", "body.fullwidth_punct",
        "body.latin_font", "body.line_spacing", "caption.number_space",
        "en_abstract.body.font", "en_abstract.body.font_size",
        "en_abstract.body.line_spacing", "en_keyword.separator",
        "en_keyword.space_before", "en_keyword.title.font_size",
        "en_keyword.title_label", "figure.caption.alignment", "figure.caption.font",
        "figure.caption_number",
        "figure.caption_position", "figure.placement", "formula.alignment",
        "heading.heading_1.font_size", "heading.heading_1.number",
        "heading.heading_2.alignment", "heading.heading_2.font",
        "heading.heading_2.font_size", "keyword.separator", "keyword.space_before",
        "keyword.title.font_size", "keyword.title_label", "list.numbering",
        "list_item.number_format", "note.font", "note.font_size", "note.prefix",
        "note.space_before", "numerals", "page.bottom_cm", "page.footer_cm",
        "page.left_cm", "page.right_cm", "page.top_cm",
        "reference.entry_font_size", "table.caption.alignment",
        "table.caption_number", "table.cell_east_asia_font",
        "table.cell_font_size",
        "table.continued_note_alignment", "table.continued_note_header",
        "table.continued_note_in_caption",
        "title.font_size",
    ),
}

# 软院模板的参考文献部分只有“字体字号”一句，没有类型标识/著录格式说明，
# 因此这条语义规则对软院是关着的（同顺序编码制）。
SE_LACKS = ("reference.format",)


def basis_of(college):
    with open(path_of(college, "basis", RULES_DIR),
              encoding="utf-8") as handle:
        return json.load(handle)


def paragraph(index, kind, text, **extra):
    block = {
        "type": "paragraph", "index": index, "kind": kind, "text": text,
        "runs": [{"text": text, "font_name": "宋体", "east_asia_font": "宋体",
                  "font_size_pt": 12, "bold": False}],
    }
    block.update(extra)
    return block


class IssueBasisTest(unittest.TestCase):
    """每条报告都要能说出模板依据；依据必须是 txt 里真实存在的原句。"""

    def test_basis_files_exist_and_are_not_empty(self):
        for college in COLLEGES:
            with self.subTest(college=college):
                self.assertTrue(basis_of(college))

    def test_every_observed_rule_id_has_a_basis(self):
        for college, rule_ids in OBSERVED.items():
            engine = RuleEngine.for_college(college, rules_dir=RULES_DIR)
            for rule_id in rule_ids:
                with self.subTest(college=college, rule_id=rule_id):
                    self.assertTrue(engine.basis_of(rule_id),
                                    f"{rule_id} 没有模板依据")

    def test_soft_college_lacks_the_reference_format_basis(self):
        for rule_id in SE_LACKS:
            with self.subTest(rule_id=rule_id):
                self.assertEqual("", RuleEngine.for_college(
                    "jlu_se", rules_dir=RULES_DIR).basis_of(rule_id))

    def test_basis_text_is_quoted_from_the_rules_txt(self):
        """依据的每一段都必须在规范原文里原样出现，不能自己编。"""
        for college in COLLEGES:
            with open(path_of(college, "corpus", RULES_DIR),
                      encoding="utf-8") as handle:
                txt = handle.read()
            for prefix, text in basis_of(college).items():
                for piece in text.split("；"):
                    if not piece:
                        continue
                    with self.subTest(college=college, prefix=prefix):
                        self.assertIn(piece, txt)

    def test_longest_prefix_wins(self):
        """家族前缀与精确规则号同时命中时，取更具体的那条。"""
        engine = RuleEngine.for_college("jlu_cs", rules_dir=RULES_DIR)
        general = engine.basis_of("note.prefix")
        specific = engine.basis_of("note.font_size")
        self.assertTrue(general and specific)
        self.assertNotEqual(general, specific)

    def test_semantic_issue_gets_the_template_sentence_as_basis(self):
        """语义层的报告也要带模板原句：它原来的“确定性检查：…”挪到 basis_detail。"""
        from backend.detection.checker_service import check_document

        block = paragraph(0, "acknowledgement_body", "感谢导师从 2020 年 9 月到 2021 年 6 月的指导。")
        result = check_document({"sections": [], "blocks": [block]}, "jlu_se")
        issues = [i for i in result["issues"]
                  if i["rule_id"] == "acknowledgement.content"]
        self.assertTrue(issues, [i["rule_id"] for i in result["issues"]])
        issue = issues[0]
        self.assertTrue(issue["basis"].startswith("致谢"),
                        issue["basis"])
        self.assertEqual("确定性检查：时间段正则匹配", issue.get("basis_detail"))
        self.assertTrue(issue["basis"].split("；")[0] not in ("", "确定性检查"))

    def test_every_issue_of_a_check_run_quotes_the_basis_table(self):
        """端到端：一次检测里每条报告的 basis 都必须等于依据表里那条原句。"""
        from backend.detection.checker_service import check_document

        for college in COLLEGES:
            engine = RuleEngine.for_college(college, rules_dir=RULES_DIR)
            table = basis_of(college)
            blocks = [
                paragraph(0, "title", "题目写成楷体十号。"),
                paragraph(1, "acknowledgement_body", "感谢导师从 2020 年 9 月到 2021 年 6 月的指导。"),
            ]
            result = check_document({"sections": [], "blocks": blocks}, college)
            self.assertTrue(result["issues"])
            for issue in result["issues"]:
                rule_id = issue["rule_id"]
                with self.subTest(college=college, rule_id=rule_id):
                    self.assertEqual(engine.basis_of(rule_id), issue.get("basis"))

    def test_formula_numbering_rules_quote_the_numbering_sentence(self):
        """公式编号的"连续"与"半角隔开"在 §5.1，不在 §5.6；依据必须指对。

        踩过：`formula.number_continuity` 只被家族前缀 `formula.` 命中，于是 26 份
        真样张上那 44 条"编号不连续"报告都挂着 §5.6「公式：居中书写；公式编号：
        圆括号括起，放在公式右边行末；公式与编号之间：不加虚线」——那句话**没有
        一个字**说编号要连续，等于给学生看一句不支持该结论的模板原文。
        """
        for college, keywords in (
            ("jlu_cs", {"formula.number_continuity": "依序连续编号",
                        "formula.number_format": "半角"}),
            ("jlu_se", {"formula.number_continuity": "依序连续编号",
                        "formula.number_format": "半角"}),
        ):
            engine = RuleEngine.for_college(college, rules_dir=RULES_DIR)
            for rule_id, keyword in keywords.items():
                with self.subTest(college=college, rule_id=rule_id):
                    basis = engine.basis_of(rule_id)
                    self.assertIn(keyword, basis,
                                  "%s 的依据里没有 %r：%r" % (rule_id, keyword, basis))

    def test_engine_attaches_basis_to_every_issue(self):
        """真跑一遍：产出的每条报告都带 non-empty 的 basis。"""
        engine = RuleEngine.for_college("jlu_cs", rules_dir=RULES_DIR)
        block = paragraph(0, "body", "正文段落，故意写成楷体并用 10.5 磅、单倍行距。",
                          alignment="left", first_line_indent_pt=0.0,
                          line_spacing_multiple=1.0, space_before_pt=0.0,
                          runs=[{"text": "正文段落，故意写成楷体并用 10.5 磅、单倍行距。",
                                 "font_name": "楷体", "east_asia_font": "楷体",
                                 "font_size_pt": 10.5, "bold": False}])
        issues = engine.check({"sections": [], "blocks": [block]})["issues"]
        self.assertTrue(issues)
        for issue in issues:
            with self.subTest(rule_id=issue["rule_id"]):
                self.assertTrue(issue.get("basis"), issue)

    def test_every_kind_of_block_reports_with_the_template_sentence(self):
        """把每一种块都故意写错一遍：**每条**报告都要能说出模板原句。

        踩过两次，都是这条用例原来没覆盖到的块：
        - `en.title.font` / `en.title.font_size`：英文题目（§2.3「英文题目：
          Times New Roman小三号字，居中」）——依据表里没有 `en.title.` 键，报出来的
          报告 basis 是空的；
        - `reference.entry_font`（计院）：软院依据表里有 `reference.entry_font`，
          计院只有 `reference.entry_font_size`，而计院正是发这条规则号的那个。
        26 份真样张上这两条都没触发（样张的英文题目/参考文献本来就合规），所以只有
        主动构造才查得出来。
        """
        from backend.detection.checker_service import check_document

        def wrong_run(text):
            return {"text": text, "font_name": "Arial", "east_asia_font": "楷体",
                    "font_size_pt": 20.0, "bold": False}

        def block(index, kind, text, **extra):
            block = {"type": "paragraph", "index": index, "kind": kind,
                     "text": text, "runs": [wrong_run(text)]}
            block.update(extra)
            return block

        blocks = [
            block(0, "en_title", "Research on Something"),
            block(1, "acknowledgement_body", "感谢导师的指导。"),
            block(2, "reference_entry",
                  "［1］包骥，步宏．中国数字病理发展展望[J]．实用医院临床杂志，2017．"),
            block(3, "en_abstract_title", "Abstract"),
            block(4, "en_abstract_body", "This paper studies something."),
            block(5, "en_keyword_title", "Keywords："),
            block(6, "body", "正文一段，故意用楷体二十号。", alignment="left"),
        ]
        seen = set()
        for college in COLLEGES:
            result = check_document({"sections": [], "blocks": blocks}, college,
                                    rag_service=None)
            for issue in result["issues"]:
                seen.add(issue["rule_id"])
                with self.subTest(college=college, rule_id=issue["rule_id"]):
                    self.assertTrue(issue.get("basis"),
                                    "%s 的报告没有模板依据" % issue["rule_id"])
        # 防"用例跑空了"：没报出这几条就说明构造没生效，断言等于没做。
        for rule_id in ("en.title.font", "en.title.font_size",
                        "reference.entry_font"):
            self.assertIn(rule_id, seen)

    def test_basis_survives_compaction(self):
        """合并后带出去的那条仍要有依据。"""
        from backend.detection.issue_compaction import compact_check_result

        engine = RuleEngine.for_college("jlu_cs", rules_dir=RULES_DIR)
        blocks = [paragraph(index, "body", f"正文第 {index} 段。",
                            alignment="left", first_line_indent_pt=0.0,
                            line_spacing_multiple=1.0, space_before_pt=0.0,
                            runs=[{"text": f"正文第 {index} 段。",
                                   "font_name": "楷体", "east_asia_font": "楷体",
                                   "font_size_pt": 10.5, "bold": False}])
                  for index in range(5)]
        result = engine.check({"sections": [], "blocks": blocks})
        compacted = compact_check_result(result, max_returned=50,
                                         max_location_samples=10)
        self.assertTrue(compacted["issues"])
        for issue in compacted["issues"]:
            with self.subTest(rule_id=issue["rule_id"]):
                self.assertTrue(issue.get("basis"))


if __name__ == "__main__":
    unittest.main()
