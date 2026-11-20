# -*- coding: utf-8 -*-
"""覆盖率对账：规范文本的每一句，归宿必须三选一。

金标准的机器化检查（新学院上架的门禁之一）：``<id>.txt`` 里的每个条目必须能
归到 **有判据 / 明确不查（带理由）/ 未检** 之一；任何一条没有归宿 = 静默漏句，
不允许激活。同时做反方向完整性检查：``basis.json`` 引用的原句必须在规范文本
里找得到（找不到 = 依据漂移 / 错引）。

匹配口径（实证 basis 形态）：
- basis 的 value 是一句或多句原句用"；"拼接（``page.`` 把六条页边距拼成一串），
  先按"；"拆段再逐段对位；
- 归一化：去全部空白与尾部句点（``corpus.normalize``），两边同样处理；
- 命中 = 分段与条目归一化后完全相等，或（分段长度 ≥ 4 时）分段是条目的子串——
  后者覆盖"条目里混着补充说明"的行（实证：致谢标题条目里带引号说明）。

``decisions``（"明确不查"台账）：默认自动读 ``config/rules/<学校>/<学院>/
<id>_decisions.json``（JSON 对象，键为条目文本或其子串，值为理由；显式传
``{}`` 可跳过自动读取）；没有归宿的孤儿条目一律计入"未归宿"，门禁不通过。
"""
from __future__ import annotations

import json
import os
from typing import Optional

from backend.college_registry import RULES_ROOT, dir_of, path_of
from backend.onboarding.corpus import normalize, parse_corpus, split_fragments

# 允许"分段是条目子串"的最小分段长度：太短的分段（如"参考文献"）会到处乱命中。
MIN_FRAGMENT_FOR_CONTAINMENT = 4


def load_basis(college_id: str, rules_dir: str = RULES_ROOT) -> dict:
    with open(path_of(college_id, "basis", rules_dir), encoding="utf-8") as handle:
        return json.load(handle)


def decisions_path_for(college_id: str) -> str:
    """学院的"明确不查"台账路径：``config/rules/<school>/<college>/<id>_decisions.json``。"""
    return os.path.join(dir_of(college_id), f"{college_id}_decisions.json")


def load_decisions(college_id: str) -> tuple:
    """读台账：返回 (台账, 路径)；文件不存在返回 ({}, "")；内容坏了直接报错——门禁要响。"""
    path = decisions_path_for(college_id)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}, ""
    except json.JSONDecodeError as exc:
        raise ValueError(f"决策台账不是合法 JSON：{path}（{exc}）") from exc
    if not isinstance(data, dict):
        raise ValueError(f"决策台账必须是 JSON 对象：{path}")
    return data, path


def run_coverage(college_id: str, decisions: Optional[dict] = None,
                 rules_dir: str = RULES_ROOT) -> dict:
    """跑一遍覆盖率对账，返回报告 dict（不打印、不写盘）。

    ``decisions`` 为 None 时自动读学院决策台账；显式传 dict（含空 {}）则以其为准。
    """
    decisions_source = ""
    if decisions is None:
        decisions, decisions_source = load_decisions(college_id)
    corpus_path = path_of(college_id, "corpus", rules_dir)
    with open(corpus_path, encoding="utf-8") as handle:
        document = parse_corpus(handle.read())
    basis = load_basis(college_id, rules_dir)
    norms = [normalize(item.text) for item in document.items]

    claimed: dict = {}
    unresolved = []
    for rule_id, value in basis.items():
        missing = []
        for fragment in split_fragments(value):
            hits = [i for i, norm in enumerate(norms)
                    if norm == fragment
                    or (len(fragment) >= MIN_FRAGMENT_FOR_CONTAINMENT
                        and fragment in norm)]
            if hits:
                for i in hits:
                    claimed.setdefault(i, []).append(rule_id)
            else:
                missing.append(fragment)
        if missing:
            unresolved.append({"rule_id": rule_id, "value": value,
                               "missing": missing})

    decision_map = {normalize(key): str(reason)
                    for key, reason in (decisions or {}).items() if normalize(key)}

    def reason_for(norm: str) -> Optional[str]:
        for key, reason in decision_map.items():
            if key == norm or key in norm:
                return reason
        return None

    covered, excluded, orphan = [], [], []
    for i, item in enumerate(document.items):
        entry = {"index": item.index, "line_no": item.line_no,
                 "chapter": item.chapter, "section": item.section,
                 "text": item.text}
        rules = sorted(set(claimed.get(i, [])))
        if rules:
            covered.append({**entry, "rules": rules})
            continue
        reason = reason_for(norms[i])
        if reason:
            excluded.append({**entry, "decision": reason})
        else:
            orphan.append(entry)

    return {
        "college_id": college_id,
        "corpus_path": corpus_path,
        "decisions_source": decisions_source,
        "items_total": len(document.items),
        "covered": covered,
        "excluded": excluded,
        "orphan": orphan,
        "basis_total": len(basis),
        "unresolved_basis": unresolved,
        "gate_passed": not orphan and not unresolved,
    }


def render_markdown(report: dict) -> str:
    lines = [f"# 覆盖率对账：{report['college_id']}", ""]
    lines.append(f"- 规范文本：`{report['corpus_path']}`")
    lines.append(f"- 条目总数：{report['items_total']}"
                 f"（有判据 {len(report['covered'])}；"
                 f"明确不查 {len(report['excluded'])}；"
                 f"未归宿 {len(report['orphan'])}）")
    lines.append(f"- 依据条目：{report['basis_total']}"
                 f"（原句未命中 {len(report['unresolved_basis'])}）")
    if report.get("decisions_source"):
        lines.append(f"- 决策台账：`{report['decisions_source']}`")
    lines.append(f"- 门禁：{'通过' if report['gate_passed'] else '未通过'}")
    if report["orphan"]:
        lines.append("")
        lines.append("## 未归宿条目（每句必须有归宿才能激活）")
        for item in report["orphan"]:
            location = " / ".join(p for p in (item["chapter"], item["section"]) if p)
            lines.append(f"- 第{item['line_no']}行｜{location or '（无章/节）'}"
                         f"｜{item['text'][:64]}")
    if report["unresolved_basis"]:
        lines.append("")
        lines.append("## 依据原句未在规范文本中命中（依据漂移 / 错引）")
        for row in report["unresolved_basis"]:
            lines.append(f"- `{row['rule_id']}`：缺 {'；'.join(row['missing'])[:90]}")
    return "\n".join(lines) + "\n"
