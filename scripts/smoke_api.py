"""Smoke-test the running backend over real HTTP (not starlette TestClient).

Covers the success and error paths that the contract in ``docs/openapi.json``
promises: sync check, async job lifecycle, fix/rebuild binaries, degraded
semantic fallback and the 400/404/409/413/422 responses.

Usage:
    python -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
    python scripts/smoke_api.py [base_url]

Exit code is non-zero when any expectation fails.
"""

from __future__ import annotations

import io
import json
import sys
import time
import uuid

import httpx
from docx import Document

DEFAULT_BASE = "http://127.0.0.1:8000"
COLLEGE_ID = "jlu_cs"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ((" | " + detail) if detail else ""))


def build_sample_docx() -> bytes:
    """A small thesis-shaped document that triggers several rule categories."""

    doc = Document()
    doc.add_paragraph("吉林大学本科毕业论文（设计）")
    doc.add_paragraph("基于深度学习的论文格式自动校验系统")
    doc.add_paragraph("计算机科学与技术学院")
    doc.add_paragraph("摘要")
    doc.add_paragraph("本文研究论文格式校验。使用了 BERT 模型，BERT 模型效果良好。")
    doc.add_paragraph("关键词：格式校验；深度学习")
    doc.add_paragraph("1 绪论")
    doc.add_paragraph("本文主要工作如下，首先分析现状，其次设计系统，最后完成实验。")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "表1 实验环境"
    table.cell(0, 1).text = "配置"
    table.cell(1, 0).text = "CPU"
    table.cell(1, 1).text = "Intel i7"
    doc.add_paragraph("参考文献")
    doc.add_paragraph("[1] 张三. 论文格式研究[J]. 计算机学报, 2023, 46(1): 1-10.")
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def main(base: str) -> int:
    docx_bytes = build_sample_docx()
    files = {"file": ("sample.docx", docx_bytes, DOCX_MIME)}

    with httpx.Client(base_url=base, timeout=180) as client:
        health = client.get("/api/health")
        body = health.json() if health.status_code == 200 else {}
        record("GET /api/health 200", health.status_code == 200 and "status" in body, str(health.status_code))
        rag_ready = bool(body.get("rag_initialized"))
        llm_ready = body.get("llm_status") == "ready"

        colleges = client.get("/api/colleges")
        ids = [c.get("college_id") or c.get("id") for c in colleges.json()] if colleges.status_code == 200 else []
        record("GET /api/colleges 200 且包含测试学院",
               colleges.status_code == 200 and COLLEGE_ID in ids,
               f"{colleges.status_code} n={len(ids)}")

        check = client.post("/api/check-file", files=files,
                            data={"college_id": COLLEGE_ID, "include_semantic": "false"})
        payload = check.json() if check.status_code == 200 else {}
        required = ["issues", "total_issues", "passed", "breakdown", "pending_review", "unchecked"]
        missing = [key for key in required if key not in payload]
        record("POST /api/check-file 200 且响应结构完整",
               check.status_code == 200 and not missing,
               f"{check.status_code} total_issues={payload.get('total_issues')} missing={missing}")

        issues = payload.get("issues") or []
        decisions = {issue.get("decision") for issue in issues}
        record("issues[].decision 取值属于三态",
               decisions <= {"confirmed", "suggested", "pending_review"},
               f"n={len(issues)} decisions={sorted(decisions)}")
        semantic = (payload.get("breakdown") or {}).get("semantic") or {}
        record("breakdown.semantic 携带 confirmed_count / pending_review_count",
               all(key in semantic for key in ("confirmed_count", "pending_review_count")),
               json.dumps(semantic, ensure_ascii=False)[:160])

        fixable = [issue for issue in issues if issue.get("fixable")]

        record("POST /api/check-file 缺少 college_id → 422",
               client.post("/api/check-file", files=files).status_code == 422)

        record("POST /api/check-file 非 DOCX → 400",
               client.post("/api/check-file",
                           files={"file": ("a.txt", b"hello", "text/plain")},
                           data={"college_id": COLLEGE_ID}).status_code == 400)

        record("POST /api/check-file 未知学院 → 400",
               client.post("/api/check-file", files=files,
                           data={"college_id": "no_such_college"}).status_code == 400)

        oversized = b"0" * (50 * 1024 * 1024 + 1024)
        record("POST /api/check-file 超过 50MB → 413",
               client.post("/api/check-file",
                           files={"file": ("big.docx", oversized, "application/octet-stream")},
                           data={"college_id": COLLEGE_ID}).status_code == 413)

        accepted = client.post("/api/check-jobs/create", files=files,
                               data={"college_id": COLLEGE_ID, "include_semantic": "false"})
        job = accepted.json() if accepted.status_code == 202 else {}
        job_id = job.get("job_id")
        record("POST /api/check-jobs/create 202", accepted.status_code == 202 and bool(job_id),
               f"{accepted.status_code} job_id={job_id}")

        if job_id:
            status = client.post("/api/check-jobs/status", json={"job_id": job_id})
            status_body = status.json() if status.status_code == 200 else {}
            record("POST /api/check-jobs/status 200",
                   status.status_code == 200 and status_body.get("job_id") == job_id,
                   f"{status.status_code} status={status_body.get('status')}")

            first = client.post("/api/check-jobs/result", json={"job_id": job_id})
            first_code = first.status_code
            final = first
            deadline = time.time() + 120
            while time.time() < deadline:
                if final.status_code == 200 and (final.json().get("result") or {}).get("issues") is not None:
                    break
                time.sleep(1)
                final = client.post("/api/check-jobs/result", json={"job_id": job_id})
            final_body = final.json() if final.status_code == 200 else {}
            record("POST /api/check-jobs/result 轮询至 200",
                   final.status_code == 200 and final_body.get("status") == "succeeded",
                   f"first={first_code} final={final.status_code} status={final_body.get('status')}")

            duplicate = client.post("/api/check-jobs/create", files=files,
                                    data={"college_id": COLLEGE_ID, "job_id": job_id})
            record("POST /api/check-jobs/create 重复 job_id → 409", duplicate.status_code == 409,
                   str(duplicate.status_code))

        unknown_job = str(uuid.uuid4())
        record("POST /api/check-jobs/status 未知任务 → 404",
               client.post("/api/check-jobs/status", json={"job_id": unknown_job}).status_code == 404)
        record("POST /api/check-jobs/result 未知任务 → 404",
               client.post("/api/check-jobs/result", json={"job_id": unknown_job}).status_code == 404)
        record("POST /api/check-jobs/status 非 uuid → 422",
               client.post("/api/check-jobs/status", json={"job_id": "not-a-uuid"}).status_code == 422)
        record("POST /api/check-jobs/status 缺 job_id → 422",
               client.post("/api/check-jobs/status", json={}).status_code == 422)

        if fixable:
            fixed = client.post("/api/fix", files=files,
                                data={"college_id": COLLEGE_ID,
                                      "issues_json": json.dumps(fixable, ensure_ascii=False)})
            record("POST /api/fix 200 且返回 DOCX",
                   fixed.status_code == 200 and fixed.content[:2] == b"PK",
                   f"{fixed.status_code} bytes={len(fixed.content)} fixable={len(fixable)}")
        else:
            record("POST /api/fix 200 且返回 DOCX", False,
                   "check 未返回 fixable issue，无法验证修复链路")

        record("POST /api/fix 非法 issues_json → 400",
               client.post("/api/fix", files=files,
                           data={"college_id": COLLEGE_ID, "issues_json": "not-a-json"}).status_code == 400)

        rebuilt = client.post("/api/rebuild", files=files, data={"college_id": COLLEGE_ID})
        record("POST /api/rebuild 200 且返回 DOCX",
               rebuilt.status_code == 200 and rebuilt.content[:2] == b"PK",
               f"{rebuilt.status_code} bytes={len(rebuilt.content)}")

        record("POST /api/session/reset 200 或 503",
               client.post("/api/session/reset",
                           json={"conversation_id": "smoke-conv-1"}).status_code in (200, 503))

        rag = client.post("/api/rag/answer",
                          json={"college_id": COLLEGE_ID, "question": "一级标题用什么字号？"})
        record("POST /api/rag/answer 契约内响应",
               rag.status_code == 200 or (rag.status_code == 503 and not rag_ready),
               f"{rag.status_code} rag_ready={rag_ready}")

        feedback = client.post("/api/rag/feedback",
                               json={"college_id": COLLEGE_ID, "response_id": "nope", "rating": "helpful"})
        record("POST /api/rag/feedback 契约内响应", feedback.status_code in (200, 400, 503),
               str(feedback.status_code))

        strict = client.post("/api/check-file", files=files,
                             data={"college_id": COLLEGE_ID, "include_semantic": "false",
                                   "terminology_strict": "true"})
        record("POST /api/check-file terminology_strict=true → 200", strict.status_code == 200,
               str(strict.status_code))

        degraded = client.post("/api/check-file", files=files,
                               data={"college_id": COLLEGE_ID, "include_semantic": "true"})
        degraded_semantic = ((degraded.json().get("breakdown") or {}).get("semantic") or {}) \
            if degraded.status_code == 200 else {}
        record("POST /api/check-file include_semantic=true 按模型可用性返回且不 500",
               degraded.status_code == 200
               and (
                   (llm_ready and degraded_semantic.get("llm_enabled") is True)
                   or (
                       not llm_ready
                       and degraded_semantic.get("llm_enabled") is False
                       and bool(degraded_semantic.get("reason"))
                   )
               ),
               f"{degraded.status_code} llm_ready={llm_ready} "
               f"{json.dumps(degraded_semantic, ensure_ascii=False)[:100]}")

    failed = [item for item in RESULTS if not item[1]]
    print(f"\n==== {len(RESULTS) - len(failed)} passed / {len(failed)} failed ====")
    for name, _ok, detail in failed:
        print("FAIL", name, detail)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE))
