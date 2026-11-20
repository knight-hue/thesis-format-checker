# -*- coding: utf-8 -*-
"""结构识别告警：锚点没认出来时必须说出来，且一个字都不能改到识别结果。

告警是排查用的旁路信息（只进日志），但它认的"正常"必须与真实样张一致——
乱报会让人不再看日志，漏报则等于没补。这里既测"什么情况该报"，也测
"报的过程不碰 blocks"。
"""
import copy
import unittest

from backend.recognition.anomalies import structure_warnings
from backend.recognition.classification import classify_kind
from backend.recognition.structure import DEFAULT_STRUCTURE, StructureSpec


def _block(index, kind, text):
    return {"type": "paragraph", "index": index, "kind": kind, "text": text}


# 一份结构齐全的骨架：前置区封面 → 题目 → 摘要 → 目录 → 正文 → 参考文献 → 致谢。
_FULL = [
    _block(0, "cover_info", "吉林大学"),
    _block(1, "title", "基于微信小程序的学生心理自助平台的设计与实现"),
    _block(2, "abstract_title", "摘要"),
    _block(3, "body", "本文设计并实现了……"),
    _block(4, "toc", "目录"),
    _block(5, "toc", "1 绪论........................1"),
    _block(6, "heading_1", "1 绪论"),
    _block(7, "body", "研究背景……"),
    _block(8, "reference_title", "参考文献"),
    _block(9, "acknowledgement_title", "致    谢"),
]


def _warnings(blocks, spec=None):
    return structure_warnings(blocks, spec)


class HealthyStructureTest(unittest.TestCase):
    def test_full_structure_has_no_warnings(self):
        self.assertEqual(_warnings(_FULL), [])

    def test_non_paragraph_blocks_are_ignored(self):
        """表格/图片块不参与识别告警，否则正常论文会被整篇误报。"""
        blocks = [_block(0, "body", ""), {"type": "table", "index": 1}] + _FULL[1:]
        self.assertEqual(_warnings(blocks), [])


class MissingAnchorTest(unittest.TestCase):
    def test_no_anchor_at_all_reports_every_expected_section(self):
        warnings = _warnings([_block(0, "body", "一段没有锚点的正文")])
        joined = "；".join(warnings)
        self.assertIn("没有认出任何章节锚点", joined)
        self.assertIn("没有认出摘要标题", joined)
        self.assertIn("没有认出参考文献标题", joined)
        self.assertIn("没有认出致谢标题", joined)

    def test_missing_single_anchor_is_named(self):
        blocks = [b for b in _FULL if b["kind"] != "reference_title"]
        warnings = _warnings(blocks)
        self.assertEqual(warnings, ["没有认出参考文献标题（reference_title）"])

    def test_missing_heading_is_reported(self):
        blocks = [b for b in _FULL if b["kind"] != "heading_1"]
        self.assertEqual(
            _warnings(blocks),
            ["没有认出任何一、二、三级标题：标题可能是别的写法，或没套 Word 标题样式"],
        )


class FrontMatterTest(unittest.TestCase):
    def test_declaration_page_mistaken_for_title(self):
        blocks = [_block(0, "title", "吉林大学本科毕业论文（设计）独创性声明")] + _FULL[1:]
        warnings = _warnings(blocks)
        self.assertEqual(len(warnings), 1)
        self.assertIn("像声明页/授权书却被认成中文题目", warnings[0])

    def test_real_title_word_is_not_reported(self):
        """真题目里出现"诚信"这类词不算声明页，避免天天误报。"""
        blocks = [_block(0, "title", "基于X的诚信评价系统的设计与实现")] + _FULL[1:]
        self.assertEqual(_warnings(blocks), [])

    def test_front_bodies_are_reported(self):
        blocks = _FULL[:1] + [_block(1, "body", "封面区某段被当成正文")] + _FULL[1:]
        warnings = _warnings(blocks)
        self.assertEqual(len(warnings), 1)
        self.assertIn("前置区里有 1 段被判成正文", warnings[0])


class TocEntriesTest(unittest.TestCase):
    def test_entries_without_toc_title(self):
        """目录条目还在、目录标题没认出来：条目会被当成正文逐条量。"""
        blocks = [b for b in _FULL if not (b["kind"] == "toc" and b["text"] == "目录")]
        self.assertIn("有目录条目但没认出目录标题", _warnings(blocks))

    def test_toc_title_mapping_comes_from_the_structure_spec(self):
        """目录标题认不认得出，取决于学院那份表的写法映射。"""
        empty_anchors = StructureSpec(dict(DEFAULT_STRUCTURE, section_anchors={}))
        self.assertIn("有目录条目但没认出目录标题", _warnings(_FULL, empty_anchors))
        self.assertEqual(_warnings(_FULL, StructureSpec(DEFAULT_STRUCTURE)), [])

    def test_whitespace_heavy_toc_title_is_still_recognized(self):
        """真实样张里出现过 '\\n目    录'（前导换行 + 4 个空格）。

        识别层去掉全部空白后认得出它，告警必须用同一套口径——否则会对着正常论文
        报"没认出目录标题"，而这种误报会让人再也不看日志。
        """
        self.assertEqual(classify_kind("\n目    录"), "toc")
        blocks = [
            _block(0, "cover_info", "吉林大学"),
            _block(1, "title", "基于微信小程序的学习平台的设计与开发"),
            _block(2, "abstract_title", "摘要"),
            _block(3, "toc", "\n目    录"),
            _block(4, "toc", "第1章 绪论\t1"),
            _block(5, "heading_1", "第1章 绪论"),
            _block(6, "reference_title", "参考文献"),
            _block(7, "acknowledgement_title", "致谢"),
        ]
        self.assertEqual(_warnings(blocks), [])


class ReadOnlyTest(unittest.TestCase):
    def test_blocks_are_not_modified(self):
        blocks = copy.deepcopy(_FULL)
        before = copy.deepcopy(blocks)
        structure_warnings(blocks)
        structure_warnings([_block(0, "body", "无锚点")])
        self.assertEqual(blocks, before)


if __name__ == "__main__":
    unittest.main()
