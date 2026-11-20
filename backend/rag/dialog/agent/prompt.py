# -*- coding: utf-8 -*-
"""Agent 提示词与最终答案解析。

原则：**模型不掌握事实，只编排工具**。所有数字、规则号、条数都必须来自工具返回；
最终答案里的每一条结论都要能用工具结果核对（见 output_guard）。
"""
from __future__ import annotations

import json
import re

from backend.rag.dialog.agent.tools import dumps, snapshot_for_prompt, ToolContext

FINAL_FIELDS = ("conclusion", "basis", "suggestion")

SYSTEM_PROMPT = """你是论文格式检测系统里的助手，只能通过调用工具来获取事实，不能凭记忆或推测作答。

硬性规则：
1. 任何数字（条数、字号、磅值、页码等）、规则号、位置，都必须来自工具返回的结果；工具没给你的，一律不许写。
2. 「哪些没查」只能来自 get_coverage，绝不能把未检查的条款说成已通过。
3. 系统不会自动修改论文；被问到怎么改时，只描述需要改成什么，不要声称已经改好。
4. 一次没拿到足够信息时，继续调用工具；最多 4 步。
5. 工具报错时不要编造结果，可以换一个工具或参数再试一次。

什么时候调用哪个工具：
- 问整体情况（哪里不对、总共多少）→ get_overview
- 问某一类/某一级别有哪些 → list_issues（severity / category / rule_ids 可组合）
- 问第 N 条、这条 → get_issue
- 问怎么改、能不能自动改 → get_fix
- 问依据哪句模板 → get_basis
- 问哪些没查 → get_coverage
- 问对应哪条规则 → rule_lookup
- 问规范本身（要求是什么、多少号字）→ search_spec

需要同时说明多件事时，可以连续调用多个工具，最后一次性作答。

最终回答必须只输出一个 JSON 对象，不要 Markdown 代码块：
{"conclusion": "结论", "basis": "依据（可留空）", "suggestion": "建议（可留空）", "cards": [条目序号]}

cards 只能填工具返回过的条目 index；不确定就留空数组。
"""


def build_initial_messages(question: str, ctx: ToolContext) -> list[dict]:
    """system + 一条 user 消息；user 里只放会话快照，不放论文原文。"""
    payload = {
        "question": str(question or "").strip(),
        "session": snapshot_for_prompt(ctx),
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": dumps(payload)},
    ]


def tool_result_message(call_id: str, name: str, payload: dict) -> dict:
    return {
        "role": "tool",
        "tool_call_id": str(call_id or name),
        "name": name,
        "content": dumps(payload),
    }


def assistant_tool_call_message(content: str, tool_calls: list[dict]) -> dict:
    return {
        "role": "assistant",
        "content": content or "",
        "tool_calls": tool_calls,
    }


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.S)


def parse_final_answer(content: str) -> dict | None:
    """解析最终答案。解析失败返回 None（调用方据此降级）。"""
    text = (content or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    if not text.startswith("{"):
        match = _JSON_BLOCK_RE.search(text)
        if not match:
            return None
        text = match.group(0)
    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    answer = {}
    for key in FINAL_FIELDS:
        value = data.get(key)
        if value is None:
            value = ""
        if not isinstance(value, str):
            return None
        answer[key] = value.strip()
    if not answer["conclusion"]:
        return None
    cards = data.get("cards")
    if not isinstance(cards, list):
        cards = []
    clean_cards: list[int] = []
    for item in cards:
        if isinstance(item, bool):
            continue
        if isinstance(item, int) and item > 0:
            clean_cards.append(item)
        elif isinstance(item, str) and item.strip().isdigit():
            clean_cards.append(int(item.strip()))
    answer["cards"] = clean_cards[:5]
    return answer
