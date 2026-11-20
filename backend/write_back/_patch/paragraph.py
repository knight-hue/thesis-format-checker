# -*- coding: utf-8 -*-

"""段落级原位写回：文字 + 段格式 + 逐个 run，三者都走最小改动。"""

from docx.enum.text import WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.shared import Pt
from docx.text.run import Run

from backend.docx_common.alignment import to_ooxml
from backend.docx_common.runs import iter_run_entries, set_char_spacing

from .diff import value_changed
from .keys import PARA_FORMAT, PARA_KEYS
from .runs import patch_run_list
from .text import run_writable, rewrite_paragraph_text


def apply_para_format(paragraph, key, value, new):
    pf = paragraph.paragraph_format
    if key == "alignment":
        paragraph.alignment = to_ooxml(value)
    elif key == "first_line_indent_pt":
        pf.first_line_indent = Pt(value) if value is not None else None
    elif key == "space_before_pt":
        pf.space_before = Pt(value) if value is not None else None
    elif key == "space_after_pt":
        pf.space_after = Pt(value) if value is not None else None
    elif key == "line_spacing_pt" and value is not None:
        pf.line_spacing = Pt(value)
        pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    elif key == "line_spacing_multiple" and value is not None:
        pf.line_spacing = float(value)
    elif key in ("line_spacing_pt", "line_spacing_multiple") and (
        new.get("line_spacing_pt") is None and new.get("line_spacing_multiple") is None
    ):
        pf.line_spacing = None
    else:
        return False
    return True


def _rebuild_paragraph_runs(paragraph, new_runs, report, where):
    """按解析契约重建段落 run（文字已整段改写后使用）。

    仅当段落没有超链接、所有 run 都只含 rPr/w:t 且重建后文本与当前段落一致时启用；
    否则返回 None 交回逐 run 对齐逻辑，宁可保留原格式也不破坏域/图/批注。
    重建使语义改写新产生的数字/西文能带上契约里指定的西文字体（如 Times New Roman）。
    """
    entries = list(iter_run_entries(paragraph._p))
    if not entries or any(hyperlink for _element, hyperlink in entries):
        return None
    elements = [element for element, _hyperlink in entries]
    if not all(run_writable(element) for element in elements):
        return None
    if "".join(Run(element, None).text for element in elements) != "".join(
            run.get("text") or "" for run in new_runs):
        return None
    for element in elements:
        paragraph._p.remove(element)
    for config in new_runs:
        text = config.get("text") or ""
        if not text:
            continue
        run = paragraph.add_run(text)
        if config.get("font_name"):
            run.font.name = config["font_name"]
        east_asia = config.get("east_asia_font")
        if east_asia:
            run._r.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), east_asia)
        if config.get("font_size_pt") is not None:
            run.font.size = Pt(config["font_size_pt"])
        if config.get("bold") is not None:
            run.font.bold = config["bold"]
        if config.get("italic") is not None:
            run.font.italic = config["italic"]
        if config.get("char_spacing_pt") is not None:
            set_char_spacing(run, config["char_spacing_pt"])
    return True


def patch_paragraph_runs(paragraph, old, new, report, where):
    entries = [element for element, _hyperlink in iter_run_entries(paragraph._p)]
    old_runs = old.get("runs") or []
    new_runs = new.get("runs") or []
    if len(old_runs) != len(new_runs):
        if value_changed(old, new, "text"):
            # 整段改写后 run 边界可能变化（语义改写按字符类别拆 run 保留西文字体）：
            # 安全时按新契约重建 run，避免字体信息被丢弃。
            rebuilt = _rebuild_paragraph_runs(paragraph, new_runs, report, where)
            if rebuilt is not None:
                return rebuilt
        if old_runs and len(new_runs) == 1 and len(old_runs) == len(entries):
            # 整段改写后解析层只报告 1 个 run：其余 run 沿用原格式
            new_runs = new_runs + old_runs[1:]
        else:
            return report.skip("段落 run 数量变化无法安全定位写回", where)
    return patch_run_list(entries, old_runs, new_runs, report, where)


def patch_paragraph(paragraph, old, new, report, where):
    """段落级原位写回：文字按 run 最小改动改写，格式逐项应用（W5）。"""
    applied = False
    for key in old.keys() | new.keys():
        if key in PARA_KEYS or key in PARA_FORMAT or not value_changed(old, new, key):
            continue
        report.skip(f"段落字段 {key} 暂不支持原位写回", where)
    if value_changed(old, new, "formula_xml"):
        report.skip("公式 XML 改动暂不支持原位写回", where)

    old_text = old.get("text") or ""
    new_text = new.get("text") or ""
    runs_text = "".join(run.get("text") or "" for run in (new.get("runs") or []))
    if value_changed(old, new, "text"):
        applied |= rewrite_paragraph_text(paragraph, old_text, new_text, report, where)
    elif runs_text and runs_text != new_text:
        # 解析层只改了 run 明细、没同步段落文字：以 run 明细为准
        applied |= rewrite_paragraph_text(paragraph, old_text, runs_text, report, where)

    for key in PARA_FORMAT:
        if value_changed(old, new, key) and apply_para_format(paragraph, key, new.get(key), new):
            applied = True
    applied |= patch_paragraph_runs(paragraph, old, new, report, where)
    return applied
