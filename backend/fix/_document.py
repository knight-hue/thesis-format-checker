# -*- coding: utf-8 -*-
"""修复层总入口：把确认的 issue 应用到解析契约的 JSON 数据块上。"""
from ._apply import apply_format_fixes
from ._structural import _apply_structural_fixes
from ._text import paragraph_texts, apply_text_changes


def _expand_issue_locations(issues):
    """把带 locations 的聚合问题展开成逐位置 issue（语义类已用 blocks 覆盖多段，不展开）。"""
    expanded = []
    for issue in issues:
        locations = issue.get("locations") if isinstance(issue, dict) else None
        if issue.get("source") == "rag" or not locations or len(locations) <= 1:
            expanded.append(issue)
            continue
        for location in locations:
            clone = dict(issue)
            clone.pop("locations", None)
            clone["location"] = location
            expanded.append(clone)
    return expanded


def fix_document(document, issues, college_id=None, rag_service=None, images_dir=None):
    """修复数据块的唯一入口；写回层再选择原位 patch 或显式重建。

    顺序：格式修复（非结构性） → 文字改写 → 结构性增删/移动 → 重排 index。
    """
    # 惰性导入：rag 是 fix 唯一的上层依赖（只在真跑写回时加载改写服务），
    # 这样 fix 包在导入期不依赖 rag。
    from backend.rag.text_rewrite_service import generate_text_fixes

    if not isinstance(issues, list):
        raise ValueError("issues 必须是列表")
    issues = _expand_issue_locations(issues)

    # 1) 非结构性格式修复（含图片路径绑定）
    format_result = apply_format_fixes(document, issues, images_dir=images_dir)

    # 2) 文字改写（确定性 + 语义 LLM），此时 block index 尚未被增删改变。
    text_plan = generate_text_fixes(
        paragraph_texts(document), issues,
        rag_service=rag_service, college_id=college_id,
    )
    text_result = apply_text_changes(document, text_plan["changes"])

    # 3) 结构性增删 / 移动（题注缺失、插图、题注位置）。
    structural_result = _apply_structural_fixes(document, issues, images_dir)

    # 4) 合并结果。
    text_skipped = text_result["skipped"] + text_plan.get("skipped", [])
    applied_count = (
        format_result["applied_count"]
        + text_result["applied_count"]
        + structural_result["applied_count"]
    )
    skipped_count = (
        format_result["skipped_count"]
        + len(text_skipped)
        + structural_result["skipped_count"]
    )
    return {
        "format": format_result,
        "text": {
            "applied": text_result["applied"],
            "skipped": text_skipped,
            "applied_count": text_result["applied_count"],
            "skipped_count": len(text_skipped),
            "llm_calls": text_plan.get("llm_calls", 0),
        },
        "structural": structural_result,
        "applied_count": applied_count,
        "skipped_count": skipped_count,
        "llm_calls": text_plan.get("llm_calls", 0),
    }
