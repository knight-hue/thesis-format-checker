# -*- coding: utf-8 -*-
"""写回明细 ↔ 修复项配对：算出提交的修复项里哪些真的被改成功了。

设计意图：
    fix_document 返回的 applied 只说明"某条规则在第 N 块上改成功了"，
    并不携带调用方提交的 issue 身份。而下载接口需要知道"这次修复解决了
    哪几条 issue"，才能把结果回传前端（X-Fixed-Indexes）并同步会话里的
    检测结果列表，因此按 (rule_id, index[, row, col]) 键把两边一一配对。
判定口径：
    只看 applied（有真实应用记录才算修复）：
    - skipped 会被跨层重复计数（文字类规则格式层记一次跳过、文字层再记
      一次应用），拿它对账必然错报，故不可信；
    - 漏判的代价只是把已修复项留在列表里，比误删安全。
"""
import logging

logger = logging.getLogger(__name__)

# 节级规则（page.*）的 issue 未给出分节下标时的通配哨兵：宁可放过也不误配
_ANY_INDEX = object()


def _as_int(value):
    """尽力转成 int，转不了返回 None（issue 里的 index/row/col 可能缺失或非法）。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _single_issue_keys(issue) -> list:
    """单条 issue（一个 location）可能对应的写回明细键，按优先级排列。

    键 = (rule_id, index[, row, col])：
    - 节级规则（page.*）按分节下标配对；issue 没给出下标时才退化为通配哨兵；
    - 语义类 issue 一次可能改多个段落（blocks），每个段落都算候选；
    - 表格单元格多带 (row, col)，避免同一张表里同类问题互相错配。
    """
    if not isinstance(issue, dict):
        return []
    rule_id = str(issue.get("rule_id") or "")
    loc = issue.get("location")
    if not isinstance(loc, dict):
        loc = {}
    if loc.get("type") == "section":
        index = _as_int(loc.get("index"))
        return [(rule_id, _ANY_INDEX if index is None else index)]
    if issue.get("source") == "rag":
        return [
            (rule_id, _as_int(block.get("index")) if isinstance(block, dict) else None)
            for block in issue.get("blocks") or []
        ]
    if loc.get("type") == "table_cell":
        return [(rule_id, _as_int(loc.get("index")), _as_int(loc.get("row")), _as_int(loc.get("col")))]
    return [(rule_id, _as_int(loc.get("index")))]


def _issue_key_groups(issue) -> list:
    """issue 需要全部命中的键组；每组是一组候选键。

    聚合问题带 locations（同规则多处）：必须每一处都有写回明细才算修好；
    只改到一部分时不能从列表里删掉，否则用户以为全部好了。
    """
    locations = issue.get("locations") if isinstance(issue, dict) else None
    if locations and issue.get("source") != "rag":
        return [
            _single_issue_keys({**issue, "location": location})
            for location in locations
        ]
    return [_single_issue_keys(issue)]


def _applied_record_key(item) -> tuple:
    """写回明细的键，规则同 _issue_applied_keys。"""
    rule_id = str(item.get("rule_id") or "")
    index = _as_int(item.get("index"))
    if "row" in item or "col" in item:
        return (rule_id, index, _as_int(item.get("row")), _as_int(item.get("col")))
    return (rule_id, index)


def _key_matches(candidate, record) -> bool:
    if len(candidate) != len(record) or candidate[0] != record[0]:
        return False
    return all(
        want is _ANY_INDEX or want == have
        for want, have in zip(candidate[1:], record[1:])
    )


def fixed_issue_indexes(issues, result) -> list:
    """把写回明细与提交的修复项一一配对，返回真正被修复项在 issues 中的下标。

    每条明细只能被消费一次，因此 (rule_id, index) 完全相同的多条 issue 只会放行一条；
    配不上的明细只记日志，不删任何条目。
    """
    records = []
    for layer in ("format", "text", "structural"):
        layer_result = result.get(layer) or {}
        for item in layer_result.get("applied") or []:
            if isinstance(item, dict) and item.get("rule_id"):
                records.append({"key": _applied_record_key(item), "used": False})

    fixed = []
    for position, issue in enumerate(issues):
        groups = _issue_key_groups(issue)
        matched = []
        complete = True
        for candidates in groups:
            match = next(
                (
                    record for record in records
                    if not record["used"]
                    and any(_key_matches(candidate, record["key"])
                            for candidate in candidates)
                ),
                None,
            )
            if match is None:
                complete = False
                break
            match["used"] = True
            matched.append(match)
        if complete:
            fixed.append(position)
        else:
            # 部分位置已改：不删除该条，把已占用的明细退回给其他 issue 使用。
            for record in matched:
                record["used"] = False

    leftover = sum(1 for record in records if not record["used"])
    if leftover:
        logger.warning("写回明细有 %d 条未能对应到提交的修复项，对应条目已保守保留", leftover)
    return fixed
