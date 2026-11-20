# -*- coding: utf-8 -*-
"""输出校验：把「学生看到的每句话都能由事实推出」变成可执行的检查。

两层：
- **L1 解释一致性**：单条解释时复用 ``explanation_guard``（结论翻转、建议方向反转等）；
- **L2 工具溯源**：答案里的每个数字与规则号，必须能在本次工具返回里找到。

处置策略与 ``explanation_guard`` 保持同一套语义：
``blocked`` 的（结论翻转 / 方向反转 / 答案非法）一律不修，交调用方降级；
其余问题打回让模型重试一次，仍不合格同样降级。
"""
from __future__ import annotations

import re
from typing import Any

from backend.rag import explanation_guard

BLOCKING_CODES = frozenset({
    "invalid_answer",
    "explanation:verdict_flip",
    "explanation:suggestion_reversed",
})

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_RULE_ID_RE = re.compile(r"[a-z][a-z_0-9]*(?:\.[a-z_0-9]+)+")
_COUNT_RE = re.compile(r"共\s*(\d+)\s*条")
_TOLERANCE = 0.01
_MAX_FINDINGS = 12


def _walk(value: Any, numbers: set[float], rule_ids: set[str]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        numbers.add(round(float(value), 2))
        return
    if isinstance(value, str):
        for token in _NUMBER_RE.findall(value):
            try:
                numbers.add(round(float(token), 2))
            except ValueError:
                continue
        for token in _RULE_ID_RE.findall(value):
            rule_ids.add(token)
        return
    if isinstance(value, dict):
        for item in value.values():
            _walk(item, numbers, rule_ids)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _walk(item, numbers, rule_ids)


def collect_facts(payloads: list[dict]) -> tuple[set[float], set[str]]:
    """从工具返回里收集「允许出现的数字」与「允许出现的规则号」。"""
    numbers: set[float] = set()
    rule_ids: set[str] = set()
    for payload in payloads or []:
        _walk(payload, numbers, rule_ids)
    return numbers, rule_ids


def _allowed_total(payloads: list[dict]) -> set[int]:
    totals: set[int] = set()
    for payload in payloads or []:
        if not isinstance(payload, dict):
            continue
        for key in ("total", "matched_total", "count", "stored"):
            value = payload.get(key)
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            totals.add(int(value))
    return totals


def _number_allowed(value: float, numbers: set[float]) -> bool:
    return any(abs(value - other) <= _TOLERANCE for other in numbers)


def check_provenance(answer: dict, payloads: list[dict], question: str = "") -> list[dict]:
    """L2：数字 / 规则号 / 条数是否都能溯源。"""
    if not isinstance(answer, dict):
        return [{"code": "invalid_answer", "field": ""}]
    if not str(answer.get("conclusion") or "").strip():
        return [{"code": "invalid_answer", "field": "conclusion"}]
    numbers, rule_ids = collect_facts(payloads)
    # 用户在提问里写过的数字（如「第 99 条」）属于对话上文，不能当成模型编造。
    question_numbers, _question_rules = collect_facts([{"question": str(question or "")}])
    numbers = numbers | question_numbers
    totals = _allowed_total(payloads)
    findings: list[dict] = []
    for field in ("conclusion", "basis", "suggestion"):
        text = str(answer.get(field) or "")
        for token in _NUMBER_RE.findall(text):
            try:
                value = float(token)
            except ValueError:
                continue
            if not _number_allowed(value, numbers):
                findings.append({"code": "unknown_number", "field": field, "value": token})
        for token in _RULE_ID_RE.findall(text):
            if token not in rule_ids:
                findings.append({"code": "unknown_rule_id", "field": field, "value": token})
    for match in _COUNT_RE.finditer(str(answer.get("conclusion") or "")):
        total = int(match.group(1))
        if totals and total not in totals:
            findings.append({"code": "count_mismatch", "field": "conclusion", "value": str(total)})
    return findings[:_MAX_FINDINGS]


def _single_issue_context(cards: list[int], payloads: list[dict]) -> dict | None:
    """答案只点名一条时，取该条的 actual/expected 交给 explanation_guard。"""
    if len(cards or []) != 1:
        return None
    target = int(cards[0])
    for payload in payloads or []:
        for item in payload.get("items") or []:
            if not isinstance(item, dict) or item.get("index") != target:
                continue
            actual = str(item.get("actual") or "")
            expected = str(item.get("expected") or "")
            if not actual or not expected:
                return None
            return {
                "tool_result": {
                    "verdict": "mismatch",
                    "fields": [{"actual": actual, "expected": expected}],
                }
            }
    return None


_POSITIVE_START = re.compile(r"^\s*(符合|合规|合格|通过|无需修改)")


def _claims_verdict(answer: dict, actual: str, expected: str) -> bool:
    """只有答案确实在评判这条时，才做单条解释一致性检查。

    概览类回答（「共 1 条问题，位置在第 3 段」）并不评价该条，不应因没重复实测值被拦；
    而「符合要求」「无需修改」这类肯定句就是评价，必须检查方向是否被翻转。
    """
    conclusion = str(answer.get("conclusion") or "")
    if _POSITIVE_START.match(conclusion):
        return True
    for value in (actual, expected):
        cleaned = str(value or "").strip()
        if cleaned and cleaned in conclusion:
            return True
    return False


def check_explanation_consistency(answer: dict, payloads: list[dict]) -> list[dict]:
    """L1：单条解释复用 explanation_guard 的判定。"""
    cards = answer.get("cards") or []
    context = _single_issue_context(cards, payloads)
    if context is None:
        return []
    if not all(str(answer.get(key) or "").strip() for key in ("conclusion", "basis", "suggestion")):
        return []
    actual, expected, _verdict = explanation_guard.tool_fields(context)
    if not _claims_verdict(answer, actual, expected):
        return []
    findings = explanation_guard.check_explanation(answer, context)
    return [{"code": f"explanation:{item.get('code')}", "field": item.get("field", "")}
            for item in findings][:_MAX_FINDINGS]


def check_output(answer: dict, payloads: list[dict], question: str = "") -> list[dict]:
    """两层检查合并。返回 findings；空列表表示通过。"""
    findings = check_provenance(answer, payloads, question=question)
    findings.extend(check_explanation_consistency(answer, payloads))
    seen: set[tuple] = set()
    unique: list[dict] = []
    for item in findings:
        key = (item.get("code"), item.get("field"), item.get("value"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def is_blocked(findings: list[dict]) -> bool:
    return any(item.get("code") in BLOCKING_CODES for item in findings)


def repair_output(answer: dict, payloads: list[dict],
                  question: str = "") -> tuple[dict, list[dict]]:
    """阻断前先做确定性方向修复，返回 (答案, 剩余 findings)。

    只处理「单条解释 + 定式建议方向反转」：把「将<要求>调整为<现状>」换成
    「将<现状>调整为<要求>」。换不了就原样返回，调用方继续按 blocked 降级，
    因此这里不会把不合法的答案「修成」合法。
    """
    findings = check_output(answer, payloads, question=question)
    if not isinstance(answer, dict):
        return answer, findings
    if not any(item.get("code") == "explanation:suggestion_reversed" for item in findings):
        return answer, findings
    context = _single_issue_context(answer.get("cards") or [], payloads)
    if context is None:
        return answer, findings
    actual, expected, _verdict = explanation_guard.tool_fields(context)
    swapped = explanation_guard.repair_direction(
        str(answer.get("suggestion") or ""), actual, expected)
    if not swapped:
        return answer, findings
    repaired = dict(answer)
    repaired["suggestion"] = swapped
    return repaired, check_output(repaired, payloads, question=question)


def describe(findings: list[dict]) -> str:
    """把 findings 变成给模型看的可读提示，便于重试时自我修正。"""
    parts = []
    for item in findings:
        code = item.get("code", "")
        value = item.get("value")
        if code == "unknown_number":
            parts.append(f"数字 {value} 不在工具返回里，请不要写它")
        elif code == "unknown_rule_id":
            parts.append(f"规则号 {value} 不在工具返回里，请不要写它")
        elif code == "count_mismatch":
            parts.append(f"「共 {value} 条」与工具返回的条数不一致")
        elif code == "invalid_answer":
            parts.append("回答不是合法的三字段 JSON")
        elif code.startswith("explanation:"):
            parts.append("结论或建议的方向与工具结果矛盾")
    return "；".join(parts) or "输出未通过校验"
