"""DOCX 表格解析。"""
from docx.oxml.ns import qn
from docx.table import _Cell

from backend.docx_common.alignment import VALID_TABLE
from backend.docx_common.runs import iter_runs

from .font_reader import read_explicit_alignment, read_run


def _row_repeats_header(row):
    """行是否设置了“重复标题行”（<w:tblHeader/>）。"""
    tr_pr = row._tr.find(qn("w:trPr"))
    if tr_pr is None:
        return False
    return tr_pr.find(qn("w:tblHeader")) is not None


# ========== 表格样式 / 对齐（W4） ==========


def _table_style_name(table):
    """表格样式名（<w:tblStyle>）；无样式时返回 None。

    取名字而不是 w:val（样式 ID），因为样式 ID 在别的模板里没有意义，
    重建时只能按名字在目标模板里查找。样式已被删除时退回样式 ID。
    """
    tbl_pr = table._tbl.tblPr
    if tbl_pr is None:
        return None
    style = tbl_pr.find(qn("w:tblStyle"))
    if style is None:
        return None
    style_id = style.get(qn("w:val"))
    if not style_id:
        return None
    try:
        return table.style.name or style_id
    except Exception:
        return style_id


def _table_alignment(table):
    """表格对齐（<w:tblPr>/<w:jc>）；未设置时返回 None。"""
    tbl_pr = table._tbl.tblPr
    if tbl_pr is None:
        return None
    jc = tbl_pr.find(qn("w:jc"))
    if jc is None:
        return None
    value = (jc.get(qn("w:val")) or "").strip().lower()
    return value if value in VALID_TABLE else None


# ========== 合并单元格（P3） ==========

def _tc_props(tc):
    return tc.find(qn("w:tcPr"))


def _tc_grid_span(tc):
    """单元格水平合并跨列数（<w:gridSpan w:val="N"/>），未设置时默认 1。"""
    tc_pr = _tc_props(tc)
    if tc_pr is None:
        return 1
    grid_span = tc_pr.find(qn("w:gridSpan"))
    if grid_span is None:
        return 1
    try:
        return int(grid_span.get(qn("w:val")) or 1)
    except (TypeError, ValueError):
        return 1


def _tc_vmerge(tc):
    """垂直合并状态：None=未合并；'restart'=合并起始行；'continue'=续行。"""
    tc_pr = _tc_props(tc)
    if tc_pr is None:
        return None
    vmerge = tc_pr.find(qn("w:vMerge"))
    if vmerge is None:
        return None
    val = vmerge.get(qn("w:val"))
    return "restart" if val == "restart" else "continue"


def _table_indent_pt(table):
    """表格左缩进（磅，<w:tblPr>/<w:tblInd>）。未设置按 0；百分比等算不准的返回 None。"""
    tbl_pr = table._tbl.tblPr
    if tbl_pr is None:
        return 0.0
    ind = tbl_pr.find(qn("w:tblInd"))
    if ind is None:
        return 0.0
    kind = (ind.get(qn("w:type")) or "dxa").strip().lower()
    try:
        value = int(ind.get(qn("w:w")) or 0)
    except (TypeError, ValueError):
        return 0.0
    if kind == "dxa":
        return round(value / 20.0, 2)
    # pct/auto 要按表格宽度换算才准，不猜
    return None


def _column_widths_inches(table):
    """列宽（英寸）：优先取 <w:tblGrid>/<w:gridCol>（dxa 单位，1440 dxa = 1 英寸）。

    旧实现用 rows[0].cells 逐格取宽，首行一旦有 gridSpan 合并，
    同一格会被重复计数、列位错位。tblGrid 是网格级权威列宽，
    不受合并影响；缺省时退化为首行 tc 宽度，按 gridSpan 均分展开。
    """
    tbl_grid = table._tbl.tblGrid
    grid_cols = tbl_grid.findall(qn("w:gridCol")) if tbl_grid is not None else None
    if grid_cols:
        widths = []
        for grid_col in grid_cols:
            try:
                widths.append(int(grid_col.get(qn("w:w")) or 0) / 1440.0)
            except (TypeError, ValueError):
                widths.append(0.0)
        return widths
    rows = table.rows
    if not rows:
        return []
    widths = []
    for tc in rows[0]._tr.findall(qn("w:tc")):
        tc_pr = tc.find(qn("w:tcPr"))
        tc_w = tc_pr.find(qn("w:tcW")) if tc_pr is not None else None
        try:
            raw = int(tc_w.get(qn("w:w")) or 0) if tc_w is not None else 0
        except (TypeError, ValueError):
            raw = 0
        span = _tc_grid_span(tc)
        if span > 1 and raw > 0:
            widths.extend([raw / span / 1440.0] * span)
        else:
            widths.append(raw / 1440.0)
    return widths


def _cell_fonts(cell, doc, theme_map):
    """遍历单元格全部段落/run，聚合字体字号 + 返回 run 明细（P2/P8）。

    旧实现只取 cell.paragraphs[0].runs[0]，字号只读 run.font.size：
    - 多段落/多 run 混排只看到第一个 run；
    - 字号写在样式里时 run.font.size 恒为 None，导致 cell_font_size 永久漏检。
    这里直接复用 font_reader.read_run 的四级字号回退（run 直接值 → 字符样式链
    → 段落样式链 → docDefaults），并遍历全部 run（含超链接内的 run），
    聚合值取“首个非空 run”，空单元格回退到首 run。

    run 顺序统一走 docx_common.runs.iter_runs（P8）：旧实现用 ``p.iter(w:r)``，
    会把 w:ins/w:del、文本框等嵌套结构里的 run 也算进来，与写回层按
    ``Paragraph.runs`` 定位 run 的下标口径不一致，导致 run 级修复错位。
    """
    run_infos = []
    picked = None
    for paragraph in cell.paragraphs:
        for run, _hyperlink in iter_runs(paragraph):
            info = read_run(run, doc, theme_map, paragraph)
            run_infos.append(info)
            if picked is None and info.get("text", "").strip():
                picked = info
    if picked is None and run_infos:
        picked = run_infos[0]
    if picked is None:
        return None, None, None, run_infos
    return (
        picked.get("font_name"),
        picked.get("east_asia_font"),
        picked.get("font_size_pt"),
        run_infos,
    )


def parse_table_block(table, doc, theme_map=None):
    rows = table.rows
    if not rows:
        return None
    # 旧实现（P3 问题点）：
    #     widths = [
    #         cell.width.inches if cell.width is not None else 0.0
    #         for cell in rows[0].cells
    #     ]
    # rows[0].cells 在首行存在 gridSpan 合并时会把同一格重复返回，
    # 列宽比例随之错位；改为网格级 tblGrid 解析。
    widths = _column_widths_inches(table)
    total = sum(widths)
    # 网格宽度合计（磅）：审“续表与表格左边线对齐”要算表格左边线在哪。
    # 取不到真实网格宽度时留 None（下表按均分宽度估），不让下游拿估值当实测。
    measured_width_pt = round(total * 72.0, 2) if total > 0 else None
    if not widths:
        # 无 tblGrid 也无 tcW（自动调整列宽的表格）：按首行 gridSpan 之和估列数，
        # 列宽比例给均分值。旧实现这种情况下既不输出 n_cols 也不输出
        # col_width_ratio，写回层只能退化成硬编码列宽（P8）。
        widths = [1.0] * max(sum(_tc_grid_span(tc)
                                 for tc in rows[0]._tr.findall(qn("w:tc"))), 1)
        total = sum(widths)
    if total <= 0:
        widths = [1.0] * len(widths)
        total = sum(widths)
    block = {
        "type": "table",
        "cells": [],
        "header_repeat": any(_row_repeats_header(row) for row in rows),
        "n_rows": len(rows),
        "n_cols": len(widths),
        "col_width_ratio": [round(width / total, 4) for width in widths],
        # 表格实际宽度与左缩进（磅）：位置类条款（续表与表格左边线对齐）的判据。
        "width_pt": measured_width_pt,
        "indent_pt": _table_indent_pt(table),
        # W4：样式与表格对齐由解析层给出，写回层不再硬编码 Table Grid / 居中。
        "style": _table_style_name(table),
        "align": _table_alignment(table),
    }
    heights = []
    for row in rows:
        try:
            height = row.height
            heights.append(round(height.cm, 2) if height is not None else None)
        except Exception:
            heights.append(None)
    if any(heights):
        block["row_heights_cm"] = heights
    # 旧实现（P1/P2/P3 问题点）：
    #     for row_index, row in enumerate(rows):
    #         for col_index, cell in enumerate(row.cells):
    #             font_name = font_size = None
    #             if cell.paragraphs and cell.paragraphs[0].runs:
    #                 run = cell.paragraphs[0].runs[0]
    #                 ascii_font, east_asia = read_run_fonts(run, doc, theme_map)
    #                 font_name = ascii_font or east_asia
    #                 font_size = run.font.size.pt if run.font.size is not None else None
    #             block["cells"].append({
    #                 "row": row_index, "col": col_index, "text": cell.text or "",
    #                 "font_name": font_name, "font_size_pt": font_size,
    #             })
    # 问题：enumerate(row.cells) 会把 gridSpan 合并格重复返回 N 次、
    # col_index 错位；vMerge 续行格文本为空被当成空单元格；font_name
    # 用 ascii_font or east_asia 跨槽兜底，中英混排时永远显示西文字体。
    # 现在改为按 <w:tc> 网格迭代：以网格列游标推进、标注 gridSpan/vMerge，
    # 字体分 ascii（font_name）与 east_asia（east_asia_font）两个槽。
    for row_index, row in enumerate(rows):
        col_cursor = 0
        for tc in row._tr.findall(qn("w:tc")):
            grid_span = _tc_grid_span(tc)
            vmerge = _tc_vmerge(tc)
            cell = _Cell(tc, table)
            col = col_cursor
            col_cursor += grid_span
            font_name, east_asia_font, font_size_pt, run_infos = _cell_fonts(cell, doc, theme_map)
            entry = {
                "row": row_index, "col": col, "text": cell.text or "",
                "font_name": font_name, "east_asia_font": east_asia_font,
                "font_size_pt": font_size_pt, "runs": run_infos,
            }
            # 只记录显式设置的对齐：未设置时写回层保持段落默认，不虚构居中。
            align = read_explicit_alignment(cell.paragraphs[0], doc) if cell.paragraphs else None
            if align:
                entry["align"] = align
            if grid_span > 1:
                entry["grid_span"] = grid_span
            if vmerge:
                entry["vmerge"] = vmerge
            block["cells"].append(entry)
    return block
