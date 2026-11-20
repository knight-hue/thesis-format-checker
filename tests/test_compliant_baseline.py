# -*- coding: utf-8 -*-
"""合规基线：一份“完全合规”的合成论文必须零报，定点破坏必须照报。

这条基线取代了原先的“官方模板基线”。模板不能当零报基线——模板自己的“规范说明
文字”（“图的位置应该在相关文字说明之后…”这类）在文档里就是普通段落，字排成五号
黑体，判定层只能按正文判，必然报出来；而模板里没有能把这类段落与真正文分开的
可靠信号（实测：与正文段落在样式名/颜色上无差别，只有个别行带黄色高亮），硬做
一个只会重演“spec_note 过宽误伤正文”的老问题。

所以基线改成两条互补的断言：
  · 合规文档零报 —— 挡住“规则比模板说明更苛刻”（过严）；
  · 定点破坏照报 —— 挡住“规则没接上”（漏检）。

文档里的字体/字号/对齐/边距都从 rules 现读，规则一改样张跟着改，不会出现样张
还写着上一次的旧值。
"""
import copy
import json
import os
import unittest

from backend.detection.checker_service import check_document
from backend.detection.rule_engine import RuleEngine

from backend.college_registry import load_colleges, path_of

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_DIR = os.path.join(ROOT, "config", "rules")
# 学院清单从注册表现取：新学院上架（目标 3）后自动纳入合规基线，不再硬编码。
COLLEGES = tuple(load_colleges().keys())


def load_rules(college_id):
    with open(path_of(college_id, "rules", RULES_DIR),
              encoding="utf-8") as handle:
        return json.load(handle)


def make_run(text, cjk=None, latin=None, size=None, bold=False):
    return {"text": text, "font_name": latin or cjk or "Times New Roman",
            "east_asia_font": cjk, "font_size_pt": size, "bold": bold}


def styled(kind, text, rule, *, cjk=None, indent=0.0, before=None):
    """按一条样式规则（font_name/font_size_pt/bold/alignment）生成样式块。"""
    font = rule.get("font_name")
    return {"type": "paragraph", "kind": kind, "text": text,
            "alignment": rule.get("alignment"),
            "first_line_indent_pt": indent,
            "line_spacing_multiple": 1.5,
            "space_before_pt": before,
            "runs": [make_run(text, cjk=cjk or font, latin=font,
                              size=rule.get("font_size_pt"),
                              bold=bool(rule.get("bold")))]}


def build_compliant(rules):
    """按 rules 生成一份完全合规的文档（每个 kind 都按规则自己的取值来写）。"""
    abstract = rules["abstract"]
    english = rules["english_abstract"]
    keywords = rules["keywords"]
    headings = rules["headings"]
    body_rule = rules["body"]
    tables = rules["tables"]
    figures = rules["figures"]
    notes = rules["notes"]
    refs = rules["references"]
    ack = rules["acknowledgement"]
    entry = refs.get("entry") or {}
    separator = keywords.get("separator") or "，"

    blocks = []

    def add(item):
        item["index"] = len(blocks)
        blocks.append(item)

    keyword_title_rule = keywords.get("title") or {}
    add(styled("title", "基于机器学习的癌症检测的实现", rules["title"]))
    add(styled("abstract_title", abstract["title_text"], abstract["title"]))
    add({"type": "paragraph", "kind": "abstract_body",
         "text": "本文研究了基于机器学习的癌症检测方法，并给出了完整的实验结果与分析。",
         "alignment": "left", "first_line_indent_pt": 24.0,
         "line_spacing_multiple": abstract["body"].get("line_spacing_multiple"),
         "space_before_pt": None,
         "runs": [make_run("本文研究了基于机器学习的癌症检测方法，并给出了完整的实验结果与分析。",
                           cjk=abstract["body"]["font_name"],
                           size=abstract["body"]["font_size_pt"])]})
    add(styled("keyword_title",
               (keywords.get("label") or "") + (keywords.get("label_colon") or ""),
               keyword_title_rule,
               before=12.0 if keyword_title_rule.get("blank_line_before") else None))
    add(styled("keyword_body", separator.join(["机器学习", "癌症检测", "深度学习"]),
               keywords.get("body") or {}))

    en_title_rule = english.get("abstract_title") or {}
    en_keyword_title_rule = english.get("keyword_title") or {}
    add(styled("en_abstract_title", english["title_text"], en_title_rule))
    add({"type": "paragraph", "kind": "en_abstract_body",
         "text": "This thesis studies cancer detection based on machine learning.",
         "alignment": "left", "first_line_indent_pt": 24.0,
         "line_spacing_multiple": english["body"].get("line_spacing_multiple"),
         "space_before_pt": None,
         "runs": [make_run("This thesis studies cancer detection based on machine learning.",
                           latin=english["body"]["font_name"],
                           size=english["body"]["font_size_pt"])]})
    add(styled("en_keyword_title",
               (english.get("keyword_label") or "")
               + (english.get("keyword_colon") or ""),
               en_keyword_title_rule,
               before=12.0 if en_keyword_title_rule.get("blank_line_before") else None))
    add(styled("en_keyword_body",
               ",".join(["machine learning", "cancer detection", "deep learning"]),
               english.get("keyword_body") or {}))

    add(styled("heading_1", "第1章 绪论", headings["heading_1"]))
    add(styled("heading_2", "1.1 研究背景", headings["heading_2"]))
    add(styled("heading_3", "1.1.1 国内研究现状", headings["heading_3"]))
    marker = (notes.get("prefix") or ["【注】"])[0]
    add({"type": "paragraph", "kind": "note", "text": marker + "注意标志与标题对齐。",
         "alignment": headings["heading_2"].get("alignment") or "left",
         "first_line_indent_pt": 0.0,
         "line_spacing_multiple": notes.get("line_spacing_multiple"),
         "space_before_pt": notes.get("space_before_pt"),
         "runs": [make_run(marker, cjk=notes.get("font_name"),
                           size=notes.get("font_size_pt")),
                  make_run("注意标志与标题对齐。",
                           cjk=body_rule.get("east_asia_font"),
                           size=notes.get("content_font_size_pt")
                           or notes.get("font_size_pt"))]})

    def body(text):
        return {"type": "paragraph", "kind": "body", "text": text,
                "alignment": "left", "first_line_indent_pt": 24.0,
                "line_spacing_multiple": body_rule.get("line_spacing_multiple"),
                "space_before_pt": None,
                "runs": [make_run(text, cjk=body_rule["east_asia_font"],
                                  latin=body_rule.get("latin_font"),
                                  size=body_rule["font_size_pt"])]}

    add(body("本文的主要工作包含数据预处理、模型训练与结果分析，实验代码使用 PyTorch 编写。"))
    add({"type": "image", "index": len(blocks), "path": "images/pixel.png",
         "width_inch": 1.0, "height_inch": 1.0, "align": "center"})
    add(styled("figure_caption", "图1-1\u3000模型结构图", figures["caption"]))
    add(styled("table_caption", "表1-1\u3000实验数据表", tables["caption"]))
    cell_run = make_run("数据", cjk=tables.get("cell_east_asia_font"),
                        size=tables.get("cell_font_size_pt"))
    add({"type": "table", "index": len(blocks), "n_rows": 1, "n_cols": 1,
         "cells": [{"row": 0, "col": 0, "text": cell_run["text"],
                    "font_name": cell_run["font_name"],
                    "east_asia_font": cell_run["east_asia_font"],
                    "font_size_pt": cell_run["font_size_pt"],
                    "runs": [cell_run]}]})

    add(styled("heading_1", "第2章 实验结果", headings["heading_1"]))
    add(body("实验结果表明该方法的准确率达到 95% 以上。"))

    add(styled("reference_title", refs["title_text"], refs.get("title") or {}))
    entry_text = ((entry.get("number_prefix_expected") or "")
                  + "包骥，步宏．中国数字病理发展展望[J]．实用医院临床杂志，"
                    "2017，14(3)：1-3．")
    add({"type": "paragraph", "kind": "reference_entry", "text": entry_text,
         "alignment": "left",
         "first_line_indent_pt": 0.0 if entry.get("indent_tolerance_pt") is not None else None,
         "line_spacing_multiple": 1.5, "space_before_pt": None,
         "runs": [make_run(entry_text, cjk=entry.get("font_name"),
                           size=entry.get("font_size_pt"))]})

    add(styled("acknowledgement_title", ack["title_text"], ack.get("title") or {}))
    if ack.get("body"):
        text = "感谢导师在论文完成过程中给予的悉心指导与帮助。"
        add({"type": "paragraph", "kind": "acknowledgement_body", "text": text,
             "alignment": "left", "first_line_indent_pt": 24.0,
             "line_spacing_multiple": ack["body"].get("line_spacing_multiple"),
             "space_before_pt": None,
             "runs": [make_run(text, cjk=ack["body"]["font_name"],
                               size=ack["body"]["font_size_pt"])]})

    return {"sections": [{"index": 0, **rules["page"]["margins"]}],
            "blocks": blocks}


def rule_ids(document, rules):
    return [issue["rule_id"] for issue in RuleEngine(rules).check(document)["issues"]]


def engine_ids(document, college):
    """走完整链路（含零 LLM 的确定性语义检查）。"""
    result = check_document(document, college, rag_service=None)
    return [issue["rule_id"] for issue in result["issues"]]


def find_block(document, kind):
    return next(index for index, block in enumerate(document["blocks"])
                if block.get("kind") == kind)


# 定点破坏：(说明, kind, 改什么, 期望报出的规则号)；期望值为 None 表示两院都该报。
MUTATIONS = (
    ("正文字号写小", "body", ("size", 10.5), "body.font_size", None),
    ("正文中文换楷体", "body", ("east_asia", "楷体"), "body.east_asia_font", None),
    ("正文行距写单倍", "body", ("spacing", 1.0), "body.line_spacing", None),
    ("正文西文换宋体", "body", ("latin", "宋体"), "body.latin_font", None),
    ("一级标题字号写小", "heading_1", ("size", 12.0), "heading.heading_1.font_size", None),
    ("一级标题写左对齐", "heading_1", ("align", "left"),
     "heading.heading_1.alignment", None),
    ("二级标题编号写成顿号", "heading_2", ("text", "1、研究背景"),
     "heading.heading_2.number", None),
    ("三级标题编号不写", "heading_3", ("text", "国内研究现状"),
     "heading.heading_3.number", None),
    ("摘要标题写左对齐", "abstract_title", ("align", "left"),
     "abstract.title.alignment", None),
    ("关键词只写两个", "keyword_body", ("text", "机器学习，癌症检测"),
     "keyword.count", None),
    ("关键词末尾加句号", "keyword_body",
     ("text", "机器学习，癌症检测，深度学习。"), "keyword.terminal_punct", None),
    ("关键词标签写成“关键词：”", "keyword_title", ("text", "关键词："),
     "keyword.title_label", "jlu_cs"),
    ("注释标志换宋体", "note", ("marker_east_asia", "宋体"), "note.font", None),
    ("注释段前间距写 0", "note", ("space_before", 0.0), "note.space_before", None),
    ("图题挪到图片上方", "figure_caption", ("move_figure", True),
     "figure.caption_position", None),
    ("参考文献条目缺序号", "reference_entry", ("drop_prefix", True),
     "reference.entry_number", "jlu_cs"),
    ("参考文献条目末尾无句点", "reference_entry", ("strip_period", True),
     "reference.entry_period", "jlu_cs"),
    ("参考文献条目缩进两格", "reference_entry", ("indent", 24.0),
     "reference.entry_indent", "jlu_cs"),
    ("页边距写宽", "body", ("margins", 4.0), "page.left_cm", None),
)


def mutate(document, kind, change):
    """按 change 描述破坏 document 的一个属性，返回新文档。"""
    document = copy.deepcopy(document)
    position = find_block(document, kind)
    block = document["blocks"][position]
    what, value = change
    if what == "size":
        for run in block.get("runs", []):
            run["font_size_pt"] = value
        block["font_size_pt"] = value
    elif what == "east_asia":
        for run in block.get("runs", []):
            run["east_asia_font"] = value
    elif what == "latin":
        for run in block.get("runs", []):
            run["font_name"] = value
    elif what == "marker_east_asia":
        block["runs"][0]["east_asia_font"] = value
    elif what == "spacing":
        block["line_spacing_multiple"] = value
    elif what == "align":
        block["alignment"] = value
    elif what == "text":
        block["text"] = value
    elif what == "space_before":
        block["space_before_pt"] = value
    elif what == "indent":
        block["first_line_indent_pt"] = value
    elif what == "drop_prefix":
        block["text"] = block["text"].replace("［1］", "", 1)
        for run in block.get("runs", []):
            run["text"] = run["text"].replace("［1］", "", 1)
    elif what == "strip_period":
        block["text"] = block["text"].rstrip("．.")
        for run in block.get("runs", []):
            run["text"] = run["text"].rstrip("．.")
    elif what == "move_figure":
        # 把图题挪到图片上方：图片插到图题之后
        image = document["blocks"].pop(position - 1)
        document["blocks"].insert(position, image)
        for index, item in enumerate(document["blocks"]):
            item["index"] = index
    elif what == "margins":
        document["sections"][0]["left_cm"] = value
    else:
        raise AssertionError(f"未知的破坏方式：{what}")
    return document


class CompliantBaselineTest(unittest.TestCase):
    """按 rules 写的合规文档必须零报（过严的话这里就红）。"""

    def test_rule_layer_reports_nothing(self):
        for college in COLLEGES:
            rules = load_rules(college)
            with self.subTest(college=college):
                self.assertEqual([], rule_ids(build_compliant(rules), rules))

    def test_full_pipeline_reports_nothing(self):
        """整条链路（含零 LLM 的确定性语义条款）也要零报。"""
        for college in COLLEGES:
            rules = load_rules(college)
            with self.subTest(college=college):
                self.assertEqual([], engine_ids(build_compliant(rules), college))


class InlineImageTest(unittest.TestCase):
    """与文字同段的行内图形（MathType/公式的 OLE 预览图）不是插图。

    原先这里钉的是"缺图题"；那条检查已按模板原句撤销（模板只写"图号和图题名排于
    图的下方"，没有"每张图都必须有图题"）。现在钉的是 figure.placement（随文排）：
    图排在正文首次提及之前才报，行内图形不套这条插图检查。
    """

    CAPTION = "图1-1\u3000系统架构"
    BODY = "系统由三个模块组成，如图1-1所示。"

    def _document(self, college, *, inline):
        image = {"type": "image", "index": 0, "path": "images/pixel.png",
                 "width_inch": 2.0, "height_inch": 2.0, "align": "center"}
        if inline:
            image["inline"] = True
        caption = {
            "type": "paragraph", "index": 1, "kind": "figure_caption",
            "text": self.CAPTION, "alignment": "center",
            "runs": [{"text": self.CAPTION, "font_name": "黑体",
                      "east_asia_font": "黑体", "font_size_pt": 10.5}],
        }
        body = {
            "type": "paragraph", "index": 2, "kind": "body", "text": self.BODY,
            "alignment": "justify", "first_line_indent_pt": 24,
            "line_spacing_multiple": 1.5,
            "runs": [{"text": self.BODY, "font_name": "Times New Roman",
                      "east_asia_font": "宋体", "font_size_pt": 12}],
        }
        margins = dict(load_rules(college)["page"]["margins"])
        return {"sections": [{"index": 0, **margins}],
                "blocks": [image, caption, body]}

    def test_standalone_image_before_its_mention_is_reported(self):
        for college in COLLEGES:
            with self.subTest(college=college):
                self.assertIn("figure.placement",
                              rule_ids(self._document(college, inline=False),
                                       load_rules(college)))

    def test_inline_graphic_is_not_checked_as_a_figure(self):
        for college in COLLEGES:
            with self.subTest(college=college):
                self.assertNotIn("figure.placement",
                                 rule_ids(self._document(college, inline=True),
                                          load_rules(college)))


class MutationCoverageTest(unittest.TestCase):
    """定点破坏必须照报（漏检的话这里就红）。"""

    def test_mutations_are_reported(self):
        base = {college: build_compliant(load_rules(college))
                for college in COLLEGES}
        for name, kind, change, expected, only in MUTATIONS:
            for college in COLLEGES:
                if only and only != college:
                    continue
                rules = load_rules(college)
                document = mutate(base[college], kind, change)
                with self.subTest(case=name, college=college):
                    self.assertIn(expected, rule_ids(document, rules))
                    self.assertGreaterEqual(len(rule_ids(document, rules)), 1)

    def test_reference_entry_parts_mutation_is_reported_end_to_end(self):
        """条目的著录项由语义层的确定性条款判，走整条链路验（含按学院启停）。"""
        for college in COLLEGES:
            document = mutate(
                build_compliant(load_rules(college)), "reference_entry",
                ("text", "［1］包骥，步宏．中国数字病理发展展望[J]．实用医院临床杂志，2017．"))
            with self.subTest(college=college):
                ids = engine_ids(document, college)
                if college == "jlu_cs":
                    self.assertIn("reference.format", ids)
                else:
                    # 软院模板的参考文献部分只有“字体字号”一句，整条不查
                    self.assertNotIn("reference.format", ids)

    def test_keyword_glued_to_the_abstract_is_reported_end_to_end(self):
        """关键词粘在摘要正文末尾：模板写了“单独一行”，整条链路要报出来。"""
        for college in COLLEGES:
            document = mutate(
                build_compliant(load_rules(college)), "abstract_body",
                ("text", "本文研究了癌症检测方法。关键词：机器学习，癌症检测，深度学习"))
            with self.subTest(college=college):
                self.assertIn("keyword.title.own_line", engine_ids(document, college))


if __name__ == "__main__":
    unittest.main()
