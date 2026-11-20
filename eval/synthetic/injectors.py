# -*- coding: utf-8 -*-
"""错误注入器注册表：每个注入器对合规基线做"单点破坏"并登记金标准。

命名约定：注入器名 = 金标准 rule_id 的扁平化（点 → 下划线），便于报告对照。
每个注入器返回 list[GoldenIssue]（通常 1 条）。
"""
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt

from ..golden import GoldenIssue

INJECTORS: dict = {}


def injector(name):
    def deco(fn):
        INJECTORS[name] = fn
        return fn
    return deco


def _loc(type_, text_find=None, kind=None, run=None, row=None, col=None, index=None):
    loc = {"type": type_}
    if index is not None:
        loc["index"] = index
    if run is not None:
        loc["run"] = run
    if row is not None:
        loc["row"] = row
    if col is not None:
        loc["col"] = col
    meta = {"text_find": text_find, "kind": kind}
    return loc, meta


def _issue(rule_id, loc, meta, severity="error", note="", text_anchor=None):
    return GoldenIssue(rule_id=rule_id, location=loc, severity=severity, note=note,
                       text_anchor=text_anchor, **meta)


# ====================== 页面设置 ======================
@injector("page_left_cm")
def page_left_cm(handles):
    handles["section"].left_margin = __import__("docx.shared", fromlist=["Cm"]).Cm(2)
    loc, meta = _loc("section", text_find=None, index=0)
    return [_issue("page.left_cm", loc, meta, note="左页边距 3cm → 2cm")]


# ====================== 正文 ======================
@injector("body_font_size")
def body_font_size(handles):
    run = handles["body_1"].runs[0]
    run.font.size = Pt(9)
    loc, meta = _loc("paragraph", text_find="近年来", kind="body", run=0)
    return [_issue("body.font_size", loc, meta, note="正文字号 12pt → 9pt")]


@injector("body_east_asia_font")
def body_east_asia_font(handles):
    run = handles["body_2"].runs[0]
    rpr = run._element.get_or_add_rPr()
    rpr.get_or_add_rFonts().set(qn("w:eastAsia"), "楷体")
    loc, meta = _loc("paragraph", text_find="研究背景主要围绕", kind="body", run=0)
    return [_issue("body.east_asia_font", loc, meta, note="正文中文字体 宋体 → 楷体")]


@injector("body_latin_font")
def body_latin_font(handles):
    # 该段需含拉丁字符（end-to-end），否则 latin 字体检查不触发
    handles["body_3"].runs[0].text = ("深度学习方法通过端到端训练（end-to-end）"
                                      "在多个任务上取得了良好效果。")
    handles["body_3"].runs[0].font.name = "Arial"
    loc, meta = _loc("paragraph", text_find="深度学习方法通过", kind="body", run=0)
    return [_issue("body.latin_font", loc, meta, note="正文西文字体 Times New Roman → Arial")]


@injector("body_char_spacing")
def body_char_spacing(handles):
    run = handles["body_4"].runs[0]
    rpr = run._element.get_or_add_rPr()
    # w:spacing 的 val 单位是 1/20 pt；40 → 2pt，超出规则容差 0.5pt
    spacing = rpr.makeelement(qn("w:spacing"), {qn("w:val"): "40"})
    rpr.append(spacing)
    loc, meta = _loc("paragraph", text_find="相关工作包括", kind="body", run=0)
    return [_issue("body.char_spacing", loc, meta, severity="warning",
                   note="正文字符间距 0 → 2pt")]


@injector("body_line_spacing")
def body_line_spacing(handles):
    handles["body_5"].paragraph_format.line_spacing = 1.0
    loc, meta = _loc("paragraph", text_find="系统整体架构如图2-1所示", kind="body")
    return [_issue("body.line_spacing", loc, meta, note="正文行距 1.5 倍 → 1.0 倍")]


# ====================== 题目 ======================
@injector("title_font_size")
def title_font_size(handles):
    handles["title"].runs[0].font.size = Pt(12)
    loc, meta = _loc("paragraph", text_find="基于深度学习的论文格式校验研究", kind="title", run=0)
    return [_issue("title.font_size", loc, meta, note="中文题目 15pt → 12pt")]


@injector("title_font")
def title_font(handles):
    run = handles["title"].runs[0]
    rpr = run._element.get_or_add_rPr()
    rpr.get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")
    loc, meta = _loc("paragraph", text_find="基于深度学习的论文格式校验研究", kind="title", run=0)
    return [_issue("title.font", loc, meta, note="中文题目 黑体 → 宋体")]


@injector("title_alignment")
def title_alignment(handles):
    # 注意：题目改为左对齐后，识别层会把该段降级为 cover_info，title.alignment
    # 检查不再触发——"识别依赖居中"使该规则无法用合成单点注入干净覆盖，
    # 因此本注入器只保留说明，不参与评测（见 eval/README 局限一节）。
    return []


# ====================== 摘要与关键词 ======================
@injector("abstract_title_text")
def abstract_title_text(handles):
    handles["abstract_title"].runs[0].text = "摘 要"
    loc, meta = _loc("paragraph", text_find="摘 要", kind="abstract_title")
    return [_issue("abstract.title_text", loc, meta, note="摘要标题 摘要 → 摘 要")]


@injector("abstract_body_font_size")
def abstract_body_font_size(handles):
    handles["abstract_body"].runs[0].font.size = Pt(10.5)
    loc, meta = _loc("paragraph", text_find="论文格式校验系统将", kind="abstract_body", run=0)
    return [_issue("abstract.body.font_size", loc, meta, note="摘要正文 12pt → 10.5pt")]


@injector("keyword_title_label")
def keyword_title_label(handles):
    handles["keyword_title"].runs[0].text = "关键词："
    loc, meta = _loc("paragraph", text_find="关键词：", kind="keyword_title")
    return [_issue("keyword.title_label", loc, meta, note="关键词标签 关键字： → 关键词：")]


@injector("keyword_count")
def keyword_count(handles):
    handles["keyword_body"].runs[0].text = "格式校验，文档解析"
    loc, meta = _loc("paragraph", text_find="格式校验，文档解析", kind="keyword_body")
    return [_issue("keyword.count", loc, meta, note="关键词 4 个 → 2 个")]


@injector("keyword_terminal_punct")
def keyword_terminal_punct(handles):
    handles["keyword_body"].runs[0].text = "格式校验，文档解析，契约重建，DOCX。"
    loc, meta = _loc("paragraph", text_find="格式校验，文档解析，契约重建，DOCX。", kind="keyword_body")
    return [_issue("keyword.terminal_punct", loc, meta, note="关键词末尾加了句号")]


@injector("keyword_body_font")
def keyword_body_font(handles):
    run = handles["keyword_body"].runs[0]
    rpr = run._element.get_or_add_rPr()
    rpr.get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")
    loc, meta = _loc("paragraph", text_find="格式校验，文档解析", kind="keyword_body", run=0)
    return [_issue("keyword.body.font", loc, meta, note="关键词内容 黑体 → 宋体")]


@injector("en_abstract_body_font_size")
def en_abstract_body_font_size(handles):
    handles["en_abstract_body"].runs[0].font.size = Pt(9)
    loc, meta = _loc("paragraph", text_find="This system parses DOCX", kind="en_abstract_body", run=0)
    return [_issue("en_abstract.body.font_size", loc, meta, note="英文摘要正文 12pt → 9pt")]


@injector("en_keyword_title_label")
def en_keyword_title_label(handles):
    handles["en_keyword_title"].runs[0].text = "Keywords:"
    loc, meta = _loc("paragraph", text_find="Keywords:", kind="en_keyword_title")
    return [_issue("en_keyword.title_label", loc, meta, note="Keywords 冒号 全角 → 半角")]


# ====================== 标题与编号 ======================
@injector("heading1_font_size")
def heading1_font_size(handles):
    handles["h1_1"].runs[0].font.size = Pt(14)
    loc, meta = _loc("paragraph", text_find="第1章 绪论", kind="heading_1", run=0)
    return [_issue("heading.heading_1.font_size", loc, meta, note="一级标题 16pt → 14pt")]


@injector("heading1_alignment")
def heading1_alignment(handles):
    handles["h1_1"].alignment = WD_ALIGN_PARAGRAPH.LEFT
    loc, meta = _loc("paragraph", text_find="第1章 绪论", kind="heading_1")
    return [_issue("heading.heading_1.alignment", loc, meta, note="一级标题 居中 → 居左")]


# ====================== 图与图题 ======================
@injector("fig_caption_font_size")
def fig_caption_font_size(handles):
    handles["fig_caption"].runs[0].font.size = Pt(12)
    loc, meta = _loc("paragraph", text_find="图2-1 系统架构图", kind="figure_caption", run=0)
    return [_issue("figure.caption.font_size", loc, meta, note="图题 10.5pt → 12pt")]


@injector("fig_caption_position")
def fig_caption_position(handles):
    """图题移到图片上方：figure.caption_position 应报错。"""
    handles["image_para"]._p.addprevious(handles["fig_caption"]._p)
    loc, meta = _loc("paragraph", text_find="图2-1 系统架构图", kind="figure_caption")
    return [_issue("figure.caption_position", loc, meta, note="图题放在图片上方")]


@injector("fig_caption_space")
def fig_caption_space(handles):
    handles["fig_caption"].runs[0].text = "图2-1系统架构图"
    loc, meta = _loc("paragraph", text_find="图2-1系统架构图", kind="figure_caption")
    return [_issue("caption.number_space", loc, meta, severity="warning",
                   note="图编号与题名之间无空格")]


# ====================== 表与表题 ======================
@injector("table_caption_font_size")
def table_caption_font_size(handles):
    handles["table_caption"].runs[0].font.size = Pt(12)
    loc, meta = _loc("paragraph", text_find="表2-1 实验环境配置", kind="table_caption", run=0)
    return [_issue("table.caption.font_size", loc, meta, note="表题 10.5pt → 12pt")]


def _replace_cell_run(cell, text, *, size=None, east_asia=None):
    """把单元格内容替换为单个 run（清掉 python-docx 留下的空 run），
    保证注入落在非空 run 上，检测层按有文字的 run 检查才能触发。"""
    para = cell.paragraphs[0]
    for r in list(para.runs):
        r._element.getparent().remove(r._element)
    run = para.add_run(text)
    from .baseline import set_font
    set_font(run, name="Times New Roman", east_asia=east_asia or "宋体",
             size=size if size is not None else 10.5)
    return run


@injector("table_cell_font_size")
def table_cell_font_size(handles):
    _replace_cell_run(handles["table"].cell(0, 0), "指标", size=8)
    loc, meta = _loc("table_cell", text_find="指标", kind="table", row=0, col=0)
    return [_issue("table.cell_font_size", loc, meta, note="表内字号 10.5pt → 8pt")]


@injector("table_cell_east_asia_font")
def table_cell_east_asia_font(handles):
    _replace_cell_run(handles["table"].cell(0, 1), "数值", east_asia="楷体")
    loc, meta = _loc("table_cell", text_find="数值", kind="table", row=0, col=1)
    return [_issue("table.cell_east_asia_font", loc, meta, note="表内中文字体 宋体 → 楷体")]


@injector("table_caption_position")
def table_caption_position(handles):
    """表题移到表格下方：table.caption_position 应报错。"""
    handles["table"]._tbl.addnext(handles["table_caption"]._p)
    loc, meta = _loc("paragraph", text_find="表2-1 实验环境配置", kind="table_caption")
    return [_issue("table.caption_position", loc, meta, note="表题放在表格下方")]


@injector("table_caption_number")
def table_caption_number(handles):
    handles["table_caption"].runs[0].text = "表2-2 实验环境配置"
    loc, meta = _loc("paragraph", text_find="表2-2 实验环境配置", kind="table_caption")
    return [_issue("table.caption_number", loc, meta, note="表题编号 2-1 → 2-2（跳号）")]


# ====================== 参考文献 ======================
@injector("reference_title_text")
def reference_title_text(handles):
    handles["reference_title"].runs[0].text = "参考文 献"
    loc, meta = _loc("paragraph", text_find="参考文 献", kind="reference_title")
    return [_issue("reference.title_text", loc, meta, note="参考文献标题加空格")]


@injector("ref_entry_font_size")
def ref_entry_font_size(handles):
    handles["ref_entry_1"].runs[0].font.size = Pt(9)
    loc, meta = _loc("paragraph", text_find="［1］张三", kind="reference_entry", run=0)
    return [_issue("reference.entry_font_size", loc, meta, note="参考文献条目 10.5pt → 9pt")]


@injector("ref_entry_period")
def ref_entry_period(handles):
    handles["ref_entry_2"].runs[0].text = "［2］Smith J．Neural Network Methods[M]．New York：Springer，2023：1-100"
    loc, meta = _loc("paragraph", text_find="［2］Smith J", kind="reference_entry")
    return [_issue("reference.entry_period", loc, meta, note="参考文献条目结尾无句点")]


# ====================== 致谢 ======================
@injector("ack_title_text")
def ack_title_text(handles):
    handles["ack_title"].runs[0].text = "致谢"
    loc, meta = _loc("paragraph", text_find="致谢", kind="acknowledgement_title")
    return [_issue("acknowledgement.title_text", loc, meta,
                   note="致谢标题 致    谢 → 致谢")]


@injector("ack_body_font_size")
def ack_body_font_size(handles):
    handles["ack_body"].runs[0].font.size = Pt(9)
    loc, meta = _loc("paragraph", text_find="感谢导师", kind="acknowledgement_body", run=0)
    return [_issue("ack.body_font_size", loc, meta, note="致谢正文 12pt → 9pt")]


# ====================== 语义确定性条款（零 LLM） ======================
@injector("ack_content_time")
def ack_content_time(handles):
    handles["ack_body"].runs[0].text = "感谢导师在2024年3月至2024年6月期间的指导。"
    loc, meta = _loc("paragraph", text_find="感谢导师在2024年3月至", kind="acknowledgement_body")
    return [_issue("acknowledgement.content", loc, meta, severity="warning",
                   text_anchor="2024年3月至", note="致谢出现时间段表述")]


@injector("ref_format_missing_mark")
def ref_format_missing_mark(handles):
    handles["ref_entry_2"].runs[0].text = ("［2］Smith J．Neural Network Methods．"
                                           "New York：Springer，2023：1-100．")
    loc, meta = _loc("paragraph", text_find="Neural Network Methods", kind="reference_entry")
    return [_issue("reference.format", loc, meta, severity="warning",
                   text_anchor="Neural Network Methods", note="参考文献条目缺文献类型标识")]


@injector("units_kg")
def units_kg(handles):
    handles["body_6"].runs[0].text = "实验环境配置如表2-1所示，设备重约2公斤。"
    loc, meta = _loc("paragraph", text_find="设备重约2公斤", kind="body")
    return [_issue("units", loc, meta, severity="warning",
                   text_anchor="公斤", note="计量单位使用俗称 公斤")]


@injector("numerals_hanzi")
def numerals_hanzi(handles):
    handles["body_7"].runs[0].text = "本文验证了该系统的有效性与可靠性，训练耗时约两小时。"
    loc, meta = _loc("paragraph", text_find="训练耗时约两小时", kind="body")
    return [_issue("numerals", loc, meta, severity="warning",
                   text_anchor="两小时", note="测量统计使用汉字数字 两小时")]


def injector_names():
    """注入器名列表（= 样张名），按注册顺序。"""
    return sorted(INJECTORS.keys())
