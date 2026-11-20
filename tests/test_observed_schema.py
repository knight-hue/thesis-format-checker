import unittest

from backend.rag.answer_guard import guard_answer
from backend.rag.observed_schema import sanitize_observed


class ObservedSchemaTest(unittest.TestCase):
    def test_bare_field_name_is_removed(self):
        parsed = {"conclusion": "字体是宋体。", "basis": "条款。", "suggestion": "核对。",
                  "can_judge": True, "observed": ["font_name", "font_size_pt"],
                  "unknown": [], "answer_schema_version": "2.0"}
        clean, issues = sanitize_observed(parsed)
        self.assertIn("observed_missing_value", issues)
        self.assertFalse(clean["can_judge"])
        self.assertEqual(clean["observed"], [])
        self.assertIn("font_name_value_missing", clean["unknown"])

    def test_values_are_preserved(self):
        parsed = {"conclusion": "字体宋体。", "basis": "条款。", "suggestion": "核对。",
                  "can_judge": True, "observed": ["word:font_name=宋体", "pdf:font_size_pt=12.0"],
                  "unknown": [], "answer_schema_version": "2.0"}
        clean, issues = sanitize_observed(parsed)
        self.assertEqual(issues, [])
        self.assertEqual(clean["observed"], parsed["observed"])
        self.assertTrue(clean["can_judge"])

    def test_guard_blocks_can_judge_true_but_uncertain(self):
        parsed = {"conclusion": "现有信息不能证明实际合规。", "basis": "条款。", "suggestion": "核对。",
                  "can_judge": True, "observed": ["word:font_name=宋体"], "unknown": [],
                  "answer_schema_version": "2.0"}
        result = guard_answer("实际格式如何？", parsed, college_id="jlu_se")
        self.assertTrue(result["changed"])
        self.assertIn("can_judge_true_but_uncertain", result["reasons"])
        self.assertFalse(result["can_judge"])

    def test_guard_blocks_can_judge_true_with_unknown(self):
        parsed = {"conclusion": "字体符合规范。", "basis": "条款。", "suggestion": "保持。",
                  "can_judge": True, "observed": ["pdf:font_resource_name=SimSun"],
                  "unknown": ["word_alignment"], "answer_schema_version": "2.0"}
        result = guard_answer("实际格式如何？", parsed, college_id="jlu_se")
        self.assertTrue(result["changed"])
        self.assertIn("can_judge_true_with_unknown", result["reasons"])
        self.assertFalse(result["can_judge"])

    def test_guard_blocks_can_judge_true_without_observed(self):
        parsed = {"conclusion": "符合规范。", "basis": "条款。", "suggestion": "保持。",
                  "can_judge": True, "observed": [], "unknown": [],
                  "answer_schema_version": "2.0"}
        result = guard_answer("实际格式如何？", parsed, college_id="jlu_se")
        self.assertTrue(result["changed"])
        self.assertIn("can_judge_true_without_observed", result["reasons"])

    def test_guard_rewrites_bare_observed(self):
        parsed = {"conclusion": "实际格式符合。", "basis": "条款。", "suggestion": "无需修改。",
                  "can_judge": True, "observed": ["font_name", "font_size_pt"],
                  "unknown": [], "answer_schema_version": "2.0"}
        result = guard_answer("实际格式如何？", parsed, college_id="jlu_se")
        self.assertTrue(result["changed"])
        self.assertIn("observed_missing_value", result["reasons"])
        self.assertFalse(result["can_judge"])
        self.assertIn("没有具体值", result["conclusion"])


if __name__ == "__main__":
    unittest.main()
