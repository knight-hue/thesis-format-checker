# -*- coding: utf-8 -*-
"""图内条款（模板 §5.3「图内字体字号：宋体五号字」）的判据与边界。

分成两半：
- **矢量图形里的文字**（形状/文本框，`w:txbxContent` 内的段落）：字体名与字号在
  docx 里明确写着 → 精确判定，且用户勾选后可由写回层改（只改中文槽）；
- **位图**：图内文字在图片上只是像素，字体名无处可读、字号无法从像素可靠反推
  → **不判定**，只在 `unchecked` 里说明“这条没查、请人工核对”，且**不含任何数量或
  字号估计**（曾经给过，实测那些数字量的是行结构高度而不是字高）。
"""
import os
import unittest

from backend.detection.rule_engine import RuleEngine
from backend.docx_common.blocks import is_inside_textbox

from backend.college_registry import path_of

RULES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "config", "rules")


def load_rules(college="jlu_cs"):
    import io
    import json
    with io.open(path_of(college, "rules", RULES_DIR),
                 encoding="utf-8") as handle:
        return json.load(handle)


def figure_text_block(index, runs, text=None):
    return {"type": "paragraph", "index": index, "kind": "body",
            "figure_text": True, "alignment": "center",
            "text": text if text is not None else
                    "".join(r.get("text") or "" for r in runs),
            "runs": runs}


def run(text, *, cjk="宋体", size=10.5, latin="Times New Roman"):
    return {"text": text, "font_name": latin, "east_asia_font": cjk,
            "font_size_pt": size}


def ids(document, college="jlu_cs"):
    return [issue["rule_id"] for issue in
            RuleEngine(load_rules(college)).check(document)["issues"]]


class VectorFigureTextTest(unittest.TestCase):
    """矢量图形里的图内文字：按模板「图内字体字号：宋体五号字」精确判定。"""

    def test_compliant_text_passes(self):
        block = figure_text_block(0, [run("采样流程")])
        self.assertEqual([], [i for i in ids({"blocks": [block]})
                              if i.startswith("figure.")])

    def test_wrong_font_is_reported(self):
        block = figure_text_block(0, [run("采样流程", cjk="黑体")])
        self.assertIn("figure.inner_east_asia_font", ids({"blocks": [block]}))

    def test_wrong_size_is_reported(self):
        block = figure_text_block(0, [run("采样流程", size=14.0)])
        self.assertIn("figure.inner_font_size", ids({"blocks": [block]}))

    def test_whitespace_run_is_skipped(self):
        """空白 run 不渲染可见字符，它的字体字号不参与判定（与正文口径一致）。"""
        block = figure_text_block(0, [run("采样流程"), run("   ", cjk="黑体", size=20)])
        self.assertEqual([], [i for i in ids({"blocks": [block]})
                              if i.startswith("figure.")])

    def test_body_rules_do_not_apply_to_figure_text(self):
        """图内段落不套正文规则：字号 10.5 在正文里是错，在图内是对的。"""
        block = figure_text_block(0, [run("采样流程", cjk="宋体", size=10.5)])
        result = ids({"blocks": [block]})
        self.assertNotIn("body.font_size", result)
        self.assertNotIn("body.east_asia_font", result)

    def test_specific_kind_wins_over_figure_text(self):
        """已被识别成更具体类型的段落不归图内规则抢。

        文本框里写着“注：…”的段落是注释（模板对注释有专门的起排/字体/段前规定），
        实测 1 份样张 2 段：若被图内规则接管，会丢掉 8 条注释类问题、换成 2 条图内问题。
        """
        block = figure_text_block(0, [run("注：上海市连续五年人口自然增长率为负",
                                          cjk="宋体", size=8.0)])
        block["kind"] = "note"
        result = ids({"blocks": [block]})
        self.assertNotIn("figure.inner_font_size", result)
        self.assertIn("note.prefix", result)

    def test_latin_slot_is_not_judged(self):
        """模板对图内西文/数字没有规定，不能拿中文槽的要求去套西文。"""
        block = figure_text_block(0, [run("CNN 模型", cjk="宋体", size=10.5,
                                          latin="Calibri")])
        self.assertNotIn("figure.inner_east_asia_font", ids({"blocks": [block]}))


class FigureInnerFixMappingTest(unittest.TestCase):
    """修复映射：图内中文字体必须只改中文槽。"""

    def test_suffix_resolves_to_east_asia_font(self):
        from backend.fix._core import _resolve_format

        scope, key, _parser = _resolve_format("figure.inner_east_asia_font")
        self.assertEqual("run", scope)
        self.assertEqual("east_asia_font", key)

    def test_generic_font_suffix_is_not_used(self):
        """收尾是 "font" 的名字会走 run_both（中英文一起改），图内规则不许用它。"""
        from backend.fix._core import _resolve_format

        self.assertEqual("run_both", _resolve_format("figure.inner_font")[0])

    def test_fixable_inside_vector_figure(self):
        from backend.fix.fixability import classify_fixability

        issue = {"rule_id": "figure.inner_east_asia_font", "source": "rule_engine",
                 "location": {"type": "paragraph", "index": 3}}
        fixable, _reason = classify_fixability(issue)
        self.assertTrue(fixable)

    def test_bitmap_figure_is_manual_with_reason(self):
        """位图的图内文字改不了：必须给“请重新插入”的具体原因，而不是空话术。"""
        from backend.fix.fixability import classify_fixability

        issue = {"rule_id": "figure.inner_font_size", "source": "rule_engine",
                 "location": {"type": "image", "index": 5}}
        fixable, reason = classify_fixability(issue)
        self.assertFalse(fixable)
        self.assertIn("位图", reason)
        self.assertIn("重新插入", reason)


class BitmapFigureUncheckedTest(unittest.TestCase):
    """位图：只说明“这条没查、请人工核对”，不给任何估计值。"""

    def _unchecked(self, blocks):
        from backend.detection.checker_service import check_document

        result = check_document({"sections": [], "blocks": blocks}, "jlu_cs",
                                rag_service=None, include_semantic=False,
                                rules_dir=RULES_DIR)
        # include_semantic=False 时 formula_axis 那条不会出现，这里只剩图内这条。
        return {item["rule_id"]: item["reason"] for item in result["unchecked"]}

    def test_bitmap_figure_gets_manual_check_note(self):
        block = {"type": "image", "index": 3, "align": "center",
                 "width_inch": 3.0, "height_inch": 2.0}
        reason = self._unchecked([block])["figure.inner_font_size"]
        self.assertIn("位图", reason)
        self.assertIn("请人工核对", reason)

    def test_manual_check_note_contains_no_digit(self):
        """提示里**一个数字都不许有**。

        曾写过「N 张可量出字高，估约 X pt（区间 a–b pt，±2 pt）」：实测那些数字量的
        是行结构高度而非字高，属报错与事实不符。后来改成「约 10%」（排版学的一般性
        数据）也一并删掉——**任何数字都会被读成"程序量过了"**，而这条恰恰是"没量"。
        所以口径放宽到：不出现任何阿拉伯数字，而不只是不出现某几个词。
        """
        import re

        block = {"type": "image", "index": 3, "align": "center",
                 "width_inch": 3.0, "height_inch": 2.0}
        reason = self._unchecked([block])["figure.inner_font_size"]
        self.assertIsNone(re.search(r"\d", reason),
                          "未检说明里出现了数字：%r" % reason)

    def test_no_unchecked_reason_contains_a_digit(self):
        """契约级不变量：**所有**未检说明都不许出现阿拉伯数字。

        上一条只盯图内那一条规则；这里盯整个 `unchecked` 通道（含坐标轴那条）。
        并且先断言"确实取到了多条"——守护用例若在空列表上通过，等于没守。
        """
        import re

        from backend.detection.checker_service import check_document

        block = {"type": "image", "index": 3, "align": "center",
                 "width_inch": 3.0, "height_inch": 2.0}
        # rag_service=None 且 include_semantic=True：坐标轴那条也会进 unchecked。
        result = check_document({"sections": [], "blocks": [block]}, "jlu_cs",
                                rag_service=None, include_semantic=True,
                                rules_dir=RULES_DIR)
        items = result["unchecked"]
        self.assertGreaterEqual(len(items), 2,
                                "本用例要求同时出现图内与坐标轴两条，实际：%r" % items)
        for item in items:
            self.assertIsNone(re.search(r"\d", item["reason"]),
                              "%s 的说明里出现了数字：%r"
                              % (item["rule_id"], item["reason"]))

    def test_axis_note_appears_when_ai_is_unavailable(self):
        """AI 不可用时，「坐标轴」那条**必须**出现在未检里。

        它的开关原先是 `rules["formula"]["axis"] is not None`，而两份规则文件里都
        没有这个键 → 条件恒为假 → 这条说明从来不出现，坐标轴就成了静默漏检
        （正是 `unchecked` 通道要防的事）。开关改成"依据表里有这句原句"后才成立，
        这里钉住它，防止再被换成任何恒假的条件。
        """
        from backend.detection.checker_service import check_document

        block = {"type": "image", "index": 3, "align": "center",
                 "width_inch": 3.0, "height_inch": 2.0}
        result = check_document({"sections": [], "blocks": [block]}, "jlu_cs",
                                rag_service=None, include_semantic=True,
                                rules_dir=RULES_DIR)
        ids = [item["rule_id"] for item in result["unchecked"]]
        self.assertIn("formula_axis", ids)
        # AI 可用时这条规则照常执行，不该再报"未检"。
        with_ai = check_document({"sections": [], "blocks": [block]}, "jlu_cs",
                                 rag_service=object(), include_semantic=False,
                                 rules_dir=RULES_DIR)
        self.assertNotIn("formula_axis",
                         [item["rule_id"] for item in with_ai["unchecked"]])

    def test_frontend_mock_note_stays_in_sync(self):
        """mock 里那条文案是前端离线开发的副本，必须与后端一字不差。

        副本漂移过一次（后端已改成无数字，mock 还留着「约 10%」），所以逐字比对。
        """
        import io

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        # 前端已迁移为 TypeScript（2026-10-23），mock 文件同步改名 index.js -> index.ts。
        path = os.path.join(root, "frontend", "src", "mock", "index.ts")
        with io.open(path, encoding="utf-8") as handle:
            source = handle.read()
        from backend.detection.figure_image.service import _REASON

        self.assertIn(_REASON, source,
                      "frontend/src/mock/index.ts 的未检文案与后端不一致")

    def test_inline_and_front_matter_images_are_not_figures(self):
        inline = {"type": "image", "index": 0, "inline": True, "width_inch": 0.5}
        front = {"type": "image", "index": 1, "front_matter": True, "width_inch": 2.0}
        # 正文标点那条“未检说明”与图片无关（见 checker_service），比较时排除。
        notes = self._unchecked([inline, front])
        self.assertEqual({"body.fullwidth_punct"}, set(notes))

    def test_vector_figure_text_gets_no_note(self):
        block = figure_text_block(0, [run("采样流程")])
        # 正文标点那条“未检说明”与图片无关，比较时排除。
        self.assertEqual({"body.fullwidth_punct"}, set(self._unchecked([block])))


class TextboxDetectionTest(unittest.TestCase):
    """`w:txbxContent` 内的段落要能被认出来（判据在 docx_common/blocks.py）。"""

    def test_textbox_paragraph_is_detected(self):
        from docx.oxml import parse_xml

        W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        xml = ('<w:p xmlns:w="%s"><w:r><w:drawing><wps:txbx '
               'xmlns:wps="http://schemas.microsoft.com/office/word/2010/'
               'wordprocessingShape"><w:txbxContent>'
               '<w:p><w:r><w:t>图内文字</w:t></w:r></w:p>'
               '</w:txbxContent></wps:txbx></w:drawing></w:r></w:p>' % W)
        outer = parse_xml(xml)
        inner = outer.find(".//{%s}txbxContent" % W).find("{%s}p" % W)
        self.assertTrue(is_inside_textbox(inner))
        self.assertFalse(is_inside_textbox(outer))

    def test_body_paragraph_is_not_figure_text(self):
        from docx.oxml import parse_xml

        W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        paragraph = parse_xml('<w:p xmlns:w="%s"><w:r><w:t>正文</w:t></w:r></w:p>' % W)
        self.assertFalse(is_inside_textbox(paragraph))


if __name__ == "__main__":
    unittest.main()
