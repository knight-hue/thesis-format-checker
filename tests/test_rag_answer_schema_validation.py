import unittest

from scripts.validate_rag_answer_schema import validate_record


class RAGAnswerSchemaValidationTest(unittest.TestCase):
    def test_legacy_three_field_is_valid(self):
        record = {"conclusion": "一级标题宋体三号。", "basis": "条款。", "suggestion": "设置字体。"}
        self.assertEqual(validate_record(record), [])

    def test_structured_answer_is_valid(self):
        record = {
            "conclusion": "只能陈述 PDF 层信息，不能确认 Word 设置。",
            "basis": "PDF 资源名不是 Word 设置。",
            "suggestion": "补 DOCX 观测。",
            "can_judge": False,
            "observed": ["PDF资源名=SimSun"],
            "unknown": ["word_font"],
            "answer_schema_version": "2.0",
        }
        self.assertEqual(validate_record(record), [])

    def test_can_judge_false_with_pass_is_invalid(self):
        record = {
            "conclusion": "实际格式符合规范。", "basis": "条款。", "suggestion": "保持。",
            "can_judge": False, "observed": [], "unknown": ["alignment"],
            "answer_schema_version": "2.0",
        }
        codes = [code for code, _ in validate_record(record)]
        self.assertIn("can_judge_false_but_pass", codes)

    def test_bare_observed_field_name_is_invalid(self):
        record = {
            "conclusion": "条款要求宋体。", "basis": "条款。", "suggestion": "设置。",
            "can_judge": False, "observed": ["font_name"], "unknown": ["actual_format"],
            "answer_schema_version": "2.0",
        }
        codes = [code for code, _ in validate_record(record)]
        self.assertIn("observed_missing_value", codes)

    def test_unknown_attribute_claim_is_invalid(self):
        record = {
            "conclusion": "标题居中、宋体三号。", "basis": "片段。", "suggestion": "核对。",
            "can_judge": True, "observed": [], "unknown": ["alignment", "word_font"],
            "answer_schema_version": "2.0",
        }
        codes = [code for code, _ in validate_record(record)]
        self.assertIn("unknown_attribute_claimed", codes)

    def test_schema_2_requires_can_judge(self):
        record = {
            "conclusion": "条款要求宋体三号。", "basis": "条款。", "suggestion": "设置。",
            "observed": [], "unknown": [], "answer_schema_version": "2.0",
        }
        codes = [code for code, _ in validate_record(record)]
        self.assertIn("missing_can_judge", codes)

    def test_bad_types_are_reported(self):
        record = {
            "conclusion": "条款。", "basis": "条款。", "suggestion": "设置。",
            "can_judge": "no", "observed": "PDF", "unknown": [1],
            "answer_schema_version": "3.0",
        }
        codes = [code for code, _ in validate_record(record)]
        self.assertIn("bad_can_judge", codes)
        self.assertIn("bad_observed", codes)
        self.assertIn("bad_unknown", codes)
        self.assertIn("bad_version", codes)


if __name__ == "__main__":
    unittest.main()
