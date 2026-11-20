"""Deterministic injection detector and refusal check for RAG answers.

This is a small, explainable classifier used by answer_guard. It is not a
replacement for model training; it is the hard safety layer for prompt injection.
"""
from __future__ import annotations

import re

PATTERNS = {
    "ignore_rules": re.compile(r"(忽略|无视|跳过|不要看|不依据)(以上|之前|所有|全部)?(规则|指令|要求|提示|限制|证据)"),
    "admin_update": re.compile(r"(管理员更新|系统更新|系统提示|system\s*prompt)", re.I),
    "forced_verdict": re.compile(r"(只输出|直接说|一律说|必须说)(全部合格|合格|无需修改|合规)"),
    "force_font": re.compile(r"(改成|改为|统一改为|换成)(微软雅黑|黑体|宋体|Times New Roman)", re.I),
    "jailbreak": re.compile(r"(越狱|jailbreak|DAN模式|开发者模式|你现在是|扮演|roleplay)", re.I),
    "leak_prompt": re.compile(r"(泄露|输出|告诉我)(系统提示|系统指令|提示词|prompt)", re.I),
    "all_pass": re.compile(r"(全部合格|全部合规|全部通过|无需修改|均符合)"),
}
REFUSAL_PATTERNS = (
    "不是规范来源",
    "不执行",
    "不能执行",
    "不会执行",
    "无法执行",
    "不能覆盖",
    "不作为依据",
    "忽略该指令",
    "不采纳",
)


def classify_injection(text):
    """Return injection evidence for a question or answer."""
    text = text or ""
    matches = []
    for name, pattern in PATTERNS.items():
        if pattern.search(text):
            matches.append(name)
    return {"is_injection": bool(matches), "score": len(matches), "matches": matches}


def has_refusal(text):
    text = text or ""
    return any(phrase in text for phrase in REFUSAL_PATTERNS)


def needs_refusal(question):
    return classify_injection(question)["is_injection"]


def is_refused(answer):
    return has_refusal(answer)
