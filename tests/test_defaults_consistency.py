# -*- coding: utf-8 -*-
"""write_back/_defaults.py 的兜底值与 config/rules/*_rules.json 的一致性。

_defaults.py 里的 DEFAULT_* 不是第二份规则来源，而是"契约字段缺省时该写什么"的
镜像：真正判定的是 rules。一旦 rules 改了而镜像没跟着改，重建出来的文档就会和检测
结论自相矛盾——检测层按 rules 报"表内字号应为 10.5pt"，写回层兜底却按 12pt 生成，
用户改完再看还是错的。所以这里把每个字段与 rules 的对应关系断言下来。
"""
import json
import os
import re
import unittest
from typing import get_args

from backend.document_contract import ALIGNMENT
from backend.write_back._defaults import (
    DEFAULT_CELL,
    DEFAULT_IMAGE,
    DEFAULT_RUN,
    DEFAULT_SECTION,
    pick,
)

from backend.college_registry import RULE_SUFFIX, rule_sets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_DIR = os.path.join(ROOT, "config", "rules")

# 兜底字段 → config/rules 里的路径；键集合必须与 DEFAULT_* 完全一致，
# 多一个键说明镜像里有 rules 管不到的自造默认值，少一个键说明该兜底没接上 rules。
_SECTION_MIRROR = {
    "left_cm":     ("page", "margins", "left_cm"),
    "right_cm":    ("page", "margins", "right_cm"),
    "top_cm":      ("page", "margins", "top_cm"),
    "bottom_cm":   ("page", "margins", "bottom_cm"),
    "header_cm":   ("page", "margins", "header_cm"),
    "footer_cm":   ("page", "margins", "footer_cm"),
}

# 写回层自己要、但 rules 不管的兜底项：官方模板没有“纸张尺寸/页面方向”的要求句，
# 判定层不查它们（paper_size / orientation 已从 rules 撤销），所以没有可镜像的键。
# 列在这里是为了让“兜底字段 —— rules”的对应关系仍然是全覆盖的：
# 任何新加的兜底字段要么进 _SECTION_MIRROR，要么进这里并说明理由。
_SECTION_NOT_FROM_RULES = {"paper_size", "orientation"}

_RUN_MIRROR = {
    "font_name":       ("body", "latin_font"),
    "east_asia_font":  ("body", "east_asia_font"),
    "font_size_pt":    ("body", "font_size_pt"),
    "char_spacing_pt": ("body", "char_spacing_pt"),
}

_CELL_MIRROR = {
    "east_asia_font": ("tables", "cell_east_asia_font"),
    "font_size_pt":   ("tables", "cell_font_size_pt"),
}

# 写回层自己要、但 rules 不管的兜底项：模板在图表那一章没有“表内西文/数字用什么
# 字体”的说明句（只有 §4.3 正文的「西文、数字：Times New Roman」），判定层据此不查
# 这一项（table.cell_latin_font 已撤销），所以没有可镜像的键。重建文档时仍需要一个
# 具体字体，故保留。同 _SECTION_NOT_FROM_RULES。
_CELL_NOT_FROM_RULES = {"font_name"}


def _rules_files():
    found = rule_sets(RULES_DIR)
    if not found:
        raise AssertionError(f"{RULES_DIR} 下没有 *{RULE_SUFFIX}")
    for college_id, path in found:
        with open(path, encoding="utf-8") as handle:
            yield college_id, json.load(handle)


def _dig(data, path):
    for key in path:
        data = data[key]
    return data


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _same(expected, actual):
    """字号/字距允许 int 与 float 的表示差异（rules 写 12，兜底写 12.0）。"""
    if isinstance(expected, float) or isinstance(actual, float):
        return float(expected) == float(actual)
    return expected == actual


class DefaultsMirrorRulesTest(unittest.TestCase):
    def _assert_mirror(self, defaults, mirror, rules, not_from_rules=frozenset()):
        self.assertEqual(set(defaults), set(mirror) | set(not_from_rules),
                         "兜底字段与 rules 映射的键集合不一致")
        for key, path in mirror.items():
            self.assertTrue(
                _same(_dig(rules, path), defaults[key]),
                f"DEFAULT 的 {key}={defaults[key]!r} 与 rules 的 {'.'.join(path)}="
                f"{_dig(rules, path)!r} 不一致",
            )

    def test_section_defaults_mirror_page_rules(self):
        for name, rules in _rules_files():
            with self.subTest(rules=name):
                self._assert_mirror(DEFAULT_SECTION, _SECTION_MIRROR, rules,
                                    _SECTION_NOT_FROM_RULES)

    def test_revoked_page_items_are_not_expected_in_rules(self):
        """纸张尺寸/页面方向已撤销：rules 里不该再有这两项，否则就是假标准。"""
        for name, rules in _rules_files():
            page = rules.get("page") or {}
            with self.subTest(rules=name):
                self.assertNotIn("paper_size", page)
                self.assertNotIn("orientation", page)

    def test_run_defaults_mirror_body_rules(self):
        for name, rules in _rules_files():
            with self.subTest(rules=name):
                self._assert_mirror(DEFAULT_RUN, _RUN_MIRROR, rules)

    def test_cell_defaults_mirror_table_rules(self):
        for name, rules in _rules_files():
            with self.subTest(rules=name):
                self._assert_mirror(DEFAULT_CELL, _CELL_MIRROR, rules,
                                    _CELL_NOT_FROM_RULES)

    def test_revoked_cell_latin_rule_is_not_expected_in_rules(self):
        """表内西文/数字字体已撤销：rules 里不该再有 cell_latin_font，否则就是假标准。"""
        for name, rules in _rules_files():
            with self.subTest(rules=name):
                self.assertNotIn("cell_latin_font", rules.get("tables") or {})

    def test_rules_do_not_enable_checks_without_a_template_sentence(self):
        """这些开关键在模板里**查无对应文字**，不许出现在 rules 里。

        - `body.alignment`：§4.1 只写了「正文中文…；正文行距…；正文字符间距…」，
          一个字没说正文对齐方式；
        - `body.first_line_indent_pt`：模板通篇没有"正文缩进"（已按用户裁决撤销）；
        - `notes.first_line_indent_pt`：§4.6 只写「注释起排：另起一行，用【注】开头」，
          模板全文没有"顶格"二字（只有参考文献序号那处的"左顶格"），
          代码里那句"顶格起排"是自己加的。

        实测：26 份真样张里这三条各 0 次，专门构造的文档也触发不了——目前是靠
        "期望值为 None 即放行"（`_align_eq`/`_close` 对空期望返回 True）隐式关掉的。
        本用例把这层隐式变成显式：谁想启用这三条，先补模板原句。
        """
        for name, rules in _rules_files():
            body = rules.get("body") or {}
            notes = rules.get("notes") or {}
            with self.subTest(rules=name):
                self.assertNotIn("alignment", body)
                self.assertNotIn("first_line_indent_pt", body)
                self.assertNotIn("first_line_indent_pt", notes)


class DefaultsShapeTest(unittest.TestCase):
    def test_run_defaults_do_not_pin_bold_or_italic(self):
        """W2：bold/italic 缺省必须是"不写"，不能是 False。"""
        self.assertNotIn("bold", DEFAULT_RUN)
        self.assertNotIn("italic", DEFAULT_RUN)

    def test_image_defaults_keep_original_size_and_legal_alignment(self):
        self.assertIsNone(DEFAULT_IMAGE["width_inch"])
        self.assertIsNone(DEFAULT_IMAGE["height_inch"])
        self.assertIn(DEFAULT_IMAGE["align"], get_args(ALIGNMENT))

    def test_no_unused_keys(self):
        """每个兜底键都得有人在用，避免又长出没人读的"文档性"常量。"""
        import backend.write_back.op_passage as op_passage
        import backend.write_back.op_picture as op_picture
        import backend.write_back.op_table as op_table

        sources = "".join(
            _read(module.__file__) for module in (op_passage, op_picture, op_table)
        )
        defaults = {
            "DEFAULT_RUN": DEFAULT_RUN,
            "DEFAULT_CELL": DEFAULT_CELL,
            "DEFAULT_IMAGE": DEFAULT_IMAGE,
        }
        for name, values in defaults.items():
            for key in values:
                self.assertTrue(
                    f'["{key}"]' in sources, f"{name} 的 {key} 没有任何调用点读取",
                )

    def test_section_defaults_match_queried_keys(self):
        """op_section._val 用变量下标取 DEFAULT_SECTION，所以比对"被查了哪些键"。"""
        from backend.write_back import op_section

        source = _read(op_section.__file__)
        queried = set(re.findall(r'_val\(\s*config\s*,\s*"([a-z_]+)"\s*\)', source))
        self.assertEqual(queried, set(DEFAULT_SECTION))


class PickTest(unittest.TestCase):
    def test_none_falls_back_and_falsy_values_are_kept(self):
        self.assertEqual(pick({"align": None}, "align", "center"), "center")
        self.assertEqual(pick({}, "align", "center"), "center")
        self.assertEqual(pick({"indent": 0}, "indent", 24.0), 0)
        self.assertEqual(pick({"bold": False}, "bold", True), False)

    def test_object_attribute_defaults_are_not_none(self):
        """兜底值本身不能是 None，否则等于没兜底（宽高除外，见 shape 测试）。"""
        for name in ("DEFAULT_RUN", "DEFAULT_CELL"):
            values = {"DEFAULT_RUN": DEFAULT_RUN, "DEFAULT_CELL": DEFAULT_CELL}[name]
            for key, value in values.items():
                with self.subTest(default=name, key=key):
                    self.assertIsNotNone(value)


if __name__ == "__main__":
    unittest.main()
