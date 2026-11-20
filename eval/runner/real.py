# -*- coding: utf-8 -*-
"""真实样张评测：对自由导入的 Word 文档逐个执行 解析 → 检测 → 修复 → 写回。

与合成样张评测的区别：真实文档没有金标准，因此不计算 精确率/召回率/F1，
只统计检出问题分布、修复闭环结果与耗时。

支持 .docx（python-docx 只能解析新版格式）；旧版 .doc 无法解析，
会跳过并在报告中注明原因，不中断整体评测。
"""
import json
import tempfile
import time
from pathlib import Path

from backend.detection.checker_service import check_document
from backend.fix.fixability import annotate_issues
from fastapi.testclient import TestClient

import backend.api as api
from .. import metrics
from . import parse_docx
from .fix import _detect, _issue_keys, _verify_package

DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def run_real_eval(config, client=None) -> dict:
    """对 samples_dir 下全部 .docx 文档执行评测。返回结构化结果。

    单个文档出错只记录到该文档的 error 字段，不中断其余文档。
    """
    samples = Path(config.samples_dir)
    if not samples.is_dir():
        return {"skipped": True, "reason": f"待测目录不存在：{samples_dir_str(config)}"}

    docx_files = sorted(p for p in samples.iterdir()
                        if p.suffix.lower() == ".docx" and not p.name.startswith("."))
    doc_files = sorted(p for p in samples.iterdir()
                       if p.suffix.lower() == ".doc" and not p.name.startswith("."))
    if not docx_files:
        msg = f"待测目录 {samples_dir_str(config)} 下没有 .docx 文档"
        if doc_files:
            msg += f"；有 {len(doc_files)} 个 .doc 旧格式文档（暂不支持解析，请用 Word 另存为 .docx）"
        return {"skipped": True, "reason": msg}

    own_client = client is None
    client = client or TestClient(api.app)
    try:
        totals = {
            "submitted": 0, "applied": 0, "skipped_fix": 0,
            "regressions": 0, "before_issues": 0,
            "fix_attempted_docs": 0, "openable_ok": 0, "parts_ok": 0,
        }
        per_doc = []
        for path in docx_files:
            per_doc.append(_process_one(path, config, client, totals))

        # 按规则/严重级别聚合检出问题分布（无金标准，只统计）
        by_rule: dict = {}
        by_severity: dict = {}
        for doc in per_doc:
            for issue in doc.get("issues", []):
                rid = issue.get("rule_id") or "(未知)"
                bucket = by_rule.setdefault(rid, {"count": 0, "severities": {}})
                bucket["count"] += 1
                sev = issue.get("severity") or "unknown"
                bucket["severities"][sev] = bucket["severities"].get(sev, 0) + 1
                by_severity[sev] = by_severity.get(sev, 0) + 1

        total_issues = sum(doc.get("issue_count", 0) for doc in per_doc)
        docs_with_error = sum(1 for doc in per_doc if doc.get("error"))
        return {
            "skipped": False,
            "samples_dir": samples_dir_str(config),
            "documents": per_doc,
            "totals": {
                **totals,
                "documents_processed": len(per_doc),
                "documents_with_error": docs_with_error,
                "total_issues": total_issues,
                "avg_issues_per_doc": round(
                    total_issues / len(per_doc), 2) if per_doc else None,
            },
            "fix_success_rate": metrics.accuracy(
                totals["applied"], totals["submitted"]),
            "by_rule": by_rule,
            "by_severity": by_severity,
        }
    finally:
        if own_client:
            client.close()


def _process_one(path: Path, config, client, totals: dict) -> dict:
    payload = path.read_bytes()
    out = {
        "name": path.name,
        "size_bytes": len(payload),
        "parse_ms": None,
        "detect_ms": None,
        "fix_ms": None,
        "blocks": None,
        "kind_counts": None,
        "issue_count": 0,
        "issues": [],
        "fix": None,
        "error": None,
    }
    try:
        with tempfile.TemporaryDirectory(prefix="eval_real_") as tmpd:
            tmp = Path(tmpd)
            t0 = time.perf_counter()
            document = parse_docx(str(path), config.college_id, str(tmp))
            out["parse_ms"] = round((time.perf_counter() - t0) * 1000, 2)

            t0 = time.perf_counter()
            result = check_document(
                document, config.college_id, rag_service=None,
                include_semantic=config.include_semantic,
            )
            annotate_issues(result["issues"], llm_available=False)
            out["detect_ms"] = round((time.perf_counter() - t0) * 1000, 2)

            issues = result["issues"]
            out["issue_count"] = len(issues)
            out["issues"] = [{
                "rule_id": i.get("rule_id"),
                "severity": i.get("severity"),
                "fixable": bool(i.get("fixable")),
                "message": str(i.get("message"))[:120],
                "actual": str(i.get("actual"))[:80],
            } for i in issues]

            blocks = document.get("blocks", [])
            out["blocks"] = len(blocks)
            kind_counts: dict = {}
            for b in blocks:
                k = b.get("kind") or b.get("type") or "(未知)"
                kind_counts[k] = kind_counts.get(k, 0) + 1
            out["kind_counts"] = kind_counts

            before_keys = _issue_keys(issues)
            totals["before_issues"] += len(before_keys)

            # 修复闭环：对全部可自动修复的问题提交修复接口
            fixable = [i for i in issues if i.get("fixable")]
            fix_out = {
                "submitted": 0, "applied": 0, "skipped": 0,
                "openable": None, "parts_preserved": None,
                "regressions": 0, "remaining_after_fix": len(before_keys),
                "fix_error": None,
            }
            if fixable:
                totals["submitted"] += len(fixable)
                fix_out["submitted"] = len(fixable)
                t0 = time.perf_counter()
                resp = client.post(
                    "/api/fix",
                    data={"college_id": config.college_id,
                          "issues_json": json.dumps(fixable)},
                    files={"file": ("paper.docx", payload, DOCX_CONTENT_TYPE)},
                )
                out["fix_ms"] = round((time.perf_counter() - t0) * 1000, 2)
                if resp.status_code == 200:
                    totals["fix_attempted_docs"] += 1
                    applied = int(resp.headers.get("X-Applied-Count", "0") or 0)
                    skipped = int(resp.headers.get("X-Skipped-Count", "0") or 0)
                    fix_out["applied"] = applied
                    fix_out["skipped"] = skipped
                    totals["applied"] += applied
                    totals["skipped_fix"] += skipped

                    # 把修复后的文档保存到样本同目录，便于人工打开核对
                    fixed_path = Path(config.samples_dir) / f"{path.stem}_已修复.docx"
                    try:
                        fixed_path.write_bytes(resp.content)
                        fix_out["fixed_saved_to"] = str(fixed_path)
                    except Exception as save_exc:
                        fix_out["fixed_saved_to"] = None
                        fix_out["save_error"] = f"{type(save_exc).__name__}: {save_exc}"

                    openable, parts = _verify_package(payload, resp.content)
                    fix_out["openable"] = openable
                    fix_out["parts_preserved"] = parts
                    if openable:
                        totals["openable_ok"] += 1
                    if parts is True:
                        totals["parts_ok"] += 1

                    # 重检：修复后剩余问题 / 回归
                    _, re_result = _detect(
                        resp.content, config.college_id,
                        config.include_semantic, tmp)
                    after_keys = _issue_keys(re_result["issues"])
                    fix_out["remaining_after_fix"] = len(after_keys)
                    regressions = len(after_keys - before_keys)
                    fix_out["regressions"] = regressions
                    totals["regressions"] += regressions
                else:
                    fix_out["fix_error"] = f"HTTP {resp.status_code}: {resp.text[:200]}"
            out["fix"] = fix_out
    except Exception as exc:  # 单个文档出错不中断整体
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def samples_dir_str(config) -> str:
    return str(Path(config.samples_dir))
