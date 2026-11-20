# -*- coding: utf-8 -*-
"""修复层文字应用：把 generate_text_fixes 生成的 changes 写回段落块。"""
from ._core import _set_paragraph_text, _record_skip


def paragraph_texts(document):
    """{block_index: text}，供 generate_text_fixes 使用（仅段落块）。"""
    return {
        block.get("index"): block.get("text") or ""
        for block in document.get("blocks", [])
        if block.get("type") == "paragraph"
    }


def apply_text_changes(document, changes):
    """把 generate_text_fixes 生成的文字修改应用到 JSON 段落块。"""
    blocks = document.get("blocks", [])
    by_index = {
        block.get("index"): block
        for block in blocks
        if block.get("type") == "paragraph"
    }
    applied = []
    skipped = []
    for change in changes:
        rule_id = change.get("rule_id", "")
        index = change.get("index")
        block = by_index.get(index)
        if block is None:
            _record_skip(skipped, rule_id, f"block index {index} 不存在或不是 paragraph")
            continue
        if (block.get("text") or "") != (change.get("old_text") or ""):
            _record_skip(skipped, rule_id, "写回前段落内容已变化，拒绝覆盖", index=index)
            continue
        _set_paragraph_text(block, change.get("new_text") or "",
                            preserve_latin=bool(change.get("semantic")))
        applied.append({
            "rule_id": rule_id,
            "index": index,
            "old_text": (change.get("old_text") or "")[:30],
            "new_text": (change.get("new_text") or "")[:30],
        })
    return {"applied": applied, "skipped": skipped,
            "applied_count": len(applied), "skipped_count": len(skipped)}
