# -*- coding: utf-8 -*-
"""结构化回答块：论文类答案的卡片与后续问题。

纯数据拼装，不调用模型。前端优先渲染 blocks；没有 blocks 时回退到
原有 conclusion/sections，保证旧响应仍可用。
"""
from collections import Counter

from . import facts


def issue_card(issue, index=None):
    """单条问题卡片。字段只取 issue 原文，不补充推断。"""
    return {
        "index": index,
        "rule_id": str(issue.get("rule_id") or ""),
        "category": str(issue.get("category") or ""),
        "severity": str(issue.get("severity") or ""),
        "location": facts._location(issue),
        "message": facts._message(issue),
        "expected": facts._expected(issue),
        "suggestion": facts._suggestion(issue),
        "actual": issue.get("actual"),
        "basis": facts._basis(issue),
    }


def _summary_text(issues, total):
    total = int(total or 0)
    if total <= 0:
        return "当前记录的问题为 0 条。"
    counts = Counter(facts._category(item) for item in issues or [])
    lines = [f"本次共检测出 {total} 条问题。"]
    if counts:
        parts = [f"{category} {count} 条"
                 for category, count in sorted(counts.items(),
                                               key=lambda pair: (-pair[1], pair[0]))]
        lines.append("、".join(parts))
    if len(issues or []) < total:
        lines.append(f"本次统计基于已保存的 {len(issues or [])} 条。")
    return "\n".join(lines)


def overview_blocks(issues, total):
    """概览：统计摘要 + 最前面的问题卡片。"""
    ordered = facts._sort_issues([item for item in (issues or []) if isinstance(item, dict)])
    blocks = [{
        "type": "summary",
        "title": "检测概览",
        "text": _summary_text(ordered, total),
        "issues": [],
        "sources": [],
    }]
    top = ordered[:facts.OVERVIEW_LIMIT]
    if top:
        blocks.append({
            "type": "issues",
            "title": "主要问题",
            "text": "",
            "issues": [issue_card(item, index) for index, item in enumerate(top, 1)],
            "sources": [],
        })
    return blocks


def detail_blocks(issues, title="问题明细", start_index=1):
    """逐条问题卡片。start_index 是继续往后看时的绝对序号（第 11 条起）。"""
    items = [item for item in (issues or []) if isinstance(item, dict)]
    if not items:
        return []
    start = max(1, int(start_index or 1))
    return [{
        "type": "issues",
        "title": title,
        "text": "",
        "issues": [issue_card(item, start + offset)
                   for offset, item in enumerate(items)],
        "sources": [],
    }]


def text_blocks(text, title=""):
    if not text:
        return []
    return [{
        "type": "text",
        "title": title,
        "text": text,
        "issues": [],
        "sources": [],
    }]


def suggestions_for(intent, scope=None, stats=None):
    """只给当前已支持的问法，不提供做不到的按钮。

    scope 是上一轮的实际筛选范围，stats 是事实层的统计（有无更多/总条数）；
    按钮跟着实际数据走：没有问题就不给「第 1 条怎么改」，还有更多才给「继续看」。
    """
    scope = scope if isinstance(scope, dict) else {}
    stats = stats if isinstance(stats, dict) else {}
    has_more = bool(stats.get("has_more"))
    has_items = int(stats.get("matched_total") or 0) > 0
    back = {"label": "回到概览", "query": "这篇论文哪里不对？"}
    basis = {"label": "依据哪句模板？", "query": "依据哪句模板？"}
    coverage = {"label": "还有哪些没查？", "query": "还有哪些没查？"}
    only_errors = {"label": "只看错误", "query": "只看错误"}
    fix_all = {"label": "这些怎么改？", "query": "这些怎么改？"}
    fix_this = {"label": "这条怎么改？", "query": "这条怎么改？"}
    more = {"label": "继续看", "query": "继续看"}
    single = scope.get("scope") == "index"

    if intent == "paper_overview":
        items = [only_errors] if has_items else []
        if has_items:
            items.append({"label": "第 1 条怎么改？", "query": "第 1 条怎么改？"})
        items.extend([coverage, back])
        return items
    if intent == "paper_detail":
        items = [more] if has_more else []
        items.extend([fix_this if single else fix_all, basis, coverage])
        return items
    if intent == "paper_fix":
        items = [only_errors, basis, back]
        return items
    if intent == "paper_basis":
        return [fix_this if single else fix_all, coverage, back]
    if intent == "paper_coverage":
        return [back, only_errors, basis]
    if intent == "spec_qa":
        return [
            {"label": "一级标题用什么字号？", "query": "一级标题应该用什么字号？"},
            {"label": "正文行距要求多少？", "query": "正文行距要求是多少？"},
        ]
    if intent == "out_of_scope":
        return [back]
    if intent == "paper_missing":
        return [
            {"label": "一级标题用什么字号？", "query": "一级标题应该用什么字号？"},
        ]
    return []
