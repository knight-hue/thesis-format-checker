# -*- coding: utf-8 -*-
"""新格式表格：只有 cells 数组，每个 cell 自带 row / col / text / 字体。"""
from docx.table import Table
from docx.shared import Pt, Cm
from docx.enum.table import WD_ROW_HEIGHT_RULE
from docx.oxml.ns import qn

from backend.docx_common.alignment import to_ooxml, to_ooxml_table
from backend.docx_common.runs import set_run_font

from ._defaults import DEFAULT_CELL, pick as _pick  # noqa: F401  （DEFAULT_CELL 供外部沿用）

# W4：正文可用宽度（英寸）。A4 高 29.7cm × 宽 21cm，按 rules 的
# 左 3cm / 右 2cm 页边距算，可用宽度 = 16cm ≈ 6.30 英寸。
# json_to_docx 会按真实分节参数传入，这里只作为兜底值。
DEFAULT_BODY_WIDTH_INCH = 6.3


def _grid_dimensions(table_config):
    """按 n_rows/n_cols 或 cells 推导网格尺寸（grid_span 计入列数）。"""
    cells = table_config.get("cells") or []
    n_rows = table_config.get("n_rows")
    n_cols = table_config.get("n_cols")
    if n_rows is not None and n_cols is not None:
        return int(n_rows), int(n_cols)
    if not cells:
        raise ValueError("table 至少需要一个 cell")
    n_rows = max(int(c["row"]) for c in cells) + 1
    n_cols = max(int(c["col"]) + int(c.get("grid_span", 1)) - 1 for c in cells) + 1
    return n_rows, n_cols


def _clear_cell_paragraph(paragraph):
    """W1：清掉段落里已有 run / 超链接，只保留 pPr。

    不要用 cell.text = ""（python-docx 会新建一个空 run，
    解析层读到它后字体回落默认值），直接移除 w:r / w:hyperlink。
    """
    for child in list(paragraph._p):
        if child.tag in (qn("w:r"), qn("w:hyperlink")):
            paragraph._p.remove(child)


def _apply_merges(table, cells):
    """W2：按解析层输出的 grid_span / vmerge 重建合并单元格。

    - grid_span > 1：水平合并 (r, c) ~ (r, c+span-1)；
    - vmerge == "restart"：向下与连续 vmerge == "continue" 的格子合并；
    - vmerge == "continue" 的格子不单独填内容（与 restart 属同一单元格）。
    先水平后垂直；交叉区域异常时跳过该格，不中断整表重建。
    """
    by_pos = {(int(c["row"]), int(c["col"])): c for c in cells}
    handled = set()
    for cell_cfg in cells:
        r = int(cell_cfg["row"])
        col = int(cell_cfg["col"])
        if cell_cfg.get("vmerge") == "continue":
            continue
        if (r, col) in handled:
            continue
        span = int(cell_cfg.get("grid_span", 1))
        end_col = col + span - 1
        end_row = r
        if cell_cfg.get("vmerge") == "restart":
            nxt = r + 1
            while (nxt, col) in by_pos and by_pos[(nxt, col)].get("vmerge") == "continue":
                end_row = nxt
                nxt += 1
        if end_row > r or end_col > col:
            try:
                table.cell(r, col).merge(table.cell(end_row, end_col))
            except Exception:
                pass
        handled.add((r, col))


def _set_column_widths(table, col_widths):
    """列宽写到 tblGrid 的 gridCol（单位 twips），合并后依然正确。"""
    tbl_grid = table._tbl.tblGrid
    if tbl_grid is None:
        return
    grid_cols = tbl_grid.findall(qn("w:gridCol"))
    for i, grid_col in enumerate(grid_cols):
        if i < len(col_widths):
            grid_col.set(qn("w:w"), str(int(round(col_widths[i] * 1440))))


def _body_width_inch(body_width_inch=None):
    """表格总宽度：优先用调用方给出的正文可用宽度，否则用 A4 兜底值。"""
    if body_width_inch and body_width_inch > 0:
        return float(body_width_inch)
    return DEFAULT_BODY_WIDTH_INCH


def _populate_table(table, table_config, body_width_inch=None):
    """把 table_config 填充到已建好的 Table（列宽/合并/内容/续页表头）。"""
    cells = table_config.get("cells") or []
    n_cols = _grid_dimensions(table_config)[1]

    # 1) 列宽（写 tblGrid，固定布局下 Word 以 gridCol 为准）
    # W4：总宽度取真实正文可用宽度，不再硬编码 6.0 英寸（比 A4 实际可用宽度窄）。
    total_width = _body_width_inch(body_width_inch)
    ratio = table_config.get("col_width_ratio")
    if ratio and len(ratio) == n_cols:
        col_widths = [total_width * r for r in ratio]
    else:
        col_widths = [total_width / n_cols] * n_cols
    _set_column_widths(table, col_widths)

    # 2) 行高
    row_heights_cm = table_config.get("row_heights_cm")
    for i, row in enumerate(table.rows):
        if row_heights_cm and i < len(row_heights_cm) and row_heights_cm[i]:
            row.height = Cm(row_heights_cm[i])
            row.height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST

    # 3) W2：先合并再填内容（合并后 table.cell(r, c) 会落到合并格）
    _apply_merges(table, cells)

    # 4) 填充单元格（W1：不写 cell.text，避免产生空 run）
    for cell_cfg in cells:
        if cell_cfg.get("vmerge") == "continue":
            continue  # 垂直合并续行格：内容在 restart 格，跳过
        r = int(cell_cfg["row"])
        c = int(cell_cfg["col"])
        text     = cell_cfg.get("text", "") or ""
        font     = _pick(cell_cfg, "font_name",      DEFAULT_CELL["font_name"])
        ea_font  = _pick(cell_cfg, "east_asia_font", DEFAULT_CELL["east_asia_font"])
        size     = _pick(cell_cfg, "font_size_pt",   DEFAULT_CELL["font_size_pt"])
        # W1：对齐只在解析层真的读到了 w:jc 时才写；未提供时保持段落原样，
        # 不再给每个单元格硬塞居中对齐。
        align    = cell_cfg.get("align")

        cell = table.cell(r, c)
        para = cell.paragraphs[0]
        _clear_cell_paragraph(para)
        ooxml_align = to_ooxml(align)
        if ooxml_align is not None:
            para.alignment = ooxml_align
        # 旧实现（W1 问题点）：这里还强制写入行距 1.5、段前/段后 2pt。
        # rules 对表格单元格行距没有任何规定，解析层也不记录这几项，
        # 强制写入等于凭空篡改原文档，故删除。

        # 旧实现（W1 问题点）：
        #     run = para.add_run(text)
        #     set_run_font(run, font, ea_font)
        #     run.font.size = Pt(size)
        # 只写整格单 run：多 run 混排（如“数量”Arial + “12”Times New Roman）
        # 会被压成聚合字体的一 run；且 cell.text="" 曾留下空 run。
        # 现在优先消费解析层 cell.runs（逐 run 分槽字体），空 run 跳过，
        # 无 runs 时回退整格单 run（兼容旧 JSON）。
        run_cfgs = [rc for rc in (cell_cfg.get("runs") or []) if (rc.get("text") or "")]
        if run_cfgs:
            for rc in run_cfgs:
                run = para.add_run(rc.get("text", "") or "")
                set_run_font(run,
                             _pick(rc, "font_name",      font),
                             _pick(rc, "east_asia_font", ea_font))
                run.font.size   = Pt(_pick(rc, "font_size_pt", size))
                # W2：解析层的 bold/italic 是布尔值（来自样式继承）；修复层新造
                # 的 run 可能是 None。None 表示“未指定”，此时不写 w:b/w:i，
                # 否则会凭空造出 bold=False（w:b w:val="0"）这样的显式关闭。
                if rc.get("bold") is not None:
                    run.font.bold = bool(rc.get("bold"))
                if rc.get("italic") is not None:
                    run.font.italic = bool(rc.get("italic"))
        elif text:
            run = para.add_run(text)
            set_run_font(run, font, ea_font)
            run.font.size = Pt(size)

    # 5) 续页表头：标记首行为重复标题行（<w:tblHeader/>），跨页时自动重复。
    if table_config.get("header_repeat"):
        from docx.oxml import OxmlElement

        tr_pr = table.rows[0]._tr.get_or_add_trPr()
        if tr_pr.find(qn("w:tblHeader")) is None:
            tr_pr.append(OxmlElement("w:tblHeader"))

    return table


def _apply_table_style(table, table_config):
    """应用表格样式：用解析层读到的样式名；缺失时退化为 Table Grid。

    W4：不再无条件套 Table Grid —— 原文档可能本来就没有表格样式
    （或用的是别的样式），硬编码会把样式换成带边框的网格。
    但当 JSON 里没有 style 字段（旧数据）时仍退回 Table Grid，
    保证重建出来的表格可见边框不丢。
    """
    style = table_config.get("style") if "style" in table_config else "Table Grid"
    if not style:
        return
    try:
        table.style = style
    except (KeyError, ValueError):
        # 目标模板里没有同名表格样式（重建用的是 python-docx 默认模板）
        try:
            table.style = "Table Grid"
        except (KeyError, ValueError):
            pass


def add_table(doc, table_config: dict, body_width_inch=None) -> Table:
    """生成表格：json_to_docx 的唯一表格入口。"""
    n_rows, n_cols = _grid_dimensions(table_config)
    table = doc.add_table(rows=n_rows, cols=n_cols)
    _apply_table_style(table, table_config)
    # W4：对齐只按解析层读到的 <w:tblPr>/<w:jc> 写；未设置时保持文档默认。
    table_align = to_ooxml_table(table_config.get("align"))
    if table_align is not None:
        table.alignment = table_align
    # 固定布局：JSON 里带着解析出来的 col_width_ratio，交给 Word 自动调整会
    # 重新分配列宽，反而偏离原文，故关闭 autofit。
    table.autofit = False
    return _populate_table(table, table_config, body_width_inch)


def add_table_to(container, table_config: dict, body_width_inch=None) -> Table:
    """W4：向页眉/页脚容器添加表格（python-docx 的 _Header/_Footer 有 add_table）。"""
    n_rows, n_cols = _grid_dimensions(table_config)
    table = container.add_table(rows=n_rows, cols=n_cols)
    _apply_table_style(table, table_config)
    table_align = to_ooxml_table(table_config.get("align"))
    if table_align is not None:
        table.alignment = table_align
    table.autofit = False
    return _populate_table(table, table_config, body_width_inch)
