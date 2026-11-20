# -*- coding: utf-8 -*-
"""ReAct 循环测试：用脚本化的假模型验证多步、防循环、纠正与降级。"""
import json
import unittest

from backend.rag.dialog.agent import tools as T
from backend.rag.dialog.agent.executor import ToolExecutor
from backend.rag.dialog.agent.loop import AssistantTurn, ToolCall, run_agent


def issue(rule_id="body.font_size", category="正文格式", severity="error",
          index=3, actual="10 pt", expected="12 pt", message="正文字号不符合要求",
          suggestion="请改为 12 磅", basis="正文字号：小四号字"):
    return {
        "rule_id": rule_id,
        "category": category,
        "severity": severity,
        "message": message,
        "location": {"type": "paragraph", "index": index},
        "actual": actual,
        "expected": expected,
        "suggestion": suggestion,
        "basis": basis,
        "fixable": True,
        "source": "rule_engine",
    }


def context(**kwargs):
    params = {
        "college_id": "jlu_cs",
        "paper_errors": [issue()],
        "paper_total": 1,
        "paper_unchecked": [],
        "last_listed": [],
    }
    params.update(kwargs)
    return T.ToolContext(**params)


def tool_turn(*calls):
    tool_calls = []
    for position, (name, args) in enumerate(calls):
        call_id = f"call_{position}"
        tool_calls.append(ToolCall(
            id=call_id, name=name, args=dict(args),
            raw={"id": call_id, "type": "function",
                 "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}},
        ))
    return AssistantTurn(content="", tool_calls=tool_calls)


def final_turn(conclusion, basis="正文：小四号字", suggestion="请按规范调整", cards=None):
    payload = {"conclusion": conclusion, "basis": basis,
               "suggestion": suggestion, "cards": cards or []}
    return AssistantTurn(content=json.dumps(payload, ensure_ascii=False))


class ScriptedModel:
    def __init__(self, turns):
        self.turns = list(turns)
        self.seen = []

    def __call__(self, messages):
        self.seen.append(list(messages))
        if not self.turns:
            raise AssertionError("脚本化模型已用尽，但循环仍在调用")
        return self.turns.pop(0)


class ReActLoopTest(unittest.TestCase):
    def test_tool_then_final_is_ok(self):
        model = ScriptedModel([
            tool_turn(("list_issues", {})),
            final_turn("这篇论文共检测出 1 条问题，位置在第3段。", cards=[1]),
        ])
        run = run_agent("哪里不对", context(), model)
        self.assertTrue(run.ok, run.stops)
        self.assertEqual(run.stops, "ok")
        self.assertEqual(run.trace, ["agent:list_issues"])
        self.assertIn("共检测出", run.answer["conclusion"])

    def test_unknown_number_triggers_correction(self):
        model = ScriptedModel([
            tool_turn(("get_overview", {})),
            final_turn("这篇论文共检测出 42 条问题。"),
            final_turn("这篇论文共检测出 1 条问题。"),
        ])
        run = run_agent("哪里不对", context(), model)
        self.assertTrue(run.ok, run.stops)
        self.assertEqual(run.steps, 3)

    def test_ungrounded_number_twice_degrades(self):
        model = ScriptedModel([
            final_turn("共 99 条问题。"),
            final_turn("共 88 条问题。"),
        ])
        run = run_agent("哪里不对", context(), model)
        self.assertFalse(run.ok)
        self.assertEqual(run.stops, "unverified")
        self.assertTrue(any(item["code"] == "unknown_number" for item in run.findings))

    def test_duplicate_call_is_no_progress(self):
        model = ScriptedModel([
            tool_turn(("get_overview", {})),
            tool_turn(("get_overview", {})),
        ])
        run = run_agent("哪里不对", context(), model)
        self.assertFalse(run.ok)
        self.assertEqual(run.stops, "no_progress")

    def test_max_steps_guard(self):
        turns = [
            tool_turn(("get_overview", {})),
            tool_turn(("get_coverage", {})),
            tool_turn(("rule_lookup", {"category": "正文格式"})),
            tool_turn(("get_issue", {"index": 1})),
        ]
        run = run_agent("哪里不对", context(), ScriptedModel(turns), max_steps=3)
        self.assertFalse(run.ok)
        self.assertEqual(run.stops, "max_steps")
        self.assertEqual(run.steps, 3)

    def test_invalid_json_is_retried(self):
        model = ScriptedModel([
            AssistantTurn(content="我觉得没什么问题"),
            final_turn("请到检测结果页查看问题清单。", cards=[]),
        ])
        run = run_agent("哪里不对", context(), model)
        self.assertTrue(run.ok, run.stops)

    def test_invalid_json_twice_degrades(self):
        model = ScriptedModel([AssistantTurn(content="不是 JSON"),
                               AssistantTurn(content="还不是")])
        run = run_agent("哪里不对", context(), model)
        self.assertFalse(run.ok)
        self.assertEqual(run.stops, "invalid_answer")

    def test_unknown_tool_is_reported_back(self):
        model = ScriptedModel([
            tool_turn(("drop_table", {})),
            final_turn("当前无法取得问题清单，请到检测结果页查看。", cards=[]),
        ])
        run = run_agent("哪里不对", context(), model)
        self.assertTrue(run.ok, run.stops)
        self.assertIn("agent:drop_table", run.trace)

    def test_llm_failure_degrades(self):
        def boom(_messages):
            raise RuntimeError("network down")

        run = run_agent("哪里不对", context(), boom)
        self.assertFalse(run.ok)
        self.assertEqual(run.stops, "llm_error")

    def test_verdict_flip_is_blocked(self):
        model = ScriptedModel([
            tool_turn(("get_issue", {"index": 1})),
            final_turn("符合要求，无需修改。", cards=[1]),
        ])
        run = run_agent("第1条是什么", context(), model)
        self.assertFalse(run.ok)
        self.assertEqual(run.stops, "blocked")
        self.assertTrue(any(item["code"].startswith("explanation:")
                            for item in run.findings))

    def test_basis_text_numbers_are_allowed(self):
        ctx = context(paper_errors=[issue(basis="正文行距：1.5倍行间距")])
        model = ScriptedModel([
            tool_turn(("get_issue", {"index": 1})),
            final_turn("实测 10 pt，规范要求 12 pt；模板原句提到 1.5倍行间距。", cards=[1]),
        ])
        run = run_agent("第1条是什么", ctx, model)
        self.assertTrue(run.ok, run.stops)


class ExecutorTest(unittest.TestCase):
    def test_cache_prevents_duplicate_execution(self):
        executor = ToolExecutor(context())
        first = executor.run("get_overview", {})
        second = executor.run("get_overview", {})
        self.assertFalse(first.cached)
        self.assertTrue(second.cached)
        self.assertEqual(len(executor.results), 1)

    def test_whitelist_blocks_unknown(self):
        executor = ToolExecutor(context())
        result = executor.run("rm_rf", {})
        self.assertFalse(result.ok)
        self.assertIn("未知工具", result.error)

    def test_trace_records_each_call(self):
        executor = ToolExecutor(context())
        executor.run("get_overview", {})
        executor.run("get_coverage", {})
        self.assertEqual(executor.trace, ["agent:get_overview", "agent:get_coverage"])


if __name__ == "__main__":
    unittest.main()
