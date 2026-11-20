import unittest

from backend.rag.answer import build_answer_prompt


class BuildAnswerPromptTest(unittest.TestCase):
    """非 spec_only 问答 prompt 必须可构建。

    2026-10-24 引入【结构化字段】JSON 示例时，f-string 里的单花括号会让
    ``str.format`` 把 ``"can_judge": true或false`` 当成格式说明符，导致
    ``/api/rag/answer``（spec_only=false）恒返回 400。
    """

    def test_prompt_builds_without_format_error(self):
        prompt = build_answer_prompt(
            {"context": "一级标题：黑体三号，居中。"},
            "一级标题用什么字体？",
        )
        self.assertIn("一级标题：黑体三号，居中。", prompt)
        self.assertIn("一级标题用什么字体？", prompt)

    def test_structured_field_example_keeps_literal_braces(self):
        prompt = build_answer_prompt({}, "一级标题用什么字体？")
        self.assertIn(
            '【结构化字段】{"can_judge": true或false, '
            '"observed": ["本轮实际看到的信息"], '
            '"unknown": ["无法确认的字段"]}',
            prompt,
        )

    def test_session_context_is_optional_and_rendered(self):
        without = build_answer_prompt({}, "一级标题用什么字体？")
        self.assertNotIn("# 会话上下文", without)

        with_context = build_answer_prompt(
            {}, "那二级标题呢？", "上一轮：一级标题黑体三号。"
        )
        self.assertIn("# 会话上下文", with_context)
        self.assertIn("上一轮：一级标题黑体三号。", with_context)


if __name__ == "__main__":
    unittest.main()
