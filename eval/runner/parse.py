# -*- coding: utf-8 -*-
"""解析层评测：结构识别准确率 + 文本保真（在干净样张上测量）。

干净样张 = 合规基线：识别层应把每个段/表认成预期 kind。指标：
- kind 准确率：kind 判定正确的块数 / 参与判定的块数；
- 文本保真率：期望文本片段在解析结果中出现的比例；
- 块数一致性：解析 blocks 数与构建段落数一致。
"""
import tempfile

from .. import metrics
from . import parse_docx, resolve_block_index

# 段名 → (定位文本, 期望 kind/type)。定位文本取基线原文。
EXPECTED = {
    "title": ("基于深度学习的论文格式校验研究", "title"),
    "abstract_title": ("摘要", "abstract_title"),
    "abstract_body": ("论文格式校验系统将", "abstract_body"),
    "keyword_title": ("关键字：", "keyword_title"),
    "keyword_body": ("格式校验，文档解析，契约重建，DOCX", "keyword_body"),
    "en_title": ("Thesis Format Checking Based on Deep Learning", "en_title"),
    "en_abstract_title": ("Abstract", "en_abstract_title"),
    "en_abstract_body": ("This system parses DOCX", "en_abstract_body"),
    "en_keyword_title": ("Keywords：", "en_keyword_title"),
    "en_keyword_body": ("format checking, document parsing", "en_keyword_body"),
    "h1_1": ("第1章 绪论", "heading_1"),
    "body_1": ("近年来，深度学习", "body"),
    "h2_1": ("1.1 研究背景", "heading_2"),
    "body_2": ("研究背景主要围绕", "body"),
    "h3_1": ("1.1.1 深度学习方法", "heading_3"),
    "body_3": ("深度学习方法通过", "body"),
    "h2_2": ("1.2 相关工作", "heading_2"),
    "body_4": ("相关工作包括", "body"),
    "h1_2": ("第2章 系统设计", "heading_1"),
    "h2_3": ("2.1 系统架构", "heading_2"),
    "body_5": ("系统整体架构如图2-1所示", "body"),
    "fig_caption": ("图2-1 系统架构图", "figure_caption"),
    "body_6": ("实验环境配置如表2-1所示", "body"),
    "table_caption": ("表2-1 实验环境配置", "table_caption"),
    "body_7": ("本文验证了该系统的有效性与可靠性", "body"),
    "reference_title": ("参考文献", "reference_title"),
    "ref_entry_1": ("［1］张三", "reference_entry"),
    "ref_entry_2": ("［2］Smith J", "reference_entry"),
    "ack_title": ("致    谢", "acknowledgement_title"),
    "ack_body": ("感谢导师与实验室全体同学的帮助", "acknowledgement_body"),
}


def run_parse_eval(cases, config) -> dict:
    """在干净样张上测量解析层指标。返回结构化结果。"""
    clean_cases = [c for c in cases if c.is_clean]
    if not clean_cases:
        return {"skipped": True, "reason": "无干净样张"}

    results = []
    for case in clean_cases:
        with tempfile.TemporaryDirectory(prefix="eval_parse_") as tmp:
            document = parse_docx(case.docx_path, case.college_id, tmp)
        blocks = document.get("blocks", [])
        checks = []
        correct = 0
        for name, (text, expected) in EXPECTED.items():
            idx = resolve_block_index(document, text, kind=None)
            if idx is None:
                checks.append({"name": name, "found": False, "expected": expected,
                               "actual": None})
                continue
            block = next(b for b in blocks if b.get("index") == idx)
            actual = block.get("kind") or block.get("type")
            ok = actual == expected
            correct += int(ok)
            checks.append({"name": name, "found": True, "expected": expected,
                           "actual": actual, "ok": ok})
        results.append({
            "case": case.name,
            "blocks": len(blocks),
            "checked": len(checks),
            "correct_kinds": correct,
            "kind_accuracy": metrics.accuracy(correct, len(checks)),
            "details": checks,
        })

    total_checked = sum(r["checked"] for r in results)
    total_correct = sum(r["correct_kinds"] for r in results)
    return {
        "skipped": False,
        "kind_accuracy": metrics.accuracy(total_correct, total_checked),
        "correct_kinds": total_correct,
        "checked_kinds": total_checked,
        "block_count": results[0]["blocks"] if results else 0,
        "cases": results,
    }
