# -*- coding: utf-8 -*-
"""规模扫描：按页数分档生成论文，测解析/检测耗时与内存峰值。"""
import statistics
import tempfile
import time
import tracemalloc
from pathlib import Path

from backend.detection.checker_service import check_document

from ..runner import parse_docx
from ..synthetic.baseline import build_scaled_paper


def run_scale_eval(config) -> dict:
    """按 config.scale_pages 档位扫描，返回每档耗时与内存。"""
    rows = []
    for pages in config.scale_pages:
        n_paras = max(4, pages * config.paragraphs_per_page // 10)
        n_tables = max(1, pages * config.tables_per_page // 10)
        n_figures = max(1, pages * config.figures_per_page // 10)
        parse_samples, detect_samples, mem_samples = [], [], []
        blocks_count = None
        with tempfile.TemporaryDirectory(prefix="eval_scale_") as tmp:
            docx_path = str(Path(tmp) / f"scale_{pages}.docx")
            build_scaled_paper(docx_path, n_paras, n_tables, n_figures)
            for _ in range(config.scale_repeat):
                tracemalloc.start()
                t0 = time.perf_counter()
                document = parse_docx(docx_path, config.college_id, tmp)
                parse_ms = (time.perf_counter() - t0) * 1000
                _, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                mem_samples.append(peak / 1024 / 1024)
                parse_samples.append(parse_ms)
                blocks_count = len(document.get("blocks", []))

                t0 = time.perf_counter()
                check_document(document, config.college_id, rag_service=None,
                               include_semantic=config.include_semantic)
                detect_samples.append((time.perf_counter() - t0) * 1000)

        rows.append({
            "target_pages": pages,
            "body_paragraphs": n_paras,
            "tables": n_tables,
            "figures": n_figures,
            "blocks": blocks_count,
            "parse_ms_median": round(statistics.median(parse_samples), 2),
            "detect_ms_median": round(statistics.median(detect_samples), 2),
            "total_ms_median": round(statistics.median(
                [a + b for a, b in zip(parse_samples, detect_samples)]), 2),
            "peak_mem_mb": round(max(mem_samples), 2),
            "parse_ms_samples": [round(x, 2) for x in parse_samples],
            "detect_ms_samples": [round(x, 2) for x in detect_samples],
        })
    return {"rows": rows, "units": "目标页数 → 块数 → 解析/检测耗时(ms) → 峰值内存(MB)"}
