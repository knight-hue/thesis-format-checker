# -*- coding: utf-8 -*-
"""修复层评测：对注入样张执行"检测 → 提交可修复项 → 写回 → 重检"闭环。

指标：
- 提交率：金标准中可自动修复（fixable）的比例；
- 应用率：/api/fix 实际应用的提交项比例（X-Applied-Count）；
- 重检消失率：修复后重新检测，原金标准问题消失的比例（修复真正生效）；
- 回归破坏率：修复后新出现、修复前不存在且不在金标准中的问题数 / 修复前问题数。
"""
import io
import json
import tempfile
from zipfile import ZipFile

from backend.detection.checker_service import check_document
from backend.fix.fixability import annotate_issues
from fastapi.testclient import TestClient

import backend.api as api
from .. import metrics
from ..golden import GoldenIssue
from . import parse_docx, resolve_case_locations

DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _detect(docx_bytes, college_id, include_semantic, tmp):
    with open(tmp / "in.docx", "wb") as f:
        f.write(docx_bytes)
    document = parse_docx(str(tmp / "in.docx"), college_id, str(tmp))
    result = check_document(
        document, college_id, rag_service=None, include_semantic=include_semantic
    )
    annotate_issues(result["issues"], llm_available=False)
    return document, result


def run_fix_eval(cases, config) -> dict:
    """对全部注入样张执行修复闭环评测。"""
    client = TestClient(api.app)
    try:
        return _run(client, cases, config)
    finally:
        client.close()


def _run(client, cases, config):
    injected = [c for c in cases if not c.is_clean]
    per_case = []
    totals = {"submitted": 0, "applied": 0, "skipped_fix": 0,
              "recheck_cleared": 0, "recheck_targets": 0,
              "regressions": 0, "before_issues": 0, "fix_ok": 0}

    for case in injected:
        with tempfile.TemporaryDirectory(prefix="eval_fix_") as tmp:
            tmp = __import__("pathlib").Path(tmp)
            payload = open(case.docx_path, "rb").read()
            document, result = _detect(payload, case.college_id,
                                       config.include_semantic, tmp)
            golden = [g.to_dict() for g in case.golden]
            resolved = resolve_case_locations(document, golden)

            # 选出与金标准匹配、且可修复的检测项
            targets = []
            for detected in result["issues"]:
                for g in golden:
                    if metrics.golden_matches_detected(g, detected):
                        targets.append(detected)
                        break
            targets = [t for t in targets if t.get("fixable")]
            totals["submitted"] += len(targets)

            before_keys = _issue_keys(result["issues"])

            case_out = {
                "name": case.name,
                "submitted": len(targets),
                "applied": 0,
                "recheck_cleared": 0,
                "regressions": 0,
                "skipped": 0,
                "openable": None,
                "parts_preserved": None,
            }
            if targets:
                resp = client.post(
                    "/api/fix",
                    data={"college_id": case.college_id,
                          "issues_json": json.dumps(targets)},
                    files={"file": ("paper.docx", payload, DOCX_CONTENT_TYPE)},
                )
                if resp.status_code == 200:
                    applied = int(resp.headers.get("X-Applied-Count", "0") or 0)
                    skipped = int(resp.headers.get("X-Skipped-Count", "0") or 0)
                    case_out["applied"] = applied
                    case_out["skipped"] = skipped
                    totals["applied"] += applied
                    totals["skipped_fix"] += skipped
                    totals["fix_ok"] += 1

                    # 写回产物验证：可打开 + 部件保真
                    openable, parts_preserved = _verify_package(payload, resp.content)
                    case_out["openable"] = openable
                    case_out["parts_preserved"] = parts_preserved

                    # 重检：金标准问题是否消失
                    fixed_path = tmp / "fixed.docx"
                    fixed_path.write_bytes(resp.content)
                    _, re_result = _detect(resp.content, case.college_id,
                                           config.include_semantic, tmp)
                    after_keys = _issue_keys(re_result["issues"])
                    # 重检消失：金标准匹配的 issue 在修复后不再出现
                    targets_now = 0
                    for detected in re_result["issues"]:
                        for g in golden:
                            if metrics.golden_matches_detected(g, detected):
                                targets_now += 1
                                break
                    cleared = len(targets) - targets_now
                    case_out["recheck_cleared"] = cleared
                    totals["recheck_targets"] += len(targets)
                    totals["recheck_cleared"] += cleared

                    # 回归：修复后新增的、不在金标准里的问题
                    regressions = after_keys - before_keys
                    regressions = {k for k in regressions
                                   if not any(metrics.golden_matches_detected(g, {
                                       "rule_id": k[0], "location": {
                                           "type": k[1], "index": k[2], "run": k[3],
                                           "row": k[4], "col": k[5]}})
                                       for g in golden)}
                    case_out["regressions"] = len(regressions)
                    totals["regressions"] += len(regressions)
                else:
                    case_out["fix_error"] = f"HTTP {resp.status_code}: {resp.text[:200]}"
            totals["before_issues"] += len(before_keys)
            per_case.append(case_out)

    return {
        "totals": totals,
        "fix_success_rate": metrics.accuracy(totals["applied"], totals["submitted"]),
        "recheck_clear_rate": metrics.accuracy(
            totals["recheck_cleared"], totals["recheck_targets"]),
        "regression_rate": metrics.accuracy(
            totals["regressions"], totals["before_issues"]),
        "cases": per_case,
    }


def _issue_keys(issues):
    """把 issue 折叠成去重键集合（用于回归对比）。"""
    keys = set()
    for issue in issues:
        loc = issue.get("location") or {}
        keys.add((issue.get("rule_id"), loc.get("type"), loc.get("index"),
                  loc.get("run"), loc.get("row"), loc.get("col")))
    return keys


def _verify_package(original_bytes, fixed_bytes):
    """写回保真：可打开性 + 未修改部件字节一致。"""
    try:
        from docx import Document
        Document(io.BytesIO(fixed_bytes))
    except Exception:
        return False, None
    try:
        with ZipFile(io.BytesIO(original_bytes)) as a, ZipFile(io.BytesIO(fixed_bytes)) as b:
            if set(a.namelist()) != set(b.namelist()):
                return True, False
            changed = {n for n in a.namelist()
                       if n != "word/document.xml" and a.read(n) != b.read(n)}
            return True, len(changed) == 0
    except Exception:
        return True, None
