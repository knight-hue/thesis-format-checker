# -*- coding: utf-8 -*-
"""路由决策：什么时候该交给 Agent。

默认策略 ``complex``：
- 规则识别的兜底结果（``R9``）且会话里有检测结果 → 交给 Agent；
- 一轮问了多个问题（``R0`` 澄清）→ 交给 Agent 尝试一次答完；
- 命中复合表述（整理/排序/最严重/能自动改…）→ 交给 Agent。

其余情况一律维持现有确定性链路：它更快、更准、可复现。
"""
from __future__ import annotations

# 单一命中即视为复合请求
_STRONG_MARKERS = (
    "能自动改", "可以自动改", "自动修复", "整理", "汇总", "排序",
    "最严重", "分别列出", "各自", "按类别", "按严重", "对比",
)

# 需要累计两个才视为复合请求（单个太容易误伤）
_WEAK_MARKERS = ("然后", "同时", "并且", "以及", "另外", "还有", "剩下的")

_PAPER_INTENTS = ("paper_overview", "paper_detail", "paper_fix",
                  "paper_basis", "paper_coverage")


def agent_mode() -> str:
    """读取运行期开关，便于测试时直接改 config 模块属性。"""
    try:
        from backend import config
        return str(getattr(config, "AGENT_ROUTE", "complex") or "complex").strip().lower()
    except Exception:  # pragma: no cover - 配置异常时按关闭处理
        return "off"


def agent_enabled() -> bool:
    try:
        from backend import config
        return bool(getattr(config, "USE_DIALOG_AGENT", False))
    except Exception:  # pragma: no cover
        return False


def is_complex_question(question: str) -> bool:
    text = str(question or "")
    if any(marker in text for marker in _STRONG_MARKERS):
        return True
    hits = sum(1 for marker in _WEAK_MARKERS if marker in text)
    return hits >= 2


def should_use_agent(intent, question: str, has_paper: bool) -> bool:
    """是否把这一轮交给 Agent。"""
    if not agent_enabled():
        return False
    mode = agent_mode()
    if mode == "off":
        return False
    name = str(getattr(intent, "name", "") or "")
    slots = getattr(intent, "slots", None) or {}
    reason = str(getattr(intent, "reason", "") or "")

    if mode == "all":
        return True

    if name in _PAPER_INTENTS and not has_paper:
        # 没有检测结果时交给状态机的 paper_missing，Agent 无从下手
        return False

    if name == "clarify" and slots.get("kind") == "multi_question":
        return True
    if has_paper and reason.startswith("R9"):
        return True
    if mode == "fallback":
        return False
    return is_complex_question(question)
