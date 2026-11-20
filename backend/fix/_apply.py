# -*- coding: utf-8 -*-
"""修复层分派：把格式类 issue 应用到 document 的对应数据块。"""

import re

from backend.docx_common.assets import image_path
from backend.detection.numbering import caption_text, expected_formula_number

# 判定层（captions._check_table_block）是逐 run 读表内中文字体/字号的；
# 修复必须改到每一个 run，只改单元格代表值会让重检继续报同一处。
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_CM_IN_PT = 28.3465

from ._core import (
    _TEXT_LAYER_RULE_IDS, _STRUCTURAL_RULE_IDS,
    _get_block, _to_int, _parse_number, _record_skip, _apply_generic_format,
)
from ._content import (
    _rewrite_caption_number_block, _save_image_bytes,
    _replace_formula_number_block, _move_formula_number_to_end_block,
    _remove_formula_dotted_line_block, _pin_image_paths, _number_on_own_line,
)


# ====================== 各类 issue 的应用 ======================
def _apply_section_fix(document, issue, applied, skipped):
    """页面设置修复：按 issue 的 location.index 定位到具体分节。

    多节文档里每节的页边距/方向各自独立，必须按下标寻址；早前固定写
    sections[0] 会让第 2 节起的设置永远修不动，却照样上报"已应用"。
    """
    rule_id = issue.get("rule_id", "")
    expected = issue.get("expected")
    sections = document.setdefault("sections", [])
    index = _to_int((issue.get("location") or {}).get("index"))
    index = 0 if index is None else index
    if not sections and index == 0:
        sections.append({})
    if not 0 <= index < len(sections):
        _record_skip(skipped, rule_id, f"分节下标 {index} 不存在（文档共 {len(sections)} 节）")
        return
    sec = sections[index]

    if rule_id.startswith("page.") and rule_id.endswith("_cm"):
        key = rule_id[len("page."):]  # left_cm / right_cm / ... / footer_cm
        value = _parse_number(expected)
        if value is None:
            _record_skip(skipped, rule_id, f"无法解析期望值 {expected!r}")
            return
        sec[key] = value
        applied.append({"rule_id": rule_id, "index": index, "format": {key: value}})
        return
    _record_skip(skipped, rule_id, "暂无自动修复映射")


def _apply_cell_fix(document, issue, applied, skipped):
    rule_id = issue.get("rule_id", "")
    loc = issue.get("location") or {}
    block = _get_block(document.get("blocks", []), loc.get("index"), kind="table")
    if block is None:
        _record_skip(skipped, rule_id, f"表格 index {loc.get('index')} 不存在或不是 table")
        return
    row, col = _to_int(loc.get("row")), _to_int(loc.get("col"))
    cell = next(
        (c for c in block.get("cells", [])
         if c.get("row") == row and c.get("col") == col),
        None,
    )
    if cell is None:
        _record_skip(skipped, rule_id, f"单元格 ({row}, {col}) 不存在")
        return
    # 中文字体：只改中文槽（w:eastAsia），西文字体保持不变，
    # 否则会把单元格里的西文/数字一起改成中文字体。
    # "table.cell_font" 是旧版规则编号，语义同样是中文字体，保留以兼容已存的结果快照。
    if rule_id in ("table.cell_east_asia_font", "table.cell_font"):
        value = str(issue.get("expected") or "").strip()
        if not value:
            _record_skip(skipped, rule_id, "期望值为空")
            return
        cell["east_asia_font"] = value
        # 同步所有含中文的 run：判定不看单元格代表值。
        for run in cell.get("runs") or []:
            if _CJK_RE.search(run.get("text") or ""):
                run["east_asia_font"] = value
        applied.append({"rule_id": rule_id, "index": loc.get("index"),
                        "row": row, "col": col, "format": {"east_asia_font": value}})
        return
    # 表内字号：模板 §5.5「表内字体字号：宋体五号字」。
    if rule_id == "table.cell_font_size":
        value = _parse_number(issue.get("expected"))
        if value is None:
            _record_skip(skipped, rule_id, f"无法解析期望值 {issue.get('expected')!r}")
            return
        cell["font_size_pt"] = value
        # 字号判定覆盖单元格内所有非空 run（含西文 run）。
        for run in cell.get("runs") or []:
            if (run.get("text") or "").strip():
                run["font_size_pt"] = value
        applied.append({"rule_id": rule_id, "index": loc.get("index"),
                        "row": row, "col": col, "format": {"font_size_pt": value}})
        return
    _record_skip(skipped, rule_id, "暂无自动修复映射")


def _apply_image_fix(document, issue, images_dir, applied, skipped):
    rule_id = issue.get("rule_id", "")
    loc = issue.get("location") or {}
    index = loc.get("index")
    block = _get_block(document.get("blocks", []), index, kind="image")
    if block is None:
        _record_skip(skipped, rule_id, f"图片 index {index} 不存在或不是 image")
        return

    if rule_id == "image.replace":
        data = issue.get("image_bytes")
        if not data:
            _record_skip(skipped, rule_id, "缺少新图片字节（image_bytes）")
            return
        fname = _save_image_bytes(images_dir, data, index)
        block["path"] = image_path(fname)
        applied.append({"rule_id": rule_id, "index": index, "replaced": True})
        return
    _record_skip(skipped, rule_id, "暂无自动修复映射")


def _apply_paragraph_fix(document, issue, applied, skipped):
    rule_id = issue.get("rule_id", "")
    loc = issue.get("location") or {}
    index = loc.get("index")
    blocks = document.get("blocks", [])
    block = _get_block(blocks, index, kind="paragraph")
    if block is None:
        _record_skip(skipped, rule_id, f"block index {index} 不是可写回的 paragraph")
        return

    # 自动编号：编号不在段落文字里，由 write_back.numbering 改 numbering.xml；
    # 格式/文字层不处理，也不记跳过（否则前端会看到一条假“跳过”）。
    if issue.get("auto_number"):
        return

    # 题注编号重写
    if rule_id in ("figure.caption_number", "table.caption_number"):
        kind = "figure" if rule_id.startswith("figure") else "table"
        new_number = caption_text(blocks, index, kind)
        if _rewrite_caption_number_block(block, new_number, kind):
            applied.append({"rule_id": rule_id, "index": index, "caption": block["text"]})
        else:
            _record_skip(skipped, rule_id, "题注编号无需重写")
        return

    # 公式（alignment 走通用格式，其余专用处理）
    if rule_id.startswith("formula.") and rule_id != "formula.alignment":
        target = None
        if rule_id in ("formula.number_format", "formula.number_continuity"):
            target = expected_formula_number(blocks, index)
        if _apply_formula_fix(block, rule_id, target):
            applied.append({"rule_id": rule_id, "index": index})
        elif rule_id == "formula.number_position" and _number_on_own_line(block):
            # 编号独占一段（公式与编号分行）：合并要连带删掉编号段，
            # 属于结构性改动，这里只如实说明需要手动处理。
            _record_skip(skipped, rule_id, "编号独占一行，需手动把编号移到公式行末")
        else:
            _record_skip(skipped, rule_id, "公式内容无变化或无需修复")
        return

    # 交给文字改写层的规则
    if rule_id in _TEXT_LAYER_RULE_IDS:
        _record_skip(skipped, rule_id, "文字类规则，交由文字改写层处理")
        return

    # 参考文献顶格：判定看首行起点 = 左缩进 + 首行缩进（refs._check_reference_entry）。
    # 只把首行缩进改成 0 时，左缩进 29.1 pt 会让首行起点仍为 29.1 pt，重检继续报。
    if rule_id == "reference.entry_indent":
        block["left_indent_pt"] = 0.0
        block["first_line_indent_pt"] = 0.0
        applied.append({"rule_id": rule_id, "index": index})
        return

    # 续表标注对齐：判定是按“到版心左边界的 cm 距离”算的，alignment 只是辅助。
    # 旧实现把 expected 整句写进 alignment，重检仍会报位置不符。
    if rule_id == "table.continued_note_alignment":
        match = re.search(r"距版心左边界\s*([0-9]+(?:\.[0-9]+)?)\s*cm",
                          str(issue.get("expected") or ""))
        if not match:
            _record_skip(skipped, rule_id,
                         "目标位置未给出可解析的距离，需人工按表格左边线对齐")
            return
        block["alignment"] = "left"
        block["first_line_indent_pt"] = 0.0
        block["left_indent_pt"] = float(match.group(1)) * _CM_IN_PT
        applied.append({"rule_id": rule_id, "index": index,
                        "format": {"left_indent_pt": block["left_indent_pt"]}})
        return

    run_index = loc.get("run")
    if _apply_generic_format(block, rule_id, issue.get("expected"), run_index):
        applied.append({"rule_id": rule_id, "index": index})
    else:
        _record_skip(skipped, rule_id, "暂无自动修复映射")


def _apply_formula_fix(block, rule_id, target_number=None):
    """公式内容类修复；返回是否实际改动。"""
    if rule_id in ("formula.number_format", "formula.number_continuity"):
        if target_number is None:
            return False
        return _replace_formula_number_block(block, target_number)

    if rule_id == "formula.number_position":
        return _move_formula_number_to_end_block(block)
    if rule_id == "formula.dotted_line":
        return _remove_formula_dotted_line_block(block)
    return False


# ====================== 格式层入口 ======================
def apply_format_fixes(document, issues, images_dir=None):
    """把非结构性的格式 issue 应用到 JSON 块（原地修改 document）。

    图片路径会先绑定为显式 path，保证后续增删不改变图片文件定位。
    结构性 issue（题注缺失/插图/题注位置）不在此处理，交由
    apply_structural_fixes 在文字改写之后统一执行。
    """
    _pin_image_paths(document, images_dir)
    applied = []
    skipped = []

    for issue in issues:
        rule_id = issue.get("rule_id", "")
        loc = issue.get("location") or {}
        loc_type = loc.get("type")

        # 前端注入的图片替换项无 source，先按 rule_id 处理。
        if rule_id == "image.replace" and loc_type == "image":
            _apply_image_fix(document, issue, images_dir, applied, skipped)
            continue

        if issue.get("source") != "rule_engine":
            continue

        # 结构性规则交由 _apply_structural_fixes 处理，格式层不记录。
        if rule_id in _STRUCTURAL_RULE_IDS:
            continue

        if loc_type == "section":
            _apply_section_fix(document, issue, applied, skipped)
        elif loc_type == "table_cell":
            _apply_cell_fix(document, issue, applied, skipped)
        elif loc_type == "image":
            _apply_image_fix(document, issue, images_dir, applied, skipped)
        elif loc_type == "paragraph":
            _apply_paragraph_fix(document, issue, applied, skipped)
        else:
            _record_skip(skipped, rule_id, f"非可写回定位（type={loc_type}）")

    return {"applied": applied, "skipped": skipped,
            "applied_count": len(applied), "skipped_count": len(skipped)}
