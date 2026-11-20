import unittest


class _Completions:
    def __init__(self, captured):
        self.captured = captured

    def create(self, **kwargs):
        self.captured.update(kwargs)
        message = type("Message", (), {"content": "ok"})()
        choice = type("Choice", (), {"message": message})()
        return type("Response", (), {"choices": [choice]})()


def _service(captured):
    from backend.rag.llm_service import LLMService

    client = type("Client", (), {
        "base_url": "http://127.0.0.1:11434/v1",
        "chat": type("Chat", (), {"completions": _Completions(captured)})(),
    })()
    return LLMService(client=client, llm_model="test")


class ExplanationMessageRoleTests(unittest.TestCase):
    def test_generate_messages_keeps_system_and_user_roles(self):
        captured = {}
        service = _service(captured)
        text = service.generate_messages([
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "user question"},
        ])
        self.assertEqual(text, "ok")
        self.assertEqual([m["role"] for m in captured["messages"]],
                         ["system", "user"])

    def test_generate_wraps_single_user_message(self):
        captured = {}
        service = _service(captured)
        self.assertEqual(service.generate("hello"), "ok")
        self.assertEqual(captured["messages"],
                         [{"role": "user", "content": "hello"}])


if __name__ == "__main__":
    unittest.main()
