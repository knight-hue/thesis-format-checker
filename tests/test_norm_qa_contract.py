import json
import unittest
from collections import defaultdict
from unittest.mock import patch

from backend.rag.norm_qa_contract import (
    build_context,
    build_messages,
    validate_answer,
)


class _Doc:
    def __init__(self, text, chapter="正文", section="字体", full_path="正文 / 字体"):
        self.page_content = text
        self.metadata = {
            "chapter": chapter,
            "section": section,
            "full_path": full_path,
        }


class NormQAContractTests(unittest.TestCase):
    def test_context_keeps_version_and_clauses(self):
        context = build_context(
            "jlu_cs", "正文要求什么字体？",
            {"template_version": "Z-3", "effective_year": None},
            [_Doc("正文中文：宋体小四号字")],
        )
        self.assertEqual(context["college_id"], "jlu_cs")
        self.assertEqual(context["version"]["label"], "Z-3")
        self.assertIsNone(context["version"]["effective_year"])
        self.assertEqual(context["clauses"][0]["text"], "正文中文：宋体小四号字")
        self.assertEqual(context["clauses"][0]["full_path"], "正文 / 字体")

    def test_messages_keep_system_and_user_roles(self):
        context = build_context(
            "jlu_cs", "正文要求什么字体？", {"template_version": "Z-3"},
            [_Doc("正文中文：宋体小四号字")],
        )
        messages = build_messages(context)
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["question"], "正文要求什么字体？")
        self.assertEqual(payload["version"]["label"], "Z-3")
        self.assertNotIn("tool_result", payload)
        self.assertNotIn("observation", payload)

    def test_missing_question_or_clause_is_rejected(self):
        with self.assertRaises(ValueError):
            build_context("jlu_cs", "", {}, [_Doc("正文")])
        with self.assertRaises(ValueError):
            build_context("jlu_cs", "正文要求？", {}, [])

    def test_answer_must_have_three_nonempty_fields(self):
        self.assertTrue(validate_answer({
            "conclusion": "要求宋体。", "basis": "正文中文：宋体小四号字。",
            "suggestion": "按宋体设置。",
        }))
        self.assertFalse(validate_answer({"conclusion": "要求宋体。"}))
        self.assertFalse(validate_answer({
            "conclusion": "", "basis": "x", "suggestion": "y",
        }))

    def test_answer_adapter_uses_norm_qa_prompt(self):
        from backend.rag.answer import build_norm_qa_messages
        context = build_context(
            "jlu_cs", "正文要求什么字体？", {"template_version": "Z-3"},
            [_Doc("正文中文：宋体小四号字")],
        )
        messages = build_norm_qa_messages(context)
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        self.assertIn("规范问答", messages[0]["content"])


class _Retriever:
    def invoke(self, question):
        return [_Doc("正文中文：宋体小四号字")]


class _KB:
    colleges = {
        "jlu_cs": {
            "college_name": "计算机科学与技术学院",
            "template_version": "Z-3",
            "effective_year": None,
        }
    }

    def get_retriever(self, college_id):
        return _Retriever()


class _LLM:
    def __init__(self):
        self.messages = None
        self.generate_calls = 0

    def generate_messages(self, messages):
        self.messages = messages
        return json.dumps({
            "conclusion": "正文要求宋体小四号字。",
            "basis": "正文中文：宋体小四号字。",
            "suggestion": "按宋体小四号字设置。",
        }, ensure_ascii=False)

    def generate(self, prompt):
        self.generate_calls += 1
        return "{}"


class SpecOnlyIntegrationTests(unittest.TestCase):
    def test_spec_only_sends_norm_qa_system_and_user(self):
        from backend.rag import rag_service as module
        from backend.rag.rag_service import RAGService

        service = RAGService.__new__(RAGService)
        service.stats = defaultdict(float)
        service.session_manager = None
        service.cache = None
        service.query_rewriter = None
        service.kb_manager = _KB()
        service.llm = _LLM()

        with patch.object(module, "USE_DIALOG_ROUTER", False), \
                patch.object(module, "USE_QUERY_REWRITE", False):
            result = service.answer("jlu_cs", "正文要求什么字体？", spec_only=True)

        self.assertEqual([m["role"] for m in service.llm.messages], ["system", "user"])
        payload = json.loads(service.llm.messages[1]["content"])
        self.assertIn("clauses", payload)
        self.assertNotIn("tool_result", payload)
        self.assertNotIn("observation", payload)
        self.assertEqual(service.llm.generate_calls, 0)
        self.assertIn("宋体", result["conclusion"])


if __name__ == "__main__":
    unittest.main()
