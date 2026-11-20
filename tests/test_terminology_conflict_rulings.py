# -*- coding: utf-8 -*-
"""2026-11-18 标签/ gate 分歧裁定的回归测试（12 条改 gate）。"""
import unittest

from backend.detection.semantic.terminology import evaluate_document, load_policy


def doc(*blocks):
    return {"blocks": [{"index": index, "kind": kind, "text": text}
                       for index, (kind, text) in enumerate(blocks)], "sections": []}


def verdict(text, token):
    decisions = [d for d in evaluate_document(doc(("body", text)))
                 if d.occurrence.token == token]
    return decisions[0].verdict if decisions else "no-decision"


class ExplainRulingsTest(unittest.TestCase):
    """标签判违规、gate 原本无法判定 → 现在判违规。"""

    def test_gdbt_is_a_core_term(self):
        self.assertEqual("mismatch", verdict(
            "建立逻辑回归、随机森林、GDBT、LightGBM 模型并进行优化。", "GDBT"))

    def test_lti_is_a_core_term(self):
        self.assertEqual("mismatch", verdict(
            "想用一种叫LTI的技术，把学习工具连起来。", "LTI"))

    def test_ct_is_no_longer_exempt_as_proper_name(self):
        self.assertEqual("mismatch", verdict(
            "通过患者的病史、肺部X光片或CT检查大致诊断。", "CT"))


class ExemptRulingsTest(unittest.TestCase):
    """标签判不适用、gate 原本走 human → 现在判不适用。"""

    def test_mp_is_a_model_proper_name(self):
        self.assertEqual("not_applicable", verdict(
            "心理学家McColloch和数学家Pitts发表了神经模型MP。", "MP"))

    def test_tb_is_a_common_unit(self):
        self.assertEqual("not_applicable", verdict(
            "实现 TB 级地理数据的实时渲染。", "TB"))

    def test_model_context_code_name_is_proper_noun(self):
        self.assertEqual("not_applicable", verdict(
            "朱金阳构建中医药领域关系抽取模型RLTP，创建知识图谱。", "RLTP"))

    def test_multiword_product_fragment_is_exempt(self):
        # 候选过滤后要么不出现在决策里，要么显式判不适用；都不能判违规。
        self.assertIn(verdict("基于 ECharts GL 开源库构建可视化引擎。", "GL"),
                      ("not_applicable", "no-decision"))

    def test_citation_author_year_is_exempt(self):
        self.assertIn(verdict("例如，KATOMAA（2018）针对低资源语言的研究。", "KATOMAA"),
                      ("not_applicable", "no-decision"))

    def test_quoted_example_is_exempt(self):
        self.assertIn(verdict("例外现象（如 “Mr.”、“UFO” 等缩写词的词性判断）难以覆盖。", "UFO"),
                      ("not_applicable", "no-decision"))


class GlossRulingsTest(unittest.TestCase):
    """标签判合规、gate 原本判违规 → 现在识别为有释义。"""

    def test_chinese_full_name_then_english_then_token(self):
        self.assertEqual("match", verdict(
            "知识图谱也就是KnowledgeGraph，KG[1]，作为组织知识的技术。", "KG"))

    def test_chinese_full_name_then_token(self):
        self.assertEqual("match", verdict(
            "随着中文自然语言处理也就是NLP 以及医疗人工智能技术持续进展。", "NLP"))

    def test_adjacent_chinese_name_without_brackets(self):
        self.assertEqual("match", verdict(
            "离散化版本像隐马尔可夫模型HMM 或者更一般的连续形式。", "HMM"))


class PolicyRegistrationTest(unittest.TestCase):
    def test_rulings_are_registered_in_expected_sets(self):
        policy = load_policy()
        manual = {str(x).upper() for x in policy.gate["manual_non_exempt"]}
        for term in ("GDBT", "LTI", "CT"):
            self.assertIn(term, manual)
        self.assertNotIn("CT", {str(x).upper() for x in policy.gate["proper_names"]})
        self.assertIn("MP", policy.gate["model_proper_names"])
        ubiquitous = {str(x).upper() for x in policy.gate["ubiquitous"]}
        for unit in ("KB", "MB", "GB", "TB", "PB"):
            self.assertIn(unit, ubiquitous)
        self.assertEqual({"GL", "KATOMAA", "UFO"},
                         {str(x).upper() for x in policy.gate["non_term_examples"]})


if __name__ == "__main__":
    unittest.main()
