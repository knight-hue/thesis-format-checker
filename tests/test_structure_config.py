# -*- coding: utf-8 -*-
"""结构识别表：默认值必须是"改造前的行为"，每学院的表必须能改得动、改错了要报错。

这张表管"这一段是什么"（与 config/rules/<学校>/<学院>/*_rules.json 的"要求是什么"分开）。它一旦写错，
后果不是"少报几条"而是**整块区域错位**——所以校验必须严格、必须能证明改动真的生效。
"""
import json
import os
import tempfile
import unittest
import warnings

from backend.college_registry import file_path, path_of
from backend.recognition.classification import classify_kind
from backend.recognition.structure import DEFAULT_STRUCTURE, StructureSpec, load_structure

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_DIR = os.path.join(ROOT, "config", "rules")
COLLEGES = ("jlu_cs", "jlu_se")


def _write_registry(root, entries):
    """临时注册表：测试用的假学院（如 abc_xy）得先登记，否则定位不到规则目录。"""
    path = os.path.join(root, "colleges.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({cid: {"school": school, "college": college}
                   for cid, (school, college) in entries.items()}, handle)
    return path


def _write_structure(root, college_id, data, school="abc", college="xy"):
    """写一份结构识别表到 config/rules/<学校>/<学院>/，返回文件路径。"""
    path = file_path(school, college, college_id, "structure", root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    return path


class DefaultStructureTest(unittest.TestCase):
    """默认表 = 改造前的常量值：判据一个都不能变。"""

    def test_default_table_compiles_without_warnings(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            spec = StructureSpec(DEFAULT_STRUCTURE)
        self.assertIn("摘要", spec.anchor_kinds)
        self.assertTrue(spec.ref_entry_number_re.match("［1］张三．"))
        self.assertTrue(spec.ref_entry_number_re.match("[12] Li S."))

    def test_default_behaviour_is_the_code_behaviour(self):
        cases = {
            ("摘　要", None): "abstract_title",
            ("参考文献", None): "reference_title",
            ("致    谢", None): "acknowledgement_title",
            ("第1章 绪论", None): "heading_1",
            ("第 1 章 绪论", None): "body",          # 章号带空格：要靠 Word 样式兜底
            ("第 1 章 绪论", "标题 1"): "heading_1",
            ("图3-1 系统结构图", None): "figure_caption",
            ("续表2-1", None): "table_continued_note",
            ("吉林大学学士学位论文（设计）承诺书", None): "pledge_title",
            ("指导教师：张三", None): "author_info",
            ("Keywords: a, b", None): "en_keyword_title",
        }
        for (text, style), expected in cases.items():
            with self.subTest(text=text, style=style):
                self.assertEqual(expected, classify_kind(text, style_name=style))


class CollegeStructureFilesTest(unittest.TestCase):
    """两院各有一份表，且当前与默认值一致（纯搬家，零行为变化）。"""

    def test_every_college_has_a_loadable_table(self):
        default = StructureSpec(DEFAULT_STRUCTURE).config
        for college in COLLEGES:
            path = path_of(college, "structure", RULES_DIR)
            with self.subTest(college=college):
                self.assertTrue(os.path.isfile(path), f"缺少 {path}")
                spec = load_structure(college, rules_dir=RULES_DIR)
                for group in ("section_anchors", "heading", "front_matter",
                              "captions", "references"):
                    self.assertEqual(default[group], spec.config[group],
                                     f"{college} 的 {group} 与默认值不一致")

    def test_missing_file_falls_back_to_default(self):
        self.assertEqual(
            StructureSpec(DEFAULT_STRUCTURE).config["captions"],
            load_structure("没有这个学院", rules_dir=RULES_DIR).config["captions"])


class StructureOverrideTest(unittest.TestCase):
    """改表必须真的生效（否则"配置写了却没生效"最难查）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.registry = _write_registry(self.root, {"abc_xy": ("abc", "xy")})

    def tearDown(self):
        self.tmp.cleanup()

    def test_declaration_page_and_caption_prefix_can_be_configured(self):
        _write_structure(self.root, "abc_xy", {
            "college_id": "abc_xy",
            "front_matter": {"pledge_title_suffixes": ["学术诚信声明"]},
            "captions": {"figure_prefixes": ["图", "Fig."]},
        })
        spec = load_structure("abc_xy", rules_dir=self.root, config_path=self.registry)

        # 异校的声明页被免检，而不是按正文报格式
        self.assertEqual("pledge_title",
                         classify_kind("某大学研究生学术诚信声明", structure=spec))
        # 英文图题也认出来了
        self.assertEqual("figure_caption",
                         classify_kind("Fig. 3-1 System architecture", structure=spec))
        # 表格/词表是**整组替换**：写了 pledge_title_suffixes 就以它为准，
        # 所以要保留原来的写法就得一起写上（下面这条断言钉住这个语义）。
        self.assertEqual("body",
                         classify_kind("吉林大学学士学位论文（设计）承诺书",
                                       structure=spec))
        # 没写到的分组仍按默认：封面字段行照旧认
        self.assertEqual("author_info",
                         classify_kind("指导教师：张三", structure=spec))
        # 同一份文本在默认表下不是 pledge_title（证明差异确实来自配置）
        self.assertNotEqual("pledge_title",
                            classify_kind("某大学研究生学术诚信声明"))

    def test_heading_style_name_can_be_configured(self):
        _write_structure(self.root, "abc_xy", {
            "heading": {"style_names": ["标题", "heading", "章标题"]},
        })
        spec = load_structure("abc_xy", rules_dir=self.root, config_path=self.registry)
        self.assertEqual("heading_2",
                         classify_kind("研究背景", style_name="章标题 2", structure=spec))

    def test_every_group_can_be_overridden_and_takes_effect(self):
        """五个分组逐个改一遍：同一段文本在默认表下与改过的表下结论不同。

        没有这条测试，"某组写进配置却不生效"只能在换学校时才发现。
        """
        cases = [
            ("section_anchors", {"内容摘要": "abstract_title"},
             "内容摘要", None, "body", "abstract_title"),
            ("heading", {"chapter_units": "讲"},
             "第1讲 绪论", None, "body", "heading_1"),
            ("front_matter", {"cover_fields": ["学号"]},
             "学号：2021001", None, "body", "author_info"),
            ("captions", {"table_prefixes": ["Table"]},
             "Table 2-1 Results", None, "body", "table_caption"),
        ]
        for group, config, text, style, before, after in cases:
            with self.subTest(group=group):
                self.assertEqual(before, classify_kind(text, style_name=style))
                _write_structure(self.root, "abc_xy", {group: config})
                spec = load_structure("abc_xy", rules_dir=self.root, config_path=self.registry)
                self.assertEqual(after, classify_kind(text, style_name=style,
                                                      structure=spec))

    def test_entry_number_brackets_can_be_overridden(self):
        """条目序号括号是区域状态机用的正则（不是 classify_kind），单独验一遍。"""
        self.assertTrue(StructureSpec(DEFAULT_STRUCTURE).ref_entry_number_re.match("［1］张三"))
        self.assertFalse(StructureSpec(DEFAULT_STRUCTURE).ref_entry_number_re.match("(1) 张三"))
        _write_structure(self.root, "abc_xy", {
            "references": {"entry_number_open": "(", "entry_number_close": ")"}})
        spec = load_structure("abc_xy", rules_dir=self.root, config_path=self.registry)
        self.assertTrue(spec.ref_entry_number_re.match("(1) Zhang S."))

    def test_comment_keys_are_ignored(self):
        _write_structure(self.root, "abc_xy", {
            "_note": "给人看的注释",
            "captions": {"figure_prefixes": ["图"]},
        })
        self.assertTrue(load_structure("abc_xy", rules_dir=self.root, config_path=self.registry).figure_caption_re)


class BadStructureFileTest(unittest.TestCase):
    """配置写错要立刻报错并指出位置，不能静默按默认表跑。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.registry = _write_registry(self.root, {"abc_xy": ("abc", "xy")})

    def tearDown(self):
        self.tmp.cleanup()

    def _load(self, data):
        _write_structure(self.root, "abc_xy", data)
        return load_structure("abc_xy", rules_dir=self.root, config_path=self.registry)

    def test_unknown_group_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self._load({"sections": {}})
        self.assertIn("未知分组", str(ctx.exception))

    def test_unknown_key_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self._load({"captions": {"figure_prefixs": ["图"]}})
        self.assertIn("未知键", str(ctx.exception))

    def test_anchor_pointing_to_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self._load({"section_anchors": {"内容摘要": "abstract"}})
        self.assertIn("未知 kind", str(ctx.exception))

    def test_empty_word_list_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self._load({"front_matter": {"pledge_title_suffixes": ["", "  "]}})
        self.assertIn("空词表", str(ctx.exception))

    def test_broken_json_is_reported_with_path(self):
        path = file_path("abc", "xy", "abc_xy", "structure", self.root)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{ 不是 json")
        with self.assertRaises(ValueError) as ctx:
            load_structure("abc_xy", rules_dir=self.root, config_path=self.registry)
        self.assertIn("abc_xy_structure.json", str(ctx.exception))

    def test_bom_encoded_file_is_accepted(self):
        """Windows 记事本另存常带 BOM，配置文件要读得进。"""
        path = file_path("abc", "xy", "abc_xy", "structure", self.root)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8-sig") as handle:
            json.dump({"captions": {"figure_prefixes": ["图"]}}, handle,
                      ensure_ascii=False)
        self.assertTrue(load_structure("abc_xy", rules_dir=self.root, config_path=self.registry).figure_caption_re)


if __name__ == "__main__":
    unittest.main()
