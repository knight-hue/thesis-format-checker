# -*- coding: utf-8 -*-
"""对话工具节点：只从检测结果字段取事实，不调用模型。"""
from . import facts


def rule_lookup(issues, slots, query=""):
    """查条目对应的规则号。返回 (text, rule_ids, reason)；找不到时 text 为空。"""
    selected, reason, _meta = facts.select_issues(issues, slots, query)
    if not selected:
        return "", [], reason
    rule_ids = []
    for issue in selected:
        rule_id = str(issue.get("rule_id") or "").strip()
        if rule_id and rule_id not in rule_ids:
            rule_ids.append(rule_id)
    if not rule_ids:
        return "", [], "这些条目没有记录规则号"
    text = "这些条目对应的规则号：" + "、".join(rule_ids) + "。\n" \
           "可在「检测结果」页按规则号逐条核对模板条款。"
    return text, rule_ids, ""
