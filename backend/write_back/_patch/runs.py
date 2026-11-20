# -*- coding: utf-8 -*-

"""run 级格式写回：逐项应用字体/字号/粗斜/字距，单项失败只告警。"""

from docx.oxml.ns import qn
from docx.shared import Pt
from docx.text.run import Run

from backend.docx_common.runs import set_char_spacing

from .diff import value_changed
from .keys import RUN_FORMAT, RUN_IGNORED, RUN_READONLY


def patch_run(run, old, new, report, where):
    """逐项应用 run 级格式；单项写入失败只告警（W5：不再让整次写回失败）。"""
    try:
        if value_changed(old, new, "font_name"):
            run.font.name = new.get("font_name")
        if value_changed(old, new, "east_asia_font"):
            rfonts = run._r.get_or_add_rPr().get_or_add_rFonts()
            value = new.get("east_asia_font")
            if value is None:
                rfonts.attrib.pop(qn("w:eastAsia"), None)
            else:
                rfonts.set(qn("w:eastAsia"), value)
        if value_changed(old, new, "font_size_pt"):
            run.font.size = Pt(new["font_size_pt"]) if new.get("font_size_pt") is not None else None
        if value_changed(old, new, "bold"):
            run.font.bold = new.get("bold")
        if value_changed(old, new, "italic"):
            run.font.italic = new.get("italic")
        if value_changed(old, new, "char_spacing_pt"):
            set_char_spacing(run, new.get("char_spacing_pt"))
    except Exception as exc:
        return report.skip(f"run 格式写入失败：{exc}", where)
    return True


def patch_run_list(entries, old_runs, new_runs, report, where):
    """按 run 顺序应用格式改动（段落与单元格共用，W7）。"""
    if not old_runs and not new_runs:
        # 解析层对空段落/空单元格不输出 run 明细，run 级格式无从对齐
        return False
    if len(old_runs) != len(new_runs):
        return report.skip("run 数量变化无法安全定位写回", where)
    if len(entries) != len(new_runs):
        return report.skip(
            f"原 DOCX run 数量（{len(entries)}）与解析契约（{len(new_runs)}）不一致", where,
        )
    applied = False
    for element, old_run, new_run in zip(entries, old_runs, new_runs):
        for key in old_run.keys() | new_run.keys():
            if not value_changed(old_run, new_run, key) or key in RUN_IGNORED:
                continue
            if key in RUN_READONLY:
                report.skip(f"run 字段 {key} 为解析信息，无法写回", where)
            elif key not in RUN_FORMAT | {"text"}:
                report.skip(f"run 字段 {key} 暂不支持原位写回", where)
        applied |= patch_run(Run(element, None), old_run, new_run, report, where)
    return applied
