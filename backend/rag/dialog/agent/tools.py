# -*- coding: utf-8 -*-
"""Agent 工具层：8 个只读工具，全部复用 facts / blocks / tools 的既有实现。

四条硬约束：
1. **只读**：不提供任何写操作（项目没有「自动改」，改动必须用户勾选后走 /api/fix）；
2. **幂等**：同样的参数返回同样的结果；
3. **分页**：``list_issues`` 单次最多返回 10 条，避免把上千条塞进模型上下文；
4. **不抛异常**：未知工具 / 参数不合法 / 底层出错，一律返回结构化错误。

工具的作用不是「产生新能力」，而是把已有的事实查询暴露给模型，让它能组合调用。
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from backend.rag.dialog import blocks, facts
from backend.rag.dialog import tools as dialog_tools

MAX_PAGE_SIZE = 10
MAX_CARDS = 5


@dataclass
class ToolContext:
    """工具运行所需的只读快照（不持有数据库连接，不写会话）。"""

    college_id: str = ""
    college_name: str = ""
    paper_errors: list = field(default_factory=list)
    paper_total: int = 0
    paper_unchecked: list | None = None
    last_listed: list = field(default_factory=list)
    last_scope: dict = field(default_factory=dict)
    spec_answer: Optional[Callable[[str], dict]] = None

    @property
    def has_paper(self) -> bool:
        return bool(self.paper_total or self.paper_errors)


@dataclass
class ToolResult:
    ok: bool
    name: str
    data: dict = field(default_factory=dict)
    error: str = ""
    cached: bool = False

    def as_payload(self) -> dict:
        if not self.ok:
            return {"ok": False, "error": self.error}
        return {"ok": True, **self.data}


# ---------------------------------------------------------------- 参数声明

_INT = "integer"
_STR = "string"
_LIST = "array"

PARAM_SPECS: dict[str, dict[str, dict]] = {
    "get_overview": {},
    "list_issues": {
        "severity": {"type": _STR, "enum": ["error", "warning", "info"]},
        "category": {"type": _STR},
        "rule_ids": {"type": _LIST, "items": _STR, "max_items": 20},
        "page": {"type": _INT, "minimum": 1, "maximum": 50},
        "page_size": {"type": _INT, "minimum": 1, "maximum": MAX_PAGE_SIZE},
    },
    "get_issue": {"index": {"type": _INT, "minimum": 1, "maximum": 9999, "required": True}},
    "get_fix": {"index": {"type": _INT, "minimum": 1, "maximum": 9999, "required": True}},
    "get_basis": {
        "index": {"type": _INT, "minimum": 1, "maximum": 9999},
        "rule_id": {"type": _STR},
    },
    "get_coverage": {},
    "rule_lookup": {
        "category": {"type": _STR},
        "rule_ids": {"type": _LIST, "items": _STR, "max_items": 20},
        "index": {"type": _INT, "minimum": 1, "maximum": 9999},
    },
    "search_spec": {"query": {"type": _STR, "required": True, "max_length": 200}},
}

_DESCRIPTIONS = {
    "get_overview": "获取本次检测的整体情况：问题总数、按类别计数。回答「哪里不对」「总共多少问题」「整体情况」时先调它。",
    "list_issues": "按严重级别/类别/规则号筛选问题，分页返回（每页最多 10 条）。这是把「哪些条目」列给用户的主要工具。",
    "get_issue": "按序号取单条问题详情：位置、实测值、规范要求、说明。序号指上一次 list_issues 或列表回答里的编号。",
    "get_fix": "按序号取单条问题的修改建议；同时给出该条是否可自动修复。回答「怎么改」「能不能自动改」时使用。",
    "get_basis": "按序号或规则号取模板原句（依据）。回答「依据哪句模板」「凭什么」时使用，只能返回已记录的 basis。",
    "get_coverage": "取本次未检查的条款清单（unchecked）。回答「哪些没查」「有没有漏检」时使用，不得把未检说成通过。",
    "rule_lookup": "查询条目对应的规则号。回答「对应哪条规则」时使用。",
    "search_spec": "在学院规范知识库里检索条款并回答「要求是什么」。回答与具体论文无关的规范问题时使用。",
}


def tool_schema(name: str) -> dict:
    """把一个工具渲染成 OpenAI function-calling 的 JSON Schema。"""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for key, spec in PARAM_SPECS[name].items():
        item: dict[str, Any] = {"type": spec["type"]}
        for extra in ("enum", "minimum", "maximum"):
            if extra in spec:
                item[extra] = spec[extra]
        if spec["type"] == _LIST:
            item["items"] = {"type": spec.get("items", _STR)}
            if "max_items" in spec:
                item["maxItems"] = spec["max_items"]
        if spec.get("max_length"):
            item["maxLength"] = spec["max_length"]
        properties[key] = item
        if spec.get("required"):
            required.append(key)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": _DESCRIPTIONS[name],
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


TOOL_SPECS: list[dict] = [tool_schema(name) for name in PARAM_SPECS]
TOOL_NAMES: frozenset[str] = frozenset(PARAM_SPECS)


def validate_args(name: str, args: Any) -> tuple[dict, str]:
    """校验工具参数。返回 (清洗后的参数, 错误说明)；错误说明非空表示不合法。"""
    if name not in PARAM_SPECS:
        return {}, f"未知工具：{name}"
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return {}, "参数必须是 JSON 对象"
    spec = PARAM_SPECS[name]
    unknown = sorted(set(args) - set(spec))
    if unknown:
        return {}, f"不支持的参数：{', '.join(unknown)}"
    clean: dict[str, Any] = {}
    for key, rule in spec.items():
        if key not in args or args[key] is None:
            if rule.get("required"):
                return {}, f"缺少必填参数：{key}"
            continue
        value = args[key]
        kind = rule["type"]
        if kind == _INT:
            if isinstance(value, bool) or not isinstance(value, int):
                return {}, f"参数 {key} 必须是整数"
            if "minimum" in rule and value < rule["minimum"]:
                return {}, f"参数 {key} 不能小于 {rule['minimum']}"
            if "maximum" in rule and value > rule["maximum"]:
                return {}, f"参数 {key} 不能大于 {rule['maximum']}"
        elif kind == _STR:
            if not isinstance(value, str):
                return {}, f"参数 {key} 必须是字符串"
            value = value.strip()
            if not value:
                continue
            if rule.get("max_length") and len(value) > rule["max_length"]:
                return {}, f"参数 {key} 过长（上限 {rule['max_length']}）"
            if rule.get("enum") and value not in rule["enum"]:
                return {}, f"参数 {key} 只能是 {rule['enum']} 之一"
        else:  # array
            if not isinstance(value, list):
                return {}, f"参数 {key} 必须是数组"
            items = [str(item).strip() for item in value if str(item).strip()]
            if not items:
                continue
            limit = rule.get("max_items")
            if limit and len(items) > limit:
                return {}, f"参数 {key} 最多 {limit} 项"
            value = items
        clean[key] = value
    return clean, ""


# ---------------------------------------------------------------- 工具实现


def _candidates(ctx: ToolContext) -> list:
    """「第 N 条」所指的顺序：优先用会话里上次列出的顺序。"""
    if ctx.last_listed:
        return list(ctx.last_listed)
    return facts._listed(facts._sort_issues(ctx.paper_errors))


def _issue_items(selected: list, start_index: int = 1) -> list[dict]:
    items = []
    for offset, issue in enumerate(selected):
        items.append({
            "index": start_index + offset,
            "rule_id": facts._rule_id(issue),
            "category": facts._category(issue),
            "severity": str(issue.get("severity") or ""),
            "location": facts._location(issue),
            "message": facts._message(issue),
            "actual": facts._actual(issue),
            "expected": facts._expected(issue),
            "suggestion": facts._suggestion(issue),
            "basis": facts._basis(issue),
            "fixable": bool(issue.get("fixable")) if issue.get("fixable") is not None else None,
        })
    return items


def _filtered_slots(severity=None, category=None, rule_ids=None, page=1) -> dict:
    # 统一用 scope=page：它会先 _filter 再排序分页；scope=all 走召回，会忽略筛选条件
    slots: dict[str, Any] = {"scope": "page", "page": max(1, int(page or 1))}
    if severity:
        slots["severity"] = severity
    if category:
        slots["category"] = category
    if rule_ids:
        slots["rule_ids"] = list(rule_ids)
    return slots


def _get_overview(ctx: ToolContext, **_kwargs) -> ToolResult:
    if not ctx.has_paper:
        # 从没检测过 ≠ 检测出 0 条问题：这里必须给「尚未检测」的事实，
        # 否则模型会把 0 当成“没有问题”。
        return ToolResult(True, "get_overview", {
            "has_paper": False,
            "total": 0,
            "college_id": ctx.college_id,
            "college_name": ctx.college_name,
            "text": facts.build_missing_paper(),
        })
    issues = [item for item in (ctx.paper_errors or []) if isinstance(item, dict)]
    counts = Counter(facts._category(issue) for issue in issues)
    text, _listed = facts.build_overview(ctx.paper_errors, ctx.paper_total)
    return ToolResult(True, "get_overview", {
        "has_paper": True,
        "total": int(ctx.paper_total or 0),
        "stored": len(issues),
        "college_id": ctx.college_id,
        "college_name": ctx.college_name,
        "category_counts": dict(counts),
        "text": text,
        "unchecked_count": len(ctx.paper_unchecked or []),
    })


def _list_issues(ctx: ToolContext, severity=None, category=None, rule_ids=None,
                 page=1, page_size=MAX_PAGE_SIZE, **_kwargs) -> ToolResult:
    if not ctx.has_paper:
        return ToolResult(False, "list_issues", {}, "当前会话还没有检测结果")
    slots = _filtered_slots(severity, category, rule_ids, page)
    selected, reason, meta = facts.select_issues(ctx.paper_errors, slots, "")
    offset = int(meta.get("offset") or 0)
    size = min(int(page_size or MAX_PAGE_SIZE), MAX_PAGE_SIZE)
    selected = selected[:size]
    return ToolResult(True, "list_issues", {
        "matched_total": int(meta.get("matched_total") or 0),
        "page": int(meta.get("page") or 1),
        "offset": offset,
        "returned": len(selected),
        "items": _issue_items(selected, offset + 1),
        "reason": reason if not selected else "",
    })


def _select_by_index(ctx: ToolContext, index: int):
    slots = {"scope": "index", "index": int(index), "candidates": _candidates(ctx)}
    return facts.select_issues(ctx.paper_errors, slots, "")


def _get_issue(ctx: ToolContext, index: int, **_kwargs) -> ToolResult:
    selected, reason, meta = _select_by_index(ctx, index)
    if not selected:
        return ToolResult(False, "get_issue", {}, reason or "没有该序号对应的条目")
    text, _listed = facts.build_detail(selected, mode="detail")
    return ToolResult(True, "get_issue", {
        "index": int(index),
        "count": len(selected),
        "items": _issue_items(selected, int(index)),
        "text": text,
        "matched_total": int(meta.get("matched_total") or 0),
    })


def _get_fix(ctx: ToolContext, index: int, **_kwargs) -> ToolResult:
    selected, reason, meta = _select_by_index(ctx, index)
    if not selected:
        return ToolResult(False, "get_fix", {}, reason or "没有该序号对应的条目")
    text, _listed = facts.build_detail(selected, mode="fix")
    return ToolResult(True, "get_fix", {
        "index": int(index),
        "count": len(selected),
        "items": _issue_items(selected, int(index)),
        "text": text,
        "matched_total": int(meta.get("matched_total") or 0),
    })


def _get_basis(ctx: ToolContext, index=None, rule_id=None, **_kwargs) -> ToolResult:
    if index is None and not rule_id:
        return ToolResult(False, "get_basis", {}, "需要提供 index 或 rule_id")
    if index is not None:
        selected, reason, _meta = _select_by_index(ctx, index)
        if not selected:
            return ToolResult(False, "get_basis", {}, reason or "没有该序号对应的条目")
    else:
        selected = [item for item in (ctx.paper_errors or [])
                    if isinstance(item, dict) and facts._rule_id(item) == rule_id]
        if not selected:
            return ToolResult(False, "get_basis", {}, f"没有规则号 {rule_id} 的条目")
    text, _listed = facts.build_basis(selected)
    notes = facts.basis_notes(selected)
    return ToolResult(True, "get_basis", {
        "count": len(selected),
        "items": [{"rule_id": facts._rule_id(item),
                   "location": facts._location(item),
                   "basis": facts._basis(item)} for item in selected],
        "text": text,
        "notes": notes,
    })


def _get_coverage(ctx: ToolContext, **_kwargs) -> ToolResult:
    unchecked = ctx.paper_unchecked if ctx.paper_unchecked is not None else []
    text = facts.build_coverage(unchecked)
    return ToolResult(True, "get_coverage", {
        "count": len(unchecked),
        "items": [{"rule_id": str(item.get("rule_id") or ""),
                   "reason": str(item.get("reason") or "")}
                  for item in unchecked if isinstance(item, dict)],
        "text": text,
    })


def _rule_lookup(ctx: ToolContext, category=None, rule_ids=None, index=None, **_kwargs) -> ToolResult:
    if index is not None:
        selected, reason, _meta = _select_by_index(ctx, index)
        slots: dict[str, Any] = {"scope": "index", "index": int(index),
                                 "candidates": _candidates(ctx)}
    else:
        slots = _filtered_slots(None, category, rule_ids, 1)
        selected, reason, _meta = facts.select_issues(ctx.paper_errors, slots, "")
    if not selected:
        return ToolResult(False, "rule_lookup", {}, reason or "没有匹配的条目")
    text, found, lookup_reason = dialog_tools.rule_lookup(ctx.paper_errors, slots, "")
    if not text:
        return ToolResult(False, "rule_lookup", {}, lookup_reason or "这些条目没有记录规则号")
    return ToolResult(True, "rule_lookup", {
        "rule_ids": list(found),
        "count": len(selected),
        "text": text,
    })


def _search_spec(ctx: ToolContext, query: str, **_kwargs) -> ToolResult:
    if ctx.spec_answer is None:
        return ToolResult(False, "search_spec", {}, "规范检索当前不可用")
    try:
        result = ctx.spec_answer(query) or {}
    except ValueError as exc:
        # norm_qa_contract 在没有任何条款时抛 ValueError：如实说明未检索到
        return ToolResult(False, "search_spec", {}, f"未检索到匹配条款：{exc}")
    except Exception as exc:  # noqa: BLE001 - 工具层不向上抛异常
        return ToolResult(False, "search_spec", {}, f"规范检索失败：{exc}")
    sources = []
    for item in (result.get("sources") or [])[:5]:
        if isinstance(item, dict):
            sources.append({"chapter": str(item.get("chapter") or ""),
                            "section": str(item.get("section") or ""),
                            "full_path": str(item.get("full_path") or "")})
    return ToolResult(True, "search_spec", {
        "found": bool(result.get("found")),
        "conclusion": str(result.get("conclusion") or ""),
        "basis": str(result.get("basis") or ""),
        "suggestion": str(result.get("suggestion") or ""),
        "sources": sources,
    })


HANDLERS: dict[str, Callable[..., ToolResult]] = {
    "get_overview": _get_overview,
    "list_issues": _list_issues,
    "get_issue": _get_issue,
    "get_fix": _get_fix,
    "get_basis": _get_basis,
    "get_coverage": _get_coverage,
    "rule_lookup": _rule_lookup,
    "search_spec": _search_spec,
}


def execute(name: str, args: Any, ctx: ToolContext) -> ToolResult:
    """执行一个工具。永远返回 ToolResult，不抛异常。"""
    clean, error = validate_args(name, args)
    if error:
        return ToolResult(False, name, {}, error)
    handler = HANDLERS.get(name)
    if handler is None:
        return ToolResult(False, name, {}, f"未知工具：{name}")
    try:
        return handler(ctx, **clean)
    except Exception as exc:  # noqa: BLE001 - 工具层兜住所有异常
        return ToolResult(False, name, {}, f"工具执行失败：{exc}")


# ---------------------------------------------------------------- 输出辅助


def build_card_blocks(ctx: ToolContext, indexes) -> list[dict]:
    """把模型点名的条目序号渲染成卡片；序号越界时跳过，不编造条目。"""
    cards: list[dict] = []
    for raw in list(indexes or [])[:MAX_CARDS]:
        try:
            number = int(raw)
        except (TypeError, ValueError):
            continue
        if number <= 0:
            continue
        selected, _reason, _meta = _select_by_index(ctx, number)
        if not selected:
            continue
        cards.append(blocks.issue_card(selected[0], index=number))
    return cards


def snapshot_for_prompt(ctx: ToolContext) -> dict:
    """给模型的会话快照：只放计数与能力提示，不放任何论文原文。"""
    issues = [item for item in (ctx.paper_errors or []) if isinstance(item, dict)]
    counts = Counter(facts._category(issue) for issue in issues)
    return {
        "college_id": ctx.college_id,
        "has_paper": ctx.has_paper,
        "issue_total": int(ctx.paper_total or 0),
        "stored_issues": len(issues),
        "category_counts": dict(counts),
        "listed_count": len(ctx.last_listed or []),
        "unchecked_count": len(ctx.paper_unchecked or []),
    }


def dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
