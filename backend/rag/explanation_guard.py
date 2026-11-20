# -*- coding: utf-8 -*-
"""Deterministic post-processing for three-field explanation answers.

This is a safety/consistency layer, not a model-quality improvement:

- append the tool's observed/expected value when the model omitted it;
- collapse repeated clauses;
- drop an unmatched Chinese quote;
- refuse to rewrite an answer that flips a mismatch into a pass.

The public three-field contract (conclusion/basis/suggestion) is preserved.
Callers decide whether to use the repaired answer or fall back to a template.
"""
from __future__ import annotations

import re

FINDING_CODES = (
    "invalid_answer",
    "missing_actual",
    "missing_expected",
    "verdict_flip",
    "suggestion_reversed",
    "repeated_clause",
    "unbalanced_quote",
)

_POSITIVE_START = re.compile(r"^\s*(符合|合规|合格|通过|无需修改)")
_QUOTE_OPEN = "“"
_QUOTE_CLOSE = "”"
_PUNCT_NAMES = {
    "；": "分号", ";": "分号",
    "，": "逗号", ",": "逗号",
    "：": "冒号", ":": "冒号",
    "．": "句点", ".": "句点",
    "（": "左括号", "(": "左括号",
    "）": "右括号", ")": "右括号",
}


def _clean(text):
    return re.sub(r"[\s，。；：、（）()\[\]【】“”\"']", "", text or "")


def _numbers(text):
    return [round(float(value), 2) for value in re.findall(r"\d+(?:\.\d+)?", text or "")]


def _contains(haystack, needle):
    """Literal or numeric containment, aligned with the offline scorer's intent."""
    haystack = str(haystack or "")
    needle = str(needle or "")
    if not needle:
        return True
    normalized_needle = _clean(needle)
    if normalized_needle and normalized_needle in _clean(haystack):
        return True
    needle_numbers = _numbers(needle)
    if needle_numbers:
        haystack_numbers = _numbers(haystack)
        return all(any(abs(value - other) <= 0.01 for other in haystack_numbers)
                   for value in needle_numbers)
    return False


def tool_fields(context):
    tool = (context or {}).get("tool_result") or {}
    fields = tool.get("fields") or []
    if not fields:
        return "", "", str(tool.get("verdict") or "")
    first = fields[0] or {}
    return (str(first.get("actual") or ""), str(first.get("expected") or ""),
            str(tool.get("verdict") or ""))


def _informative(value):
    """Skip short punctuation fragments such as ')是' or '成('."""
    if re.search(r"\d", value or ""):
        return True
    text = re.sub(r"[^\u4e00-\u9fffA-Za-z]", "", value or "")
    return len(text) >= 2


def _punctuation_explained(actual, text):
    """A single punctuation mark is covered when its name or the mark is named."""
    name = _PUNCT_NAMES.get(actual)
    if name is None:
        return False
    return name in (text or "") or actual in (text or "")


def _dedupe_repeated(text):
    text = text or ""
    segments = re.split(r"([；;])", text)
    output = []
    previous = None
    for index in range(0, len(segments), 2):
        segment = segments[index]
        delimiter = segments[index + 1] if index + 1 < len(segments) else ""
        key = _clean(segment)
        if key and key == previous:
            continue
        output.append(segment + delimiter)
        previous = key
    deduped = "".join(output)
    # Collapse a repeated multi-character phrase inside one clause.
    deduped = re.sub(r"(.{8,40}?)\1{1,}", r"\1", deduped)
    # Removing the last duplicate may leave a dangling separator.
    if text.rstrip().endswith("。"):
        deduped = deduped.rstrip("；; \t")
        if not deduped.endswith("。"):
            deduped += "。"
    return deduped


def _balance_quotes(text):
    text = text or ""
    while text.count(_QUOTE_OPEN) > text.count(_QUOTE_CLOSE):
        index = text.rfind(_QUOTE_OPEN)
        text = text[:index] + text[index + 1:]
    while text.count(_QUOTE_CLOSE) > text.count(_QUOTE_OPEN):
        index = text.rfind(_QUOTE_CLOSE)
        text = text[:index] + text[index + 1:]
    return text


def _suggestion_reversed(suggestion, actual, expected):
    """A mismatch suggestion must move actual -> expected, not expected -> actual."""
    if not actual or not expected or actual == expected:
        return False
    actual_at = suggestion.find(actual)
    expected_at = suggestion.find(expected)
    if actual_at < 0 or expected_at < 0 or expected_at > actual_at:
        return False
    return bool(re.search(r"(调整|改为|改成|修改|替换)", suggestion))


_SWAP_LINK = re.compile(r"[\s，,、]*(?:调整为|改为|改成|修改为|替换为|更新为)[\s，,]*")
# 取值过长时换位会得到读不通的长句（例如整段被截断的正文），此时宁可阻断。
_MAX_SWAP_VALUE = 24

# 参考文献条目结尾必须是全角句点；模型常把「现状片段」当修改目标，写出
# 「将参考文献条目以句点“．”结束调整为．」这类不通的句子。这类句式有固定答案，
# 直接换成规范表述，比阻断或原样返回更有用。
_REFERENCE_PERIOD_RULE = "reference.entry_period"
_REFERENCE_PERIOD_SUGGESTION = "将参考文献条目结尾的半角句点（.）改为全角句点（．）。"


def repair_reference_period(suggestion, context):
    """Fix a malformed "end the reference entry with a period" suggestion.

    Only applies to ``reference.entry_period`` when the value recorded as actual
    ends with a half-width dot and the expected mark is the full-width one —
    anything else returns ``None`` so the caller keeps the model's wording.
    """
    fields = ((context or {}).get("tool_result") or {}).get("fields") or []
    if not fields:
        return None
    first = fields[0] or {}
    if str(first.get("rule_id") or "") != _REFERENCE_PERIOD_RULE:
        return None
    expected = str(first.get("expected") or "")
    actual = str(first.get("actual") or "")
    if expected != "．" or not actual.endswith("."):
        return None
    text = suggestion or ""
    if not text:
        return None
    malformed = (actual in text
                 or re.search(r"调整为\s*[．.]", text) is not None
                 or re.search(r"(?:改为|改成)\s*[．.]", text) is not None)
    if not malformed:
        return None
    return _REFERENCE_PERIOD_SUGGESTION


def repair_direction(suggestion, actual, expected):
    """Turn「把要求改成现状」into「把现状改成要求」。

    Only fixed-shape sentences are touched: the suggestion contains both actual and
    expected, expected comes first, and the gap between them is a single swap verb.
    Anything else returns ``None`` so callers keep the old fail-safe (downgrade).
    """
    if not suggestion or not actual or not expected or actual == expected:
        return None
    if len(actual) > _MAX_SWAP_VALUE or len(expected) > _MAX_SWAP_VALUE:
        return None
    actual_at = suggestion.find(actual)
    expected_at = suggestion.find(expected)
    if actual_at < 0 or expected_at < 0 or expected_at > actual_at:
        return None
    between = suggestion[expected_at + len(expected):actual_at]
    if not _SWAP_LINK.fullmatch(between):
        return None
    swapped = (suggestion[:expected_at] + actual + between
               + expected + suggestion[actual_at + len(actual):])
    if _suggestion_reversed(swapped, actual, expected):
        return None
    return swapped


def check_explanation(answer, context):
    """Return a list of finding dicts; does not mutate the answer."""
    if not isinstance(answer, dict) or not all(
            isinstance(answer.get(key), str) and answer.get(key).strip()
            for key in ("conclusion", "basis", "suggestion")):
        return [{"code": "invalid_answer", "field": ""}]

    conclusion = answer["conclusion"]
    suggestion = answer["suggestion"]
    actual, expected, verdict = tool_fields(context)
    findings = []

    if actual and _informative(actual) and not _contains(conclusion, actual) and not (
            _punctuation_explained(actual, conclusion)):
        findings.append({"code": "missing_actual", "field": "conclusion"})
    if expected and _informative(expected) and not _contains(
            conclusion + suggestion, expected) and not (
            _punctuation_explained(expected, conclusion + suggestion)):
        findings.append({"code": "missing_expected", "field": "conclusion/suggestion"})
    if verdict == "mismatch" and _POSITIVE_START.match(conclusion):
        findings.append({"code": "verdict_flip", "field": "conclusion"})
    if verdict == "mismatch" and _suggestion_reversed(suggestion, actual, expected):
        findings.append({"code": "suggestion_reversed", "field": "suggestion"})

    for field in ("conclusion", "basis", "suggestion"):
        value = answer[field]
        if _dedupe_repeated(value) != value:
            findings.append({"code": "repeated_clause", "field": field})
        if value.count(_QUOTE_OPEN) != value.count(_QUOTE_CLOSE):
            findings.append({"code": "unbalanced_quote", "field": field})
    return findings


def _append_evidence(text, evidence, prefix):
    text = text.rstrip()
    if not text:
        return prefix + evidence + "。"
    separator = "" if text[-1] in "。！？；" else "；"
    return text + separator + prefix + evidence + "。"


def repair_explanation(answer, context):
    """Mechanically repair safe issues; refuse when the verdict itself is wrong.

    Returns {"answer", "findings", "remaining", "changed", "blocked"}.
    """
    findings = check_explanation(answer, context)
    codes = {item["code"] for item in findings}
    actual, expected, _ = tool_fields(context)
    if "invalid_answer" in codes or "verdict_flip" in codes:
        return {
            "answer": dict(answer or {}),
            "findings": findings,
            "remaining": findings,
            "changed": False,
            "blocked": True,
        }

    repaired = {key: str(answer.get(key) or "") for key in
                ("conclusion", "basis", "suggestion")}
    for field in ("conclusion", "basis", "suggestion"):
        repaired[field] = _balance_quotes(_dedupe_repeated(repaired[field]))

    if "suggestion_reversed" in codes:
        # 定式方向反转就换位修好；换不了（长句 / 非定式）仍然阻断，交调用方降级。
        swapped = repair_direction(repaired["suggestion"], actual, expected)
        if swapped is None:
            return {
                "answer": dict(answer or {}),
                "findings": findings,
                "remaining": findings,
                "changed": False,
                "blocked": True,
            }
        repaired["suggestion"] = swapped

    if "missing_actual" in codes and actual:
        repaired["conclusion"] = _append_evidence(
            repaired["conclusion"], actual, "工具观测：")
    if "missing_expected" in codes and expected:
        repaired["suggestion"] = _append_evidence(
            repaired["suggestion"], expected, "要求：")

    # 句点句式放在最后：规范表述本身已包含「全角句点（．）」，
    # 覆盖掉前面可能追加的「要求：．」，避免出现重复尾巴。
    period_fix = repair_reference_period(repaired["suggestion"], context)
    if period_fix:
        repaired["suggestion"] = period_fix

    remaining = check_explanation(repaired, context)
    return {
        "answer": repaired,
        "findings": findings,
        "remaining": remaining,
        "changed": repaired != {key: str(answer.get(key) or "") for key in repaired},
        "blocked": any(item["code"] in ("verdict_flip", "suggestion_reversed")
                       for item in remaining),
    }
