# -*- coding: utf-8 -*-
"""性能基准：分阶段耗时（解析/检测/写回/端到端）与预热探测。"""
import json
import statistics
import tempfile
import time
from pathlib import Path

from backend.detection.checker_service import check_document
from backend.fix.fixability import annotate_issues
from fastapi.testclient import TestClient

import backend.api as api
from ..runner import parse_docx

DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _median_ms(samples):
    return round(statistics.median(samples), 2) if samples else None


def run_timing_eval(cases, config) -> dict:
    """在标准样张上测量各阶段耗时。

    解析/检测/端到端用干净样张（代表性最强）；写回需要可修复问题，
    用一个注入样张（有金标准）计时。
    """
    clean_sample = next((c for c in cases if c.is_clean), cases[0])
    inject_sample = next((c for c in cases if not c.is_clean), None)
    if inject_sample is None:
        # 调用方未提供注入样张（如 --stage perf）：现场构建一个可修复的注入样张
        from eval.synthetic.injectors import INJECTORS
        from eval.synthetic.corpus import _build_docx
        first_injector = sorted(INJECTORS.keys())[0]
        inject_sample = _build_docx(config, f"perf_inject_{first_injector}",
                                    mutate=INJECTORS[first_injector])
    payload = Path(clean_sample.docx_path).read_bytes()
    college_id = clean_sample.college_id
    client = TestClient(api.app)

    parse_ms, detect_ms, writeback_ms, e2e_ms = [], [], [], []
    writeback_fixable = None
    try:
        for _ in range(config.timing_repeat):
            with tempfile.TemporaryDirectory(prefix="eval_timing_") as tmp:
                t0 = time.perf_counter()
                document = parse_docx(clean_sample.docx_path, college_id, tmp)
                parse_ms.append((time.perf_counter() - t0) * 1000)

                t0 = time.perf_counter()
                result = check_document(document, college_id, rag_service=None,
                                        include_semantic=config.include_semantic)
                detect_ms.append((time.perf_counter() - t0) * 1000)
                annotate_issues(result["issues"], llm_available=False)

            # 端到端（同步接口）：上传 → 解析 → 检测
            t0 = time.perf_counter()
            resp = client.post(
                "/api/check-file",
                data={"college_id": college_id,
                      "include_semantic": str(config.include_semantic).lower()},
                files={"file": ("paper.docx", payload, DOCX_CONTENT_TYPE)},
            )
            e2e_ms.append((time.perf_counter() - t0) * 1000)

        # 写回计时：注入样张里取 1 个可修复问题走 /api/fix
        if inject_sample is not None and writeback_fixable is None:
            inject_payload = Path(inject_sample.docx_path).read_bytes()
            with tempfile.TemporaryDirectory(prefix="eval_timing_wb_") as tmp:
                document = parse_docx(inject_sample.docx_path, college_id, tmp)
                result = check_document(document, college_id, rag_service=None,
                                        include_semantic=config.include_semantic)
                annotate_issues(result["issues"], llm_available=False)
                writeback_fixable = [i for i in result["issues"] if i.get("fixable")][:1]
        if writeback_fixable:
            t0 = time.perf_counter()
            client.post(
                "/api/fix",
                data={"college_id": college_id,
                      "issues_json": json.dumps(writeback_fixable)},
                files={"file": ("paper.docx", Path(inject_sample.docx_path).read_bytes(),
                                DOCX_CONTENT_TYPE)},
            )
            writeback_ms.append((time.perf_counter() - t0) * 1000)
    finally:
        client.close()

    return {
        "sample": clean_sample.name,
        "writeback_sample": inject_sample.name if inject_sample else None,
        "parse_ms_median": _median_ms(parse_ms),
        "detect_ms_median": _median_ms(detect_ms),
        "writeback_ms_median": _median_ms(writeback_ms),
        "e2e_ms_median": _median_ms(e2e_ms),
        "samples": {
            "parse_ms": [round(x, 2) for x in parse_ms],
            "detect_ms": [round(x, 2) for x in detect_ms],
            "writeback_ms": [round(x, 2) for x in writeback_ms],
            "e2e_ms": [round(x, 2) for x in e2e_ms],
        },
        "blocks": _block_count(payload, sample_college_id=college_id),
    }


def _block_count(payload, sample_college_id):
    with tempfile.TemporaryDirectory(prefix="eval_count_") as tmp:
        path = Path(tmp) / "in.docx"
        path.write_bytes(payload)
        document = parse_docx(str(path), sample_college_id, tmp)
        return len(document.get("blocks", []))


def run_warmup_probe(config) -> dict:
    """探测 AI 能力初始化（生成模型门面 / 知识库）。

    无 API Key 或无嵌入模型资产时如实记录状态，不报错。
    """
    out = {"llm": {"status": "skipped", "detail": "未探测"}, "rag": None}
    try:
        from backend.rag.llm_service import LLMService
        t0 = time.perf_counter()
        service = LLMService()
        elapsed = round(time.perf_counter() - t0, 2)
        out["llm"] = {
            "status": "ready" if service is not None else "failed",
            "init_seconds": elapsed,
            "detail": service.describe() if service is not None else "构造失败",
        }
    except Exception as exc:
        out["llm"] = {"status": "failed", "init_seconds": None,
                      "detail": f"{type(exc).__name__}: {exc}"}

    # 知识库：需要嵌入模型资产（models/ 目录缺失时如实记录）
    try:
        from backend.config import EMBEDDING_DIR
        from pathlib import Path as P
        models = P.cwd() / EMBEDDING_DIR
        if not models.exists():
            out["rag"] = {"status": "skipped",
                          "detail": f"嵌入模型资产缺失（{EMBEDDING_DIR}），未构建知识库"}
        else:
            from backend.rag.rag_service import RAGService
            t0 = time.perf_counter()
            rag = RAGService(llm_service=None)
            out["rag"] = {"status": "ready",
                          "init_seconds": round(time.perf_counter() - t0, 2),
                          "detail": "知识库门面就绪"}
    except Exception as exc:
        out["rag"] = {"status": "failed", "detail": f"{type(exc).__name__}: {exc}"}
    return out
