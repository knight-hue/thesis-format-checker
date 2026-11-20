# -*- coding: utf-8 -*-
"""修复层结构修改：题注缺失 / 插图 / 题注位置 / 随文排 / 表头重复。"""

from backend.docx_common.assets import image_path
from backend.detection.numbering import caption_text

from ._core import _to_int, _get_block, _record_skip, _parse_size_spec
from ._content import (
    _make_caption_block, _move_caption_relative, _move_floating_group,
    _save_image_bytes,
)


def _reindex_blocks(document):
    """结构性增删后，重排 block 的 index，使其等于位置。"""
    for i, block in enumerate(document.get("blocks", [])):
        block["index"] = i


# ====================== 结构性修改（最后统一处理） ======================
def _apply_structural_fixes(document, issues, images_dir):
    blocks = document.get("blocks", [])
    applied = []
    skipped = []

    # 0) 表级属性修复（不改结构）：必须在任何增删移动之前按原始 index 定位，
    #     否则后续变动会让 table 的 index 漂移。
    #     table.continued_header / table.continued_note_header 都是“开启重复标题行”
    #     这一个动作：前者来自行数/高度估算，后者来自续表标注缺表头。
    _TABLE_ATTR_RULE_IDS = ("table.continued_header", "table.continued_note_header")
    for issue in issues:
        rule_id = issue.get("rule_id")
        if rule_id not in _TABLE_ATTR_RULE_IDS:
            continue
        loc = issue.get("location") or {}
        index = _to_int(loc.get("index"))
        block = _get_block(blocks, index, kind="table")
        if block is None:
            _record_skip(skipped, rule_id, f"block index {loc.get('index')} 不是 table")
            continue
        block["header_repeat"] = True
        applied.append({"rule_id": rule_id, "index": index})

    # 1) 纯插入类（插新图），按 index 降序处理，避免互相串位。
    insertions = [
        issue for issue in issues
        if issue.get("rule_id") == "image.insert"
    ]
    insertions.sort(
        key=lambda issue: _to_int((issue.get("location") or {}).get("index"), -1),
        reverse=True,
    )
    for issue in insertions:
        rule_id = issue.get("rule_id", "")
        loc = issue.get("location") or {}
        index = _to_int(loc.get("index"))
        if index is None or not 0 <= index < len(blocks):
            _record_skip(skipped, rule_id, f"block index {loc.get('index')} 越界")
            continue

        if rule_id == "image.insert":
            data = issue.get("image_bytes")
            if not data:
                _record_skip(skipped, rule_id, "缺少新图片字节（image_bytes）")
                continue
            fname = _save_image_bytes(images_dir, data, index)
            new_block = {"type": "image", "path": image_path(fname)}
            width, height = _parse_size_spec(issue.get("expected"))
            if width is not None:
                new_block["width_inch"] = width
            if height is not None:
                new_block["height_inch"] = height
            blocks.insert(index + 1, new_block)
            applied.append({"rule_id": rule_id, "index": index, "inserted": True})
            continue

    # 2) 移动类（题注位置），按 kind 就近搜索图表，不依赖原始 index。
    moves = [
        issue for issue in issues
        if issue.get("rule_id") in ("table.caption_position", "figure.caption_position")
    ]
    for issue in moves:
        rule_id = issue.get("rule_id", "")
        loc = issue.get("location") or {}
        index = _to_int(loc.get("index"))
        if index is None or not 0 <= index < len(blocks):
            _record_skip(skipped, rule_id, f"block index {loc.get('index')} 越界")
            continue
        kind = "figure" if rule_id.startswith("figure") else "table"
        if _move_caption_relative(blocks, index, kind):
            applied.append({"rule_id": rule_id, "index": index, "moved": True})
        else:
            _record_skip(skipped, rule_id, "附近没有可对齐的图片/表格")

    # 3) 随文排（figure.placement / table.placement）：把图/表组整体移到首次提及之后。
    placements = [
        issue for issue in issues
        if issue.get("rule_id") in ("figure.placement", "table.placement")
    ]
    # 降序处理：移动较后组不影响较前组的 index，避免连续移动串位。
    placements.sort(
        key=lambda issue: _to_int((issue.get("location") or {}).get("index"), -1),
        reverse=True,
    )
    for issue in placements:
        rule_id = issue.get("rule_id", "")
        loc = issue.get("location") or {}
        index = _to_int(loc.get("index"))
        if index is None or not 0 <= index < len(blocks):
            _record_skip(skipped, rule_id, f"block index {loc.get('index')} 越界")
            continue
        kind = "figure" if rule_id.startswith("figure") else "table"
        if _move_floating_group(blocks, index, kind):
            applied.append({"rule_id": rule_id, "index": index, "moved": True})
        else:
            _record_skip(skipped, rule_id, "未找到正文提及或已在正确位置")

    _reindex_blocks(document)
    return {"applied": applied, "skipped": skipped,
            "applied_count": len(applied), "skipped_count": len(skipped)}
