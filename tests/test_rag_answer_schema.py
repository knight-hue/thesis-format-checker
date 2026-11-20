
import json
import unittest

from backend.rag.answer import parse_answer
from backend.rag.answer_guard import guard_answer


class RAGAnswerSchemaTest(unittest.TestCase):
    def test_legacy_sections_have_schema_defaults(self):
        parsed = parse_answer(
            "【结论】一级标题宋体三号。\n"
            "【规范依据】一级标题：宋体三号，居中。\n"
            "【修改建议】在 Word 中设置字体。"
        )
        self.assertEqual(parsed["conclusion"], "一级标题宋体三号。")
        self.assertIsNone(parsed["can_judge"])
        self.assertEqual(parsed["observed"], [])
        self.assertEqual(parsed["unknown"], [])
        self.assertEqual(parsed["answer_schema_version"], "1.0")

    def test_structured_json_is_parsed(self):
        raw = json.dumps({
            "can_judge": False,
            "observed": ["PDF资源名=SimSun", "实测=15.02pt"],
            "unknown": ["word_font", "alignment"],
            "conclusion": "只能陈述 PDF 层信息。",
            "basis": "PDF 资源名不是 Word 设置。",
            "suggestion": "补 DOCX 的 rFonts/sz。",
            "answer_schema_version": "2.0",
        }, ensure_ascii=False)
        parsed = parse_answer(raw)
        self.assertFalse(parsed["can_judge"])
        self.assertEqual(parsed["observed"], ["PDF资源名=SimSun", "实测=15.02pt"])
        self.assertEqual(parsed["unknown"], ["word_font", "alignment"])
        self.assertEqual(parsed["answer_schema_version"], "2.0")

    def test_sidecar_is_parsed_and_not_in_suggestion(self):
        raw = (
            "【结论】只能陈述 PDF 层信息。\n"
            "【规范依据】PDF 资源名不是 Word 设置。\n"
            "【修改建议】补 DOCX 观测。\n"
            '【结构化字段】{"can_judge": false, "observed": ["PDF资源名=SimSun"], "unknown": ["word_font"]}'
        )
        parsed = parse_answer(raw)
        self.assertFalse(parsed["can_judge"])
        self.assertEqual(parsed["observed"], ["PDF资源名=SimSun"])
        self.assertEqual(parsed["unknown"], ["word_font"])
        self.assertEqual(parsed["suggestion"], "补 DOCX 观测。")
        self.assertEqual(parsed["answer_schema_version"], "2.0")

    def test_guard_preserves_structured_fields_when_safe(self):
        parsed = {
            "conclusion": "未观测到 Word 对齐，不能确认是否符合。",
            "basis": "知识库只有规范，没有实际观测。",
            "suggestion": "补充 DOCX 观测。",
            "can_judge": False,
            "observed": ["PDF资源名=SimSun"],
            "unknown": ["word_alignment"],
            "answer_schema_version": "2.0",
        }
        result = guard_answer("实际格式怎么样？", parsed, college_id="jlu_se")
        self.assertFalse(result["changed"])
        self.assertFalse(result["can_judge"])
        self.assertEqual(result["observed"], ["PDF资源名=SimSun"])
        self.assertEqual(result["unknown"], ["word_alignment"])
        self.assertEqual(result["answer_schema_version"], "2.0")

    def test_guard_blocks_can_judge_false_but_verdict(self):
        parsed = {
            "conclusion": "实际格式符合规范。", "basis": "规范条款。", "suggestion": "保持。",
            "can_judge": False, "observed": [], "unknown": ["word_alignment"],
            "answer_schema_version": "2.0",
        }
        result = guard_answer("实际格式合规吗？", parsed, college_id="jlu_se")
        self.assertTrue(result["changed"])
        self.assertIn("schema_false_can_judge_conflict", result["reasons"])
        self.assertFalse(result["can_judge"])

    def test_guard_blocks_unknown_attribute_claim(self):
        parsed = {
            "conclusion": "标题居中、宋体三号。", "basis": "PDF 片段。", "suggestion": "核对。",
            "can_judge": True, "observed": [], "unknown": ["alignment", "word_font"],
            "answer_schema_version": "2.0",
        }
        result = guard_answer("标题格式是什么？", parsed, college_id="jlu_se")
        self.assertTrue(result["changed"])
        self.assertIn("schema_unknown_attribute_conflict", result["reasons"])

    def test_guard_marks_can_judge_false_when_rewriting(self):
        parsed = {
            "conclusion": "源论文符合规范，无需修改。",
            "basis": "规范条款。",
            "suggestion": "无需修改。",
            "can_judge": True,
            "observed": [],
            "unknown": [],
            "answer_schema_version": "2.0",
        }
        result = guard_answer("论文合规吗？", parsed, college_id="jlu_se")
        self.assertTrue(result["changed"])
        self.assertFalse(result["can_judge"])
        self.assertIn("不能据此判断", result["conclusion"])


if __name__ == "__main__":
    unittest.main()
