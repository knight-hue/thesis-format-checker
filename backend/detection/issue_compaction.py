# -*- coding: utf-8 -*-
"""检测问题聚合与输出限流：**同一规则只出一条**，条目里带上各处实测值与定位。

聚合键就是 `rule_id`（一个问题类型一条）。原先还带 `actual`/`expected` 一起做键，
于是同一条规则会因为“每处的实测值不同”被拆成一堆行——软院那份样张的
`table.caption_number` 19 处就变成 18 行，学生看到的是同一句话重复 18 遍。
现在改为：一条规则一行 + `occurrence_count`（共几处）+ `variants`（最多若干个
“实测值/要求值/位置”三元组），信息不丢、行数收敛。

输出顺序按“错误在前，其次出现次数多的在前”，这样限流截断时先被截掉的是
出现次数少的提示类问题，而不是被一堆同类提示挤掉的问题。
"""
from __future__ import annotations

import copy
import json
from collections import OrderedDict

# 排序用的严重程度次序：错误 > 提示 > 信息。
_SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}

# 一条聚合问题最多携带多少个可修复位置（供 /api/fix 一次全改）。
# 超过这个数量仍降级为不可自动修复：一次请求改上千处既慢又难保证逐处成功。
MAX_FIX_LOCATIONS = 200


def _canonical(value) -> str:
    """把可能为 dict/list 的值转成稳定字符串，用于去重。"""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(value)


def _location_of(issue) -> dict:
    location = issue.get("location")
    return location if isinstance(location, dict) else {}


def _variant(issue) -> dict:
    # conclusion 一并带上：同一条规则下各处“缺什么”可能不同（如参考文献条目缺页码
    # 或缺出版地），只留实测值看不出差别。
    return {
        "actual": issue.get("actual"),
        "expected": issue.get("expected"),
        "conclusion": issue.get("conclusion"),
        "location": copy.deepcopy(_location_of(issue)),
    }


def _variant_key(variant) -> str:
    """按“实测值 + 要求值”去重：同值不同位置不算新变体（位置另在 location_samples）。"""
    return "%s|%s" % (_canonical(variant.get("actual")),
                      _canonical(variant.get("expected")))


def compact_check_result(result: dict, max_returned: int,
                         max_location_samples: int,
                         *, split_advisory: bool = True) -> dict:
    """返回聚合、截断并带分类统计的新结果，不改动原 issue 对象。

    split_advisory=False 用于内部对“仅供参考”条目做二次聚合（它们带 advisory 标记，
    若再拆分会无限递归）。
    """
    raw_issues = list(result.get("issues") or [])
    # “AI 判断，仅供参考”的条目与总数分开（不计入 total_issues）。
    # 旧会话里可能还把它们混在 issues 中（带 advisory 标记），这里兼容拆出来。
    raw_advisory = list(result.get("advisory_issues") or []) if split_advisory else []
    inline_advisory = ([issue for issue in raw_issues
                        if isinstance(issue, dict) and issue.get("advisory")]
                       if split_advisory else [])
    if inline_advisory:
        raw_advisory.extend(inline_advisory)
        raw_issues = [issue for issue in raw_issues
                      if not (isinstance(issue, dict) and issue.get("advisory"))]
    groups = OrderedDict()
    summary = OrderedDict()

    for raw_issue in raw_issues:
        issue = raw_issue if isinstance(raw_issue, dict) else {}
        category = str(issue.get("category") or "其他")
        category_stats = summary.setdefault(
            category,
            {"category": category, "occurrence_count": 0, "grouped_count": 0},
        )
        category_stats["occurrence_count"] += 1

        key = str(issue.get("rule_id") or "")
        grouped = groups.get(key)
        location = _location_of(issue)
        if grouped is None:
            grouped = copy.deepcopy(issue)
            grouped["occurrence_count"] = 1
            grouped["location_samples"] = [copy.deepcopy(location)] if location else []
            grouped["variants"] = [_variant(issue)]
            # 全部位置（不只是展示用的 location_samples）：前端把聚合项交给
            # /api/fix 时依此批量修复，不能只改第一处。
            grouped["locations"] = [copy.deepcopy(location)] if location else []
            # 去重用的内部索引，输出前去掉；只维护到降级阈值即可，
            # 否则病态重复（几万条同规则）会退化成 O(n²)。
            grouped["_location_keys"] = (
                {_canonical(location)} if location else set()
            )
            groups[key] = grouped
            category_stats["grouped_count"] += 1
            continue

        grouped["occurrence_count"] += 1
        if location and len(grouped["locations"]) <= MAX_FIX_LOCATIONS:
            location_key = _canonical(location)
            if location_key not in grouped["_location_keys"]:
                grouped["_location_keys"].add(location_key)
                grouped["locations"].append(copy.deepcopy(location))
        samples = grouped["location_samples"]
        seen_locations = {_canonical(item) for item in samples}
        if (location and len(samples) < max_location_samples
                and _canonical(location) not in seen_locations):
            samples.append(copy.deepcopy(location))
        variants = grouped["variants"]
        seen_variants = {_variant_key(item) for item in variants}
        candidate = _variant(issue)
        if (len(variants) < max_location_samples
                and _variant_key(candidate) not in seen_variants):
            variants.append(candidate)

    grouped_issues = sorted(
        groups.values(),
        key=lambda item: (_SEVERITY_RANK.get(item.get("severity"), 3),
                          -item["occurrence_count"]),
    )
    for issue in grouped_issues:
        issue.pop("_location_keys", None)
        if issue["occurrence_count"] <= 1:
            issue.pop("locations", None)
            continue
        # 同规则多处：可修复时把全部位置带上，前端一次提交即可全改。
        if issue.get("fixable"):
            locations = issue.pop("locations", [])
            if len(locations) <= MAX_FIX_LOCATIONS:
                issue["locations"] = locations
            else:
                issue["fixable"] = False
                issue["fix_reason"] = (
                    f"该问题合并了 {issue['occurrence_count']} 个位置，超过单次批量修复上限"
                    f"（{MAX_FIX_LOCATIONS} 处），请手工处理"
                )
        else:
            issue.pop("locations", None)

    returned = grouped_issues[:max_returned]
    compacted = dict(result)
    # 参考条目单独聚合一次：仍是 issue 结构（带 occurrence_count/location_samples），
    # 但不计入 total_issues，也不进 issue_summary。
    advisory_compacted = (compact_check_result(
        {"issues": raw_advisory}, max_returned, max_location_samples,
        split_advisory=False)["issues"]
        if raw_advisory else [])
    compacted.update({
        "total_issues": len(raw_issues),
        "grouped_issues": len(grouped_issues),
        "returned_issues": len(returned),
        "issues_truncated": len(grouped_issues) > len(returned),
        "issue_summary": list(summary.values()),
        "issues": returned,
        "advisory_issues": advisory_compacted,
    })
    # 响应形状由本函数兜底：未执行的条款清单（能力缺口）必须是数组，
    # 调用方没带时补空数组，前端不必区分“字段缺失”和“没有缺口”。
    compacted.setdefault("unchecked", [])
    # 三态契约：待人工队列单独返回，不计入 total_issues；本轮只落字段，恒为空数组。
    compacted.setdefault("pending_review", [])
    return compacted
