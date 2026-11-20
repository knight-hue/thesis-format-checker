# -*- coding: utf-8 -*-

"""表格级原位写回：样式/对齐、单元格文字与字体、标题行重复。"""

from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from backend.docx_common.alignment import to_ooxml, to_ooxml_table
from backend.docx_common.runs import iter_run_entries

from .diff import value_changed
from .keys import CELL_KEYS, CELL_STRUCTURAL, TABLE_KEYS
from .runs import patch_run, patch_run_list
from .text import rewrite_paragraph_text


def apply_table_attribute(table, key, value, report, where):
    """表格样式/对齐（W4 新增契约字段）就地写回（W7）。"""
    try:
        if key == "align":
            # 表格级 w:jc 只有 left/center/right 是合法取值，段落对齐多出的
            # justify / distribute 不能混用（写进去 Word 会判为无效值）。
            if value is None:
                table.alignment = None
            else:
                table_align = to_ooxml_table(value)
                if table_align is None:
                    return report.skip(f"表格对齐 {value} 不是合法表格对齐，已跳过", where)
                table.alignment = table_align
        elif key == "style":
            if value is None:
                tbl_pr = table._tbl.tblPr
                style = tbl_pr.find(qn("w:tblStyle")) if tbl_pr is not None else None
                if style is not None:
                    tbl_pr.remove(style)
            else:
                table.style = value
        else:
            return report.skip(f"表格字段 {key} 暂不支持原位写回", where)
    except Exception as exc:
        return report.skip(f"表格 {key} 无法应用到原 DOCX：{exc}", where)
    return True


def cell_entries(cell):
    """单元格内全部 run 元素，顺序与解析层聚合的 run 明细一致（逐段落展开）。"""
    return [
        element
        for paragraph in cell.paragraphs
        for element, _hyperlink in iter_run_entries(paragraph._p)
    ]


def patch_cell(table, before, after, report, where):
    """单元格文字/字体写回（W7）：文字按段落最小改动改写，不再是“一律拒绝”。"""
    applied = False
    for key in before.keys() | after.keys():
        if key in CELL_KEYS or not value_changed(before, after, key):
            continue
        report.skip(f"单元格字段 {key} 暂不支持原位写回", where)
    for key in CELL_STRUCTURAL:
        if value_changed(before, after, key):
            report.skip(f"单元格 {key} 属于合并结构信息，原位写回不动", where)
    if value_changed(before, after, "align"):
        ooxml_align = to_ooxml(after.get("align"))
        for paragraph in table.cell(after["row"], after["col"]).paragraphs:
            paragraph.alignment = ooxml_align
        applied = True

    cell = table.cell(after["row"], after["col"])
    # 修复层给出的单元格字体是整格聚合值（不逐 run 展开），沿用“整格套用”语义
    for key in ("font_name", "east_asia_font", "font_size_pt"):
        if not value_changed(before, after, key):
            continue
        applied = True
        for paragraph in cell.paragraphs:
            for run in paragraph.runs:
                patch_run(run, before, after, report, where)
    if value_changed(before, after, "text"):
        old_lines = (before.get("text") or "").split("\n")
        new_lines = (after.get("text") or "").split("\n")
        paragraphs = cell.paragraphs
        if len(old_lines) != len(paragraphs) or len(new_lines) != len(old_lines):
            report.skip("单元格段落结构变化无法安全原位改写", where)
        else:
            for paragraph, old_line, new_line in zip(paragraphs, old_lines, new_lines):
                applied |= rewrite_paragraph_text(paragraph, old_line, new_line, report, where)
    applied |= patch_run_list(
        cell_entries(cell), before.get("runs") or [], after.get("runs") or [], report, where,
    )
    return applied


def patch_table(table, old, new, report, where):
    applied = False
    for key in old.keys() | new.keys():
        if key in TABLE_KEYS or not value_changed(old, new, key):
            continue
        report.skip(f"表格字段 {key} 暂不支持原位写回", where)
    for key in ("style", "align"):
        if value_changed(old, new, key):
            applied |= apply_table_attribute(table, key, new.get(key), report, where)

    old_cells, new_cells = old.get("cells") or [], new.get("cells") or []
    if len(old_cells) != len(new_cells):
        report.skip("表格单元格结构变化无法安全原位写回", where)
    else:
        for before, after in zip(old_cells, new_cells):
            if (before.get("row"), before.get("col")) != (after.get("row"), after.get("col")):
                report.skip("表格单元格定位变化无法安全写回", where)
                continue
            applied |= patch_cell(
                table, before, after, report, f"{where} 单元格({after.get('row')},{after.get('col')})",
            )
    if value_changed(old, new, "header_repeat"):
        trpr = table.rows[0]._tr.get_or_add_trPr()
        header = trpr.find(qn("w:tblHeader"))
        if new.get("header_repeat") and header is None:
            trpr.append(OxmlElement("w:tblHeader"))
        elif not new.get("header_repeat") and header is not None:
            trpr.remove(header)
        applied = True
    return applied
