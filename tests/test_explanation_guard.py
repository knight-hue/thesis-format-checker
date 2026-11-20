import unittest

from backend.rag.explanation_guard import (
    check_explanation,
    repair_explanation,
    tool_fields,
)


def context(actual="编号后是 1 个半角空格", expected="编号后空一个汉字位置",
            verdict="mismatch"):
    return {
        "tool_result": {
            "verdict": verdict,
            "fields": [{"rule_id": "caption.number_space", "field": "number_space",
                        "actual": actual, "expected": expected}],
        }
    }


def answer(conclusion, basis="图号和图题名之间：空一个汉字", suggestion="请改为空一个汉字位置。"):
    return {"conclusion": conclusion, "basis": basis, "suggestion": suggestion}


class ExplanationGuardTests(unittest.TestCase):
    def test_complete_answer_is_unchanged(self):
        item = answer("不符合。当前为编号后是 1 个半角空格；要求是编号后空一个汉字位置。")
        result = repair_explanation(item, context())
        self.assertFalse(result["changed"])
        self.assertFalse(result["blocked"])
        self.assertEqual(check_explanation(item, context()), [])

    def test_missing_actual_is_appended(self):
        result = repair_explanation(answer("不符合。编号后是半角空格。"), context())
        self.assertTrue(result["changed"])
        self.assertIn("工具观测：编号后是 1 个半角空格", result["answer"]["conclusion"])
        self.assertNotIn("missing_actual",
                         {item["code"] for item in result["remaining"]})

    def test_missing_expected_is_appended_to_suggestion(self):
        result = repair_explanation(
            answer("不符合。编号后是 1 个半角空格。", suggestion="请调整空格。"), context())
        self.assertIn("要求：编号后空一个汉字位置", result["answer"]["suggestion"])
        self.assertNotIn("missing_expected",
                         {item["code"] for item in result["remaining"]})

    def test_repeated_clause_is_collapsed(self):
        repeated = answer("不符合。当前为Keywords:；当前为Keywords:；当前为Keywords:。")
        result = repair_explanation(repeated, {
            "tool_result": {"verdict": "mismatch", "fields": [{
                "actual": "Keywords:", "expected": "Keywords："}]}})
        conclusion = result["answer"]["conclusion"]
        self.assertEqual(conclusion.count("当前为Keywords:"), 1)
        self.assertNotIn("；。", conclusion)
        self.assertTrue(conclusion.endswith("。"))

    def test_unbalanced_quote_is_balanced(self):
        result = repair_explanation(
            answer("不符合。该段使用了半角标点（“）；要求为全角标点。"), context())
        conclusion = result["answer"]["conclusion"]
        self.assertEqual(conclusion.count("“"), conclusion.count("”"))

    def test_verdict_flip_is_blocked_not_rewritten(self):
        flipped = answer("符合。")
        result = repair_explanation(flipped, context())
        self.assertTrue(result["blocked"])
        self.assertFalse(result["changed"])
        self.assertEqual(result["answer"]["conclusion"], "符合。")

    def test_reversed_suggestion_is_swapped_into_correct_direction(self):
        item = answer(
            "不符合。编号后是 1 个半角空格，要求是编号后空一个汉字位置。",
            suggestion="将编号后空一个汉字位置调整为编号后是 1 个半角空格。")
        result = repair_explanation(item, context())
        self.assertFalse(result["blocked"])
        self.assertTrue(result["changed"])
        self.assertEqual(
            result["answer"]["suggestion"],
            "将编号后是 1 个半角空格调整为编号后空一个汉字位置。")
        self.assertNotIn("suggestion_reversed",
                         {item["code"] for item in result["remaining"]})

    def test_long_reversed_suggestion_is_still_blocked(self):
        """取值过长时换位会读不通，宁可阻断也不硬改。"""
        long_actual = "1. 格式转换。在数据预处理的过程中，对数据进行归一化处理"
        item = answer(
            "不符合。列表总项序号应为1．；当前为1.。",
            suggestion="将1．改为" + long_actual + "。")
        result = repair_explanation(item, {
            "tool_result": {"verdict": "mismatch",
                            "fields": [{"actual": long_actual, "expected": "1．"}]}})
        self.assertTrue(result["blocked"])
        self.assertFalse(result["changed"])

    def test_non_fixed_shape_reversal_is_still_blocked(self):
        """非「将A调整为B」定式不换位，保持原来的阻断语义。"""
        item = answer(
            "不符合。编号后是 1 个半角空格，要求是编号后空一个汉字位置。",
            suggestion="编号后空一个汉字位置应改为编号后是 1 个半角空格。")
        result = repair_explanation(item, context())
        self.assertTrue(result["blocked"])

    def test_correct_suggestion_direction_is_not_blocked(self):
        item = answer(
            "不符合。编号后是 1 个半角空格，要求是编号后空一个汉字位置。",
            suggestion="将编号后是 1 个半角空格调整为编号后空一个汉字位置。")
        result = repair_explanation(item, context())
        self.assertFalse(result["blocked"])

    def test_reference_period_suggestion_is_rewritten_when_malformed(self):
        context = {"tool_result": {"verdict": "mismatch", "fields": [
            {"rule_id": "reference.entry_period", "field": "entry_period",
             "actual": "2012.", "expected": "．"}]}}
        item = {
            "conclusion": "不符合。参考文献条目应以句点“．”结束，当前为2012.。",
            "basis": "参考文献条目结尾必须是句点",
            "suggestion": "将参考文献条目以句点“．”结束调整为．。",
        }
        result = repair_explanation(item, context)
        self.assertFalse(result["blocked"])
        self.assertEqual(result["answer"]["suggestion"],
                         "将参考文献条目结尾的半角句点（.）改为全角句点（．）。")
        self.assertNotIn("要求：．", result["answer"]["suggestion"])

    def test_readable_reference_period_suggestion_is_kept(self):
        context = {"tool_result": {"verdict": "mismatch", "fields": [
            {"rule_id": "reference.entry_period", "field": "entry_period",
             "actual": "2012.", "expected": "．"}]}}
        item = {
            "conclusion": "不符合。参考文献条目应以全角句点“．”结束，当前为2012.。",
            "basis": "参考文献条目结尾必须是句点",
            "suggestion": "将参考文献条目结尾改为全角句点（．）。",
        }
        result = repair_explanation(item, context)
        self.assertEqual(result["answer"]["suggestion"],
                         "将参考文献条目结尾改为全角句点（．）。")

    def test_reference_period_rewrite_does_not_touch_other_rules(self):
        item = answer("不符合。编号后是 1 个半角空格，要求是编号后空一个汉字位置。")
        result = repair_explanation(item, context())
        self.assertNotIn("参考文献", result["answer"]["suggestion"])

    def test_invalid_answer_is_blocked(self):
        result = repair_explanation({"conclusion": "不符合"}, context())
        self.assertTrue(result["blocked"])
        self.assertFalse(result["changed"])

    def test_no_tool_fields_has_no_missing_findings(self):
        self.assertEqual(tool_fields({"tool_result": {}}), ("", "", ""))
        self.assertEqual(check_explanation(answer("不符合。"), {"tool_result": {}}), [])

    def test_punctuation_name_counts_as_actual(self):
        item = answer("不符合。关键词分隔符为分号，要求为逗号。")
        self.assertEqual(check_explanation(item, context(actual="；", expected="，")), [])

    def test_punctuation_mark_counts_as_actual(self):
        item = answer("不符合。当前为；，要求为，。")
        self.assertEqual(check_explanation(item, context(actual="；", expected="，")), [])

    def test_short_punctuation_fragment_is_not_appended(self):
        item = answer("不符合。标点使用了半角字符，应为全角标点。")
        result = repair_explanation(item, context(actual=")是", expected="全角标点"))
        self.assertFalse(result["changed"])


if __name__ == "__main__":
    unittest.main()
