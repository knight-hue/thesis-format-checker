# -*- coding: utf-8 -*-
"""JSON 报告：把完整评测结果（含逐用例明细）写成可审计的 JSON 文件。"""
import json
from datetime import datetime
from pathlib import Path

from ..config import EvalConfig


def dump_json(report: dict, config: EvalConfig) -> Path:
    out = config.report_dir / "eval_report.json"
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out


def build_report_meta(config: EvalConfig) -> dict:
    import platform
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "college_id": config.college_id,
        "include_semantic": config.include_semantic,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "config": {
            "repeat_clean": config.repeat_clean,
            "scale_pages": list(config.scale_pages),
            "timing_repeat": config.timing_repeat,
        },
    }
