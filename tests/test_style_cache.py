# -*- coding: utf-8 -*-
"""样式解析缓存（parser/font_reader.py）的两条不变量。

背景：python-docx 的 ``run.style`` / ``paragraph.style`` / ``style.base_style``
每次都重新解析 Part→styles.xml 的关系、并在没有样式 id 时遍历整张样式表
（``default_for``）。实测一份真论文触发 52,280 次 get_style、36,962 次 default_for、
57,529 次 base_style，解析 26 秒里大部分花在这里。加缓存后 23 份真样张
解析输出与不加缓存**逐字节一致**、耗时降到 1/2.1~1/4.3。

这里钉住两条不变量，防止以后改缓存时不再成立：
1. **语义不变**：缓存版结果与 python-docx 原方法一致；
2. **确实命中**：同一份文档内重复取同一个样式，返回的是同一个对象（否则缓存没生效，
   性能会悄悄退回去）。
"""
import os
import tempfile
import unittest

from docx import Document
from docx.enum.style import WD_STYLE_TYPE

from backend.parser import font_reader as fr


def _make_docx(directory):
    """一份带样式继承链的文档：Normal ← Heading 1，并给一个 run 指定字符样式。"""
    doc = Document()
    doc.add_paragraph("普通段落", style="Normal")
    doc.add_paragraph("一级标题", style="Heading 1")
    paragraph = doc.add_paragraph("带字符样式的 run")
    run = paragraph.add_run("加粗字符")
    try:
        run.style = "Strong"
    except Exception:
        run.style = "Emphasis"
    path = os.path.join(directory, "style_cache_probe.docx")
    doc.save(path)
    return path


class StyleCacheTransparencyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.doc = Document(_make_docx(self._tmp.name))
        self.paragraphs = list(self.doc.paragraphs)

    def test_paragraph_style_matches_python_docx(self):
        for paragraph in self.paragraphs:
            with self.subTest(text=paragraph.text[:8]):
                expected = paragraph.style
                actual = fr._paragraph_style(paragraph)
                if expected is None:
                    self.assertIsNone(actual)
                else:
                    self.assertIsNotNone(actual)
                    self.assertEqual(expected.style_id, actual.style_id)

    def test_run_style_matches_python_docx(self):
        for paragraph in self.paragraphs:
            for run in paragraph.runs:
                with self.subTest(text=run.text[:8]):
                    expected = run.style
                    actual = fr._run_style(run)
                    self.assertIsNotNone(actual)
                    self.assertEqual(expected.style_id, actual.style_id)

    def test_base_style_matches_python_docx(self):
        """缓存版必须与 python-docx 的 base_style 一致（缓存路径要传 part）。

        这条用例抓到过一个真 bug：`_base_style` 原来用 `style.part` 取缓存桶，
        而 python-docx 的样式对象 `_parent` 是 None、`style.part` 会抛 ValueError，
        于是每次都返回 None、**整条 basedOn 继承链被截断**。所以这里必须显式传 part，
        走缓存路径，不能退回 python-docx 原实现。
        """
        checked = 0
        for paragraph in self.paragraphs:
            part = fr._part_of(paragraph)
            self.assertIsNotNone(part)
            style = paragraph.style
            while style is not None:
                expected = style.base_style
                actual = fr._base_style(style, part)
                if expected is None:
                    self.assertIsNone(actual)
                else:
                    self.assertIsNotNone(actual)
                    self.assertEqual(expected.style_id, actual.style_id)
                    checked += 1
                style = expected
        # 默认模板里 Heading 1 基于 Normal，必须真的走到过继承链，否则用例是空的。
        self.assertGreater(checked, 0, "没有走到任何 basedOn 继承链，用例无效")

    def test_styles_element_matches_python_docx(self):
        self.assertIs(fr._styles_element(self.doc.part), self.doc.styles.element)

    def test_repeated_lookups_hit_the_cache(self):
        """同一份文档内重复取同一个样式必须是同一个对象——缓存真的生效了。"""
        paragraph = self.paragraphs[1]
        run = self.paragraphs[0].runs[0]
        part = fr._part_of(paragraph)

        first_para = fr._paragraph_style(paragraph)
        first_run = fr._run_style(run)
        first_base = fr._base_style(first_para, part)
        self.assertIsNotNone(first_base)
        for _ in range(3):
            self.assertIs(fr._paragraph_style(paragraph), first_para)
            self.assertIs(fr._run_style(run), first_run)
            self.assertIs(fr._base_style(first_para, part), first_base)

    def test_style_part_is_not_usable(self):
        """钉住踩过的坑：样式对象的 `.part` 会抛异常，别拿它当缓存键。

        哪天 python-docx 改成可用也不该"顺手"改回去——`_base_style` 依赖显式传入的
        part 才能命中缓存，这句话是给以后改代码的人看的。
        """
        style = self.paragraphs[1].style
        with self.assertRaises(ValueError):
            style.part

    def test_default_style_is_cached_per_type(self):
        """没有样式 id 的段落会落到 default_for（O(样式数) 扫描），也必须命中缓存。"""
        plain = Document()
        paragraph = plain.add_paragraph("无显式样式")
        self.assertIsNone(paragraph._p.style)
        first = fr._paragraph_style(paragraph)
        self.assertIsNotNone(first)
        self.assertIs(fr._paragraph_style(paragraph), first)
        self.assertEqual(WD_STYLE_TYPE.PARAGRAPH, first.type)


if __name__ == "__main__":
    unittest.main()
