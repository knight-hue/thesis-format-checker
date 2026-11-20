# -*- coding: utf-8 -*-
"""对话 Agent 包：ReAct 循环 + 只读工具 + 输出溯源校验。

设计约束（与项目规矩一致）：
- 只读：不提供任何写操作，改动仍由用户勾选后走 ``/api/fix``；
- 确定性优先：简单请求仍走 ``graph.py`` 的状态机，Agent 只处理复合/兜底请求；
- 输出可溯源：最终文本里的数字与规则号必须能在工具返回里找到，否则降级；
- 可回退：``config.USE_DIALOG_AGENT`` 关闭时行为与改造前完全一致。
"""

from backend.rag.dialog.agent.loop import AgentRun, run_agent, service_chat_fn
from backend.rag.dialog.agent.router import should_use_agent
from backend.rag.dialog.agent.tools import TOOL_SPECS, ToolContext, build_card_blocks

__all__ = [
    "AgentRun",
    "run_agent",
    "service_chat_fn",
    "should_use_agent",
    "TOOL_SPECS",
    "ToolContext",
    "build_card_blocks",
]
