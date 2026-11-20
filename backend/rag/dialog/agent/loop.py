# -*- coding: utf-8 -*-
"""ReAct 主循环：Thought → Action → Observation，最多 4 步。

设计要点：
- **可注入**：``chat_fn`` 是普通函数，测试里用脚本化的假模型即可，不需要真实 API；
- **防循环**：同参数重复调用直接吃缓存；一整步全是缓存命中即判定无进展并终止；
- **输出校验**：最终答案必须通过 ``output_guard``；不合格先给一次纠正机会，再失败降级；
- **可降级**：任何一步失败都返回 ``answer=None``，调用方回落到确定性链路。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from backend.rag.dialog.agent import prompt as agent_prompt
from backend.rag.dialog.agent.executor import ToolExecutor
from backend.rag.dialog.agent.output_guard import repair_output, describe, is_blocked
from backend.rag.dialog.agent.tools import TOOL_SPECS, ToolContext

DEFAULT_MAX_STEPS = 4
MAX_CORRECTIONS = 1


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict
    raw: dict = field(default_factory=dict)


@dataclass
class AssistantTurn:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def has_tools(self) -> bool:
        return bool(self.tool_calls)


@dataclass
class AgentRun:
    """一次 Agent 运行的结果。``answer`` 为 None 表示调用方应当降级。"""

    answer: Optional[dict] = None
    trace: list[str] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)
    stops: str = "ok"
    steps: int = 0
    executor: Optional[ToolExecutor] = None

    @property
    def ok(self) -> bool:
        return self.answer is not None


def turn_from_message(message: Any) -> AssistantTurn:
    """把 OpenAI SDK 的 message 转成 AssistantTurn。"""
    content = str(getattr(message, "content", "") or "")
    raw_calls = list(getattr(message, "tool_calls", None) or [])
    calls: list[ToolCall] = []
    for index, call in enumerate(raw_calls):
        function = getattr(call, "function", None)
        name = str(getattr(function, "name", "") or "")
        raw_args = getattr(function, "arguments", "") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except Exception:
            args = {}
        if not isinstance(args, dict):
            args = {}
        call_id = str(getattr(call, "id", "") or f"call_{index}")
        calls.append(ToolCall(
            id=call_id,
            name=name,
            args=args,
            raw={"id": call_id, "type": "function",
                 "function": {"name": name,
                              "arguments": raw_args if isinstance(raw_args, str)
                              else json.dumps(raw_args, ensure_ascii=False)}},
        ))
    return AssistantTurn(content=content, tool_calls=calls)


def service_chat_fn(llm_service) -> Callable[[list[dict]], AssistantTurn]:
    """真实模型适配：走 LLMService 的 tool-calling 入口。

    兼容直接返回 ``AssistantTurn`` 的实现（测试里的脚本化模型），
    这样单测不需要构造 OpenAI SDK 的消息对象。
    """
    def _chat(messages: list[dict]) -> AssistantTurn:
        response = llm_service.chat_with_tools(messages, TOOL_SPECS)
        if isinstance(response, AssistantTurn):
            return response
        return turn_from_message(response)

    return _chat


def run_agent(question: str, ctx: ToolContext,
              chat_fn: Callable[[list[dict]], AssistantTurn],
              max_steps: int = DEFAULT_MAX_STEPS) -> AgentRun:
    """驱动 ReAct 循环。返回 AgentRun；``answer is None`` 表示需要降级。"""
    max_steps = max(1, int(max_steps or DEFAULT_MAX_STEPS))
    executor = ToolExecutor(ctx)
    messages = agent_prompt.build_initial_messages(question, ctx)
    corrections = 0
    last_findings: list[dict] = []
    steps = 0

    while steps < max_steps:
        steps += 1
        executor.begin_step(steps)
        try:
            turn = chat_fn(messages)
        except Exception:
            return AgentRun(None, executor.trace, last_findings, "llm_error", steps, executor)

        if turn.has_tools:
            messages.append(agent_prompt.assistant_tool_call_message(
                turn.content, [call.raw for call in turn.tool_calls]))
            fresh = 0
            for call in turn.tool_calls:
                result = executor.run(call.name, call.args)
                if not result.cached:
                    fresh += 1
                messages.append(agent_prompt.tool_result_message(
                    call.id, call.name, result.as_payload()))
            if fresh == 0:
                return AgentRun(None, executor.trace, last_findings, "no_progress",
                                steps, executor)
            continue

        answer = agent_prompt.parse_final_answer(turn.content)
        if answer is None:
            last_findings = [{"code": "invalid_answer", "field": ""}]
            if corrections < MAX_CORRECTIONS:
                corrections += 1
                messages.append({"role": "user", "content":
                                 "上一次输出不是合法的三字段 JSON。请只输出 "
                                 '{"conclusion":...,"basis":...,"suggestion":...,"cards":[...]}。'})
                continue
            return AgentRun(None, executor.trace, last_findings, "invalid_answer",
                            steps, executor)

        answer, findings = repair_output(answer, executor.ok_payloads, question=question)
        if not findings:
            return AgentRun(answer, executor.trace, [], "ok", steps, executor)
        last_findings = findings
        if is_blocked(findings):
            return AgentRun(None, executor.trace, findings, "blocked", steps, executor)
        if corrections < MAX_CORRECTIONS:
            corrections += 1
            messages.append({"role": "user", "content":
                             f"上一次输出未通过事实校验：{describe(findings)}。"
                             "请调用工具核对后重新给出 JSON 答案。"})
            continue
        return AgentRun(None, executor.trace, findings, "unverified", steps, executor)

    return AgentRun(None, executor.trace, last_findings, "max_steps", steps, executor)
