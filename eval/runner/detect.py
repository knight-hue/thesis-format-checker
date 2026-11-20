# -*- coding: utf-8 -*-
"""检测层评测：解析 → 检测 → 与金标准比对 → P/R/F1。

输出：
- overall / by_rule / by_severity 的混淆统计；
- 干净样张的误报计数；
- 每个样张的逐条明细（金标准命中/未命中、检测结果、定位解析状态）。
"""
import tempfile
import time

from backend.detection.checker_service import check_document
from backend.fix.fixability import annotate_issues

from .. import metrics
from ..golden import GoldenIssue
from . import parse_docx, resolve_case_locations


def run_detection_eval(cases, config) -> dict:
    """对全部样张执行检测评测。返回结构化结果。"""
    per_case = []
    all_golden = []
    all_detected = []
    resolution_stats = {"resolved": 0, "unresolved": 0}
    clean_issues_total = 0

    for case in cases:
        with tempfile.TemporaryDirectory(prefix="eval_detect_") as tmp:
            start = time.perf_counter()
            document = parse_docx(case.docx_path, case.college_id, tmp)
            parse_ms = (time.perf_counter() - start) * 1000

            start = time.perf_counter()
            result = check_document(
                document, case.college_id, rag_service=None,
                include_semantic=config.include_semantic,
            )
            detect_ms = (time.perf_counter() - start) * 1000
            annotate_issues(result["issues"], llm_available=False)

        detected = result["issues"]
        golden = [g.to_dict() for g in case.golden]
        resolved = resolve_case_locations(document, golden)

        for pos, ok in resolved.items():
            resolution_stats["resolved" if ok else "unresolved"] += 1

        if case.is_clean:
            clean_issues_total += len(detected)

        all_golden.extend(golden)
        all_detected.extend(detected)
        per_case.append({
            "name": case.name,
            "is_clean": case.is_clean,
            "parse_ms": round(parse_ms, 2),
            "detect_ms": round(detect_ms, 2),
            "golden": golden,
            "detected": [{
                "rule_id": i["rule_id"],
                "severity": i.get("severity"),
                "location": i.get("location"),
                "fixable": i.get("fixable"),
                "message": i.get("message"),
                "actual": str(i.get("actual"))[:80],
            } for i in detected],
            "resolved": resolved,
            "breakdown": result.get("breakdown"),
            "unchecked": result.get("unchecked", []),
        })

    overall = metrics.confusion_matrix(all_golden, all_detected)
    by_rule = metrics.group_confusion_by_rule(all_golden, all_detected)
    by_severity = _group_by_severity(all_golden, all_detected)

    return {
        "overall": {**overall, **metrics.prf(overall["tp"], overall["fp"], overall["fn"])},
        "by_rule": by_rule,
        "by_severity": by_severity,
        "resolution": resolution_stats,
        "clean_issues": clean_issues_total,
        "case_count": len(cases),
        "cases": per_case,
    }


def _group_by_severity(golden_list, detected_list):
    """按严重级别分组的混淆统计。金标准 severity 与检测 severity 都可能缺省。"""
    sevs = set()
    for g in golden_list:
        sevs.add(g.get("severity") or "unknown")
    for d in detected_list:
        sevs.add(d.get("severity") or "unknown")
    out = {}
    for sev in sorted(sevs):
        g = [g for g in golden_list if (g.get("severity") or "unknown") == sev]
        d = [x for x in detected_list if (x.get("severity") or "unknown") == sev]
        cm = metrics.confusion_matrix(g, d)
        out[sev] = {**cm, **metrics.prf(cm["tp"], cm["fp"], cm["fn"])}
    return out
