# -*- coding: utf-8 -*-
"""2026-11-17 术语策展决策落地后的策略行为测试。"""
import unittest

from backend.detection.semantic.terminology import evaluate_document, load_policy
from backend.detection.semantic.terminology import collect_occurrences


def doc(*blocks):
    return {
        "blocks": [{"index": index, "kind": kind, "text": text}
                   for index, (kind, text) in enumerate(blocks)],
        "sections": [],
    }


def by_token(decisions, token):
    return [item for item in decisions if item.occurrence.token == token]


class CurationExemptionTest(unittest.TestCase):
    """专名豁免：首次出现不展开也不判违规。"""

    CASES = [
        ("WXML", "涉及到的语言有WXML，SCSS，JavaScript，HTML，CSS。"),
        ("WXSS", "以WXSS语言来呈现页面样式。"),
        ("SCSS", "样式使用SCSS编写，并在构建阶段编译。"),
        ("JDBC", "MyBatis 简化了JDBC操作流程。"),
        ("SARSA", "早期研究集中在Q-learning和SARSA等基础算法上。"),
        ("XPBD", "Macklin 等人后续进一步提出XPBD，引入柔度参数。"),
        ("JSX", "React 的JSX语法更适合动态生成复杂题目结构。"),
        ("OWL", "采用本体语言OWL进行实现，结合RDF三元组模型。"),
    ]

    def test_proper_names_are_not_applicable(self):
        for token, text in self.CASES:
            with self.subTest(token=token):
                decisions = by_token(evaluate_document(doc(("body", text))), token)
                self.assertEqual(1, len(decisions), text)
                self.assertEqual("not_applicable", decisions[0].verdict, text)

    def test_exemption_does_not_leak_to_unrelated_tokens(self):
        """策展豁免是裸词精确匹配，不能顺带放过同句其他未收录缩写。"""
        decisions = by_token(evaluate_document(doc(
            ("body", "以WXML搭建页面，用SS加载样式。"))), "SS")
        self.assertEqual(1, len(decisions))
        self.assertEqual("unknown", decisions[0].verdict, "WXML 的豁免不应波及 SS")


class CurationExplainTest(unittest.TestCase):
    """需解释术语：首次出现无释义即判违规。"""

    CASES = [
        ("DAG", "卷积网络提取 DAG 结构特征，用于任务调度。"),
        ("TEE", "架实现 TEE 环境下的定制化文件系统。"),
        ("AIGC", "现阶段AIGC技术早已脱离单一图像生成工具的定位。"),
        ("NPC", "NPC批量生成、游戏素材管理等应用场景。"),
        ("TTL", "缓存条目的TTL决定何时过期。"),
        ("PLM", "该方案定位于PLM解决方案。"),
        ("MOOP", "该问题属于 MOOP 范畴，需要同时优化多个目标。"),
        ("PWA", "该系统使用PWA技术加载页面。"),
    ]

    def test_missing_gloss_is_mismatch(self):
        for token, text in self.CASES:
            with self.subTest(token=token):
                decisions = by_token(evaluate_document(doc(("body", text))), token)
                self.assertEqual(1, len(decisions), text)
                self.assertEqual("mismatch", decisions[0].verdict, text)

    def test_explained_first_occurrence_is_match(self):
        text = "面向对象设计遵循DRY，也就是不要重复自己，减少重复实现。"
        decisions = by_token(evaluate_document(doc(("body", text))), "DRY")
        self.assertEqual(1, len(decisions))
        self.assertEqual("match", decisions[0].verdict)


class CurationKeepHumanTest(unittest.TestCase):
    """2026-11-18 裁定后，原先「保持 human」的词已逐条改判，这里锁定新的结论。"""

    def test_formally_ambiguous_tokens_now_have_rulings(self):
        for token, text, expected in (
            ("GL", "该插件名为 ECharts GL，用于三维绘图。", "not_applicable"),
            ("TB", "系统需要处理 TB 级地理数据。", "not_applicable"),
            ("LTI", "想用一种叫LTI的技术，把学习工具连起来。", "mismatch"),
        ):
            with self.subTest(token=token):
                decisions = by_token(evaluate_document(doc(("body", text))), token)
                for item in decisions:
                    self.assertEqual(expected, item.verdict,
                                     "%s 应按 2026-11-18 裁定判 %s" % (token, expected))

    def test_kept_human_token_with_gloss_is_match(self):
        """保持 human 只表示不做全局豁免；原文真给了全称仍然算合规。"""
        decisions = by_token(evaluate_document(doc(
            ("body", "LTI（Learning Tools Interoperability）是学习工具互操作标准。"))), "LTI")
        self.assertEqual(1, len(decisions))
        self.assertEqual("match", decisions[0].verdict)


class PolicyShapeTest(unittest.TestCase):
    def test_curated_terms_are_registered_once(self):
        policy = load_policy()
        rulings = [item["term"] for item in policy.data["proper_name_rulings"]]
        manual = list(policy.gate["manual_non_exempt"])
        for term in ("WXML", "WXSS", "SCSS", "JDBC", "SARSA", "XPBD", "JSX", "OWL"):
            self.assertIn(term, rulings)
            self.assertNotIn(term, manual)
        for term in ("DAG", "TEE", "AIGC", "NPC", "TTL", "PLM", "MOOP", "PWA"):
            self.assertIn(term, manual)
            self.assertNotIn(term, rulings)

    def test_all_rulings_carry_reason(self):
        policy = load_policy()
        for item in policy.data["proper_name_rulings"]:
            self.assertTrue(str(item.get("reason") or "").strip(), item.get("term"))


if __name__ == "__main__":
    unittest.main()
