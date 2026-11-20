# -*- coding: utf-8 -*-
"""页面设置（sections）：纸张、方向、页边距、页眉页脚（单位 cm）。"""
from docx.shared import Cm
from docx.enum.section import WD_ORIENT
from docx.oxml.ns import qn

from backend.docx_common.blocks import wrapper_containers

from ._defaults import DEFAULT_SECTION, pick as _pick  # noqa: F401  （DEFAULT_SECTION 供外部沿用）

# 常见纸张（纵向，宽 x 高，单位 cm）
PAPER_SIZES_CM = {
    "A4":     (21.0, 29.7),
    "A3":     (29.7, 42.0),
    "A5":     (14.8, 21.0),
    "B5":     (17.6, 25.0),
    "Letter": (21.59, 27.94),
    "Legal":  (21.59, 35.56),
}


def _val(cfg, key):
    """取分节配置项；缺省时回落到默认页面设置。"""
    return _pick(cfg, key, DEFAULT_SECTION[key])


def _header_footer_container(section, kind, hf_type):
    """取 section 的页眉/页脚容器。kind: 'headers'|'footers'；hf_type: default|first_page|even_page。"""
    if kind == "headers":
        if hf_type == "first_page":
            return section.first_page_header
        if hf_type == "even_page":
            return section.even_page_header
        return section.header
    if hf_type == "first_page":
        return section.first_page_footer
    if hf_type == "even_page":
        return section.even_page_footer
    return section.footer


def _clear_container(container):
    """清空页眉/页脚容器的段落与表格（保留容器本身与其中默认空段落）。

    包装元素（内容控件等）里的段落现在会进契约，清空时必须连包装一起摘掉：
    否则新写入的段落会与包装里的旧内容同时存在，页眉出现两份文字。
    """
    element = container._element
    for child in list(element):
        if child.tag in (qn("w:p"), qn("w:tbl")):
            element.remove(child)
        elif wrapper_containers(child):
            element.remove(child)


def apply_header_footer(section, entries, kind, doc=None):
    """W4：把 JSON 里的 headers/footers 条目写回 section 对应容器。

    entries 为解析层输出的条目列表，每个条目含 type / is_linked_to_previous /
    text / paragraphs / tables。先设首页/奇偶页标志再取容器（顺序不能反）。
    """
    from .op_passage import add_paragraph_to
    from .op_table import add_table_to

    hf_types = {e.get("type", "default") for e in entries}
    if "first_page" in hf_types:
        section.different_first_page_header_footer = True
    if "even_page" in hf_types:
        section.even_page_headers_footers = True

    for entry in entries:
        hf_type = entry.get("type", "default")
        if hf_type not in ("default", "first_page", "even_page"):
            hf_type = "default"
        container = _header_footer_container(section, kind, hf_type)
        try:
            container.is_linked_to_previous = False
        except Exception:
            pass
        _clear_container(container)
        for block in entry.get("paragraphs", []) or []:
            add_paragraph_to(container, block, doc=doc)
        for tbl_block in entry.get("tables", []) or []:
            try:
                add_table_to(container, tbl_block)
            except Exception:
                continue


def apply_section(section, config: dict, doc=None):
    """把单个 section 配置应用到 python-docx 的 section 对象。"""
    paper = _val(config, "paper_size")
    orientation = _val(config, "orientation")

    w_cm, h_cm = PAPER_SIZES_CM.get(paper, PAPER_SIZES_CM["A4"])
    if orientation == "landscape":
        w_cm, h_cm = h_cm, w_cm
        section.orientation = WD_ORIENT.LANDSCAPE
    else:
        section.orientation = WD_ORIENT.PORTRAIT

    section.page_width = Cm(w_cm)
    section.page_height = Cm(h_cm)

    section.left_margin   = Cm(_val(config, "left_cm"))
    section.right_margin  = Cm(_val(config, "right_cm"))
    section.top_margin    = Cm(_val(config, "top_cm"))
    section.bottom_margin = Cm(_val(config, "bottom_cm"))
    section.header_distance = Cm(_val(config, "header_cm"))
    section.footer_distance = Cm(_val(config, "footer_cm"))

    # W4：页眉页脚内容写回（解析层 P5 已输出 headers/footers）
    if doc is not None:
        apply_header_footer(section, config.get("headers") or [], "headers", doc=doc)
        apply_header_footer(section, config.get("footers") or [], "footers", doc=doc)


def apply_sections(doc, sections_config: list):
    """把 JSON 里所有 sections 依次应用到 doc；不够的自动追加。"""
    if not sections_config:
        return
    for i, cfg in enumerate(sections_config):
        sec = doc.sections[i] if i < len(doc.sections) else doc.add_section()
        apply_section(sec, cfg, doc=doc)
