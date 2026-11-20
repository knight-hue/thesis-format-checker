# -*- coding: utf-8 -*-
"""指标计算：混淆矩阵、P/R/F1、逐规则聚合。

匹配口径分两类：
- 规则引擎类金标准（location 含 index/run/row/col）：维度为 None 表示不限定，
  检测结果 rule_id 相同且所有指定维度命中 → TP；
- 语义类金标准（text_anchor）：检测 issue 的可见文本包含锚文本 → TP。
检测结果未被任何金标准命中 → FP；金标准未被检测 → FN。
"""
from typing import Optional

from .golden import visible_text_of


def golden_matches_detected(golden: dict, detected: dict) -> bool:
    """金标准条目是否被某条检测结果命中。"""
    if golden.get("rule_id") != detected.get("rule_id"):
        return False
    # 语义锚匹配：检测 issue 的可见文本须包含锚文本
    anchor = golden.get("text_anchor")
    if anchor:
        return any(anchor in text for text in visible_text_of(detected))
    # 规则引擎定位匹配
    want_loc = golden.get("location") or {}
    got_loc = detected.get("location") or {}
    for dim in ("type", "index", "run", "row", "col"):
        want = want_loc.get(dim)
        if want is None:
            continue
        if got_loc.get(dim) != want:
            return False
    return True


def confusion_matrix(golden_list: list, detected_list: list) -> dict:
    """返回 {tp, fp, fn}。"""
    tp = 0
    matched_detected = set()
    for golden in golden_list:
        hit = None
        for pos, detected in enumerate(detected_list):
            if pos in matched_detected:
                continue
            if golden_matches_detected(golden, detected):
                hit = pos
                break
        if hit is not None:
            tp += 1
            matched_detected.add(hit)
    fp = len(detected_list) - len(matched_detected)
    fn = len(golden_list) - tp
    return {"tp": tp, "fp": fp, "fn": fn}


def prf(tp: int, fp: int, fn: int) -> dict:
    """精确率 / 召回率 / F1。分母为 0 时对应项为 None。"""
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and (precision + recall)
        else None
    )
    return {"precision": precision, "recall": recall, "f1": f1}


def aggregate_prf(rows: list) -> dict:
    """汇总多组 (tp, fp, fn) 算整体 P/R/F1（微平均）。"""
    tp = sum(r["tp"] for r in rows)
    fp = sum(r["fp"] for r in rows)
    fn = sum(r["fn"] for r in rows)
    return {**prf(tp, fp, fn), "tp": tp, "fp": fp, "fn": fn}


def group_confusion_by_rule(golden_list: list, detected_list: list) -> dict:
    """按 rule_id 分组统计混淆（用于逐规则明细）。"""
    by_rule: dict = {}
    matched_detected = set()
    for golden in golden_list:
        rid = golden.get("rule_id")
        bucket = by_rule.setdefault(rid, {"tp": 0, "fp": 0, "fn": 0})
        hit = None
        for pos, detected in enumerate(detected_list):
            if pos in matched_detected:
                continue
            if golden_matches_detected(golden, detected):
                hit = pos
                break
        if hit is not None:
            bucket["tp"] += 1
            matched_detected.add(hit)
        else:
            bucket["fn"] += 1
    for pos, detected in enumerate(detected_list):
        if pos in matched_detected:
            continue
        rid = detected.get("rule_id")
        bucket = by_rule.setdefault(rid, {"tp": 0, "fp": 0, "fn": 0})
        bucket["fp"] += 1
    for rid, bucket in by_rule.items():
        bucket.update(prf(bucket["tp"], bucket["fp"], bucket["fn"]))
    return by_rule


def accuracy(correct: int, total: int) -> Optional[float]:
    return correct / total if total else None
