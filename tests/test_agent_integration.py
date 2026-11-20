# -*- coding: utf-8 -*-
"""Agent 接入测试：结果封装、会话写回、失败降级。

不启动 KB、不连真实模型：用桩对象直接调用 ``RAGService._answer_agent``，
验证「成功时产出 agent 结果、失败时返回 None 让调用方回落」这条契约。
"""
import json
import time
import unittest
from types import SimpleNamespace

from backend.rag.rag_service import RAGService
from backend.rag.dialog.agent.loop import AssistantTurn, ToolCall


def issue(rule_id="body.font_size", category="正文格式", severity="error",
          index=3, actual="10 pt", expected="12 pt", message="正文字号不符合要求",
          suggestion="请改为 12 磅", basis="正文字号：小四号字"):
    return {
        "rule_id": rule_id, "category": category, "severity": severity,
        "message": message, "location": {"type": "paragraph", "index": index},
        "actual": actual, "expected": expected, "suggestion": suggestion,
        "basis": basis, "fixable": True, "source": "rule_engine",
    }


class StubSessionManager:
    def __init__(self, session):
        self.session = dict(session or {})
        self.history = []

    def get_session(self, _conversation_id):
        return dict(self.session)

    def update_history(self, _conversation_id, question, answer):
        self.history.append((question, answer))


class StubLLM:
    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = 0

    def chat_with_tools(self, _messages, _tools):
        self.calls += 1
        if not self.turns:
            raise AssertionError("模型脚本已用尽")
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn


def assistant_with_tool(name, args):
    call = ToolCall(id="c1", name=name, args=dict(args),
                    raw={"id": "c1", "type": "function",
                         "function": {"name": name,
                                      "arguments": json.dumps(args, ensure_ascii=False)}})
    return AssistantTurn(content="", tool_calls=[call])


def assistant_final(conclusion, basis="正文：小四号字", suggestion="请按规范调整", cards=None):
    return AssistantTurn(content=json.dumps(
        {"conclusion": conclusion, "basis": basis,
         "suggestion": suggestion, "cards": cards or []}, ensure_ascii=False))


def make_service(session, llm):
    kb = SimpleNamespace(colleges={
        "jlu_cs": {"college_name": "计算机科学与技术学院"},
        "jlu_se": {"college_name": "软件学院"},
    })
    session_manager = StubSessionManager(session)
    service = SimpleNamespace(
        kb_manager=kb,
        session_manager=session_manager,
        llm=llm,
        stats={"total_time": 0.0},
        answer=lambda *_args, **_kwargs: {},
    )
    service._make_result = lambda *a, **kw: RAGService._make_result(service, *a, **kw)
    return service


def session_payload():
    return {
        "paper_errors": [issue()],
        "paper_total": 1,
        "paper_checked": True,
        "paper_unchecked": [],
        "last_listed": [],
        "last_scope": {},
    }


class AnswerAgentTest(unittest.TestCase):
    def _intent(self):
        return SimpleNamespace(name="paper_detail", slots={"scope": "all"},
                               reason="R5 类目定位")

    def test_successful_run_returns_agent_result(self):
        llm = StubLLM([
            assistant_with_tool("get_overview", {}),
            assistant_final("这篇论文共检测出 1 条问题。"),
        ])
        service = make_service(session_payload(), llm)
        result = RAGService._answer_agent(
            service, "jlu_cs", "哪里有问题", "conv-1", self._intent(), time.time())

        self.assertIsNotNone(result)
        self.assertEqual(result["answer_source"], "agent")
        self.assertEqual(result["intent"], "paper_detail")
        self.assertIn("共检测出", result["conclusion"])
        self.assertEqual(result["trace"], ["agent:get_overview"])
        self.assertTrue(result["blocks"])
        # 统计必须从本次结果累加；不直接断言 > 0，因为 time.time() 在 Windows
        # 上分辨率约 1ms，桩 Agent 可能在同一 tick 内跑完导致耗时精确为 0.0。
        self.assertGreaterEqual(result["total_time"], 0)
        self.assertEqual(service.stats["total_time"], result["total_time"])

    def test_history_is_written(self):
        llm = StubLLM([
            assistant_with_tool("get_overview", {}),
            assistant_final("这篇论文共检测出 1 条问题。"),
        ])
        service = make_service(session_payload(), llm)
        RAGService._answer_agent(
            service, "jlu_cs", "哪里有问题", "conv-1", self._intent(), time.time())
        self.assertEqual(len(service.session_manager.history), 1)
        self.assertEqual(service.session_manager.history[0][0], "哪里有问题")

    def test_cards_become_blocks(self):
        llm = StubLLM([
            assistant_with_tool("get_issue", {"index": 1}),
            assistant_final("实测 10 pt，低于要求的 12 pt。", cards=[1]),
        ])
        service = make_service(session_payload(), llm)
        result = RAGService._answer_agent(
            service, "jlu_cs", "第1条是什么", "conv-1", self._intent(), time.time())
        self.assertEqual(len(result["blocks"]), 1)
        self.assertEqual(result["blocks"][0]["rule_id"], "body.font_size")
        self.assertEqual(result["actions"], [{"type": "open-result"}])

    def test_llm_failure_degrades_to_none(self):
        llm = StubLLM([RuntimeError("network down")])
        service = make_service(session_payload(), llm)
        result = RAGService._answer_agent(
            service, "jlu_cs", "哪里有问题", "conv-1", self._intent(), time.time())
        self.assertIsNone(result)
        self.assertEqual(service.session_manager.history, [])

    def test_hallucinated_number_degrades_to_none(self):
        llm = StubLLM([
            assistant_with_tool("get_overview", {}),
            assistant_final("共 99 条问题。"),
            assistant_final("共 88 条问题。"),
        ])
        service = make_service(session_payload(), llm)
        result = RAGService._answer_agent(
            service, "jlu_cs", "哪里有问题", "conv-1", self._intent(), time.time())
        self.assertIsNone(result)

    def test_without_paper_still_answers_spec(self):
        llm = StubLLM([
            assistant_with_tool("get_coverage", {}),
            assistant_final("当前没有需要说明的未检条款。"),
        ])
        service = make_service({"paper_errors": [], "paper_total": 0}, llm)
        result = RAGService._answer_agent(
            service, "jlu_cs", "哪些没查", "conv-1", self._intent(), time.time())
        self.assertIsNotNone(result)
        self.assertEqual(result["answer_source"], "agent")


class RouterIntegrationTest(unittest.TestCase):
    def test_agent_disabled_by_default(self):
        from backend import config
        from backend.rag.dialog.agent.router import should_use_agent
        from backend.rag.dialog.intent import Intent
        self.assertFalse(config.USE_DIALOG_AGENT)
        self.assertFalse(should_use_agent(
            Intent("spec_qa", {}, 0.3, "R9 兜底"), "随便问问", True))


if __name__ == "__main__":
    unittest.main()
