# -*- coding: utf-8 -*-
"""输出校验测试：数字与规则号溯源、条数一致、单条解释方向。"""
import unittest

from backend.rag.dialog.agent import output_guard as G


PAYLOAD = {
    "total": 12,
    "matched_total": 4,
    "stored": 12,
    "category_counts": {"正文格式": 7, "表": 5},
    "items": [{"index": 1, "rule_id": "body.font_size",
               "actual": "10 pt", "expected": "12 pt",
               "suggestion": "请改为 12 磅"}],
}


def answer(conclusion="", basis="", suggestion="", cards=None):
    return {"conclusion": conclusion, "basis": basis,
            "suggestion": suggestion, "cards": cards or []}


class ProvenanceTest(unittest.TestCase):
    def test_grounded_answer_passes(self):
        self.assertEqual(G.check_output(
            answer("共 12 条问题，正文格式 7 条，表 5 条。"), [PAYLOAD]), [])

    def test_unknown_number_flagged(self):
        findings = G.check_output(answer("共 99 条问题。"), [PAYLOAD])
        self.assertTrue(any(item["code"] == "unknown_number" for item in findings))

    def test_number_inside_tool_text_allowed(self):
        payload = {"text": "规范要求 1.5 倍行间段"}
        self.assertEqual(G.check_output(answer("模板要求 1.5 倍行距。"), [payload]), [])

    def test_unknown_rule_id_flagged(self):
        findings = G.check_output(answer("问题出在 body.line_spacing。"), [PAYLOAD])
        self.assertTrue(any(item["code"] == "unknown_rule_id" for item in findings))

    def test_known_rule_id_allowed(self):
        self.assertEqual(G.check_output(answer("问题出在 body.font_size。"), [PAYLOAD]), [])

    def test_count_mismatch_flagged(self):
        findings = G.check_output(answer("共 5 条问题。"), [PAYLOAD])
        self.assertTrue(any(item["code"] == "count_mismatch" for item in findings))

    def test_invalid_answer_flagged(self):
        findings = G.check_output({"conclusion": ""}, [PAYLOAD])
        self.assertTrue(G.is_blocked(findings))

    def test_findings_are_deduplicated(self):
        findings = G.check_output(
            answer("共 99 条问题，共 99 条问题。", basis="也是 99 条"), [PAYLOAD])
        keys = [(item["code"], item.get("field"), item.get("value")) for item in findings]
        self.assertEqual(len(keys), len(set(keys)))

    def test_describe_is_actionable(self):
        text = G.describe([{"code": "unknown_number", "value": "99"}])
        self.assertIn("99", text)
        self.assertIn("不在工具返回里", text)

    def test_number_from_question_is_allowed(self):
        # 用户问「第 99 条」，模型回答里提到 99 属于对话上文，不是编造
        findings = G.check_output(answer("没有第 99 条，本次共 12 条问题。"), [PAYLOAD],
                                  question="第 99 条是什么？")
        self.assertEqual(findings, [])

    def test_number_not_in_question_still_flagged(self):
        findings = G.check_output(answer("没有第 98 条。"), [PAYLOAD], question="第 99 条是什么？")
        self.assertTrue(any(item["code"] == "unknown_number" for item in findings))


class ExplanationConsistencyTest(unittest.TestCase):
    def test_verdict_flip_blocked(self):
        findings = G.check_output(
            answer("符合要求，无需修改。", "正文：小四号字", "请改为 12 磅", cards=[1]),
            [PAYLOAD])
        self.assertTrue(G.is_blocked(findings))

    def test_reversed_suggestion_blocked(self):
        output = answer("实测 10 pt，不符合 12 pt 的要求。", "正文：小四号字",
                        "请把 12 pt 改为 10 pt。", cards=[1])
        findings = G.check_output(output, [PAYLOAD])
        self.assertTrue(any(item["code"] == "explanation:suggestion_reversed"
                            for item in findings))

    def test_normal_explanation_passes(self):
        output = answer("实测 10 pt，低于要求的 12 pt。", "正文：小四号字",
                        "请把 10 pt 改为 12 pt。", cards=[1])
        self.assertEqual(G.check_output(output, [PAYLOAD]), [])

    def test_multi_card_skips_explanation_check(self):
        output = answer("符合要求。", "正文：小四号字", "请改为 12 磅", cards=[1, 2])
        findings = G.check_output(output, [PAYLOAD])
        self.assertFalse(any(item["code"].startswith("explanation:") for item in findings))


class RepairOutputTest(unittest.TestCase):
    REVERSED_PAYLOAD = {
        "total": 1,
        "matched_total": 1,
        "items": [{"index": 1, "rule_id": "caption.number_space",
                   "actual": "编号后是 1 个半角空格",
                   "expected": "编号后空一个汉字位置"}],
    }

    def test_reversed_fixed_suggestion_is_swapped(self):
        output = answer(
            "实测编号后是 1 个半角空格，不符合编号后空一个汉字位置的要求。",
            "图号和图题名之间：空一个汉字",
            "将编号后空一个汉字位置调整为编号后是 1 个半角空格。",
            cards=[1])
        repaired, findings = G.repair_output(output, [self.REVERSED_PAYLOAD])
        self.assertEqual(repaired["suggestion"],
                         "将编号后是 1 个半角空格调整为编号后空一个汉字位置。")
        self.assertFalse(G.is_blocked(findings))

    def test_unsupported_suggestion_still_blocked(self):
        output = answer(
            "实测编号后是 1 个半角空格，不符合编号后空一个汉字位置的要求。",
            "图号和图题名之间：空一个汉字",
            "编号后空一个汉字位置应改为编号后是 1 个半角空格。",
            cards=[1])
        repaired, findings = G.repair_output(output, [self.REVERSED_PAYLOAD])
        self.assertEqual(repaired["suggestion"], output["suggestion"])
        self.assertTrue(G.is_blocked(findings))

    def test_verdict_flip_is_not_repaired(self):
        output = answer("符合要求，无需修改。", "正文：小四号字", "请改为 12 磅", cards=[1])
        repaired, findings = G.repair_output(output, [PAYLOAD])
        self.assertTrue(G.is_blocked(findings))
        self.assertEqual(repaired["conclusion"], "符合要求，无需修改。")


if __name__ == "__main__":
    unittest.main()
