# -*- coding: utf-8 -*-
"""段落内 run 流：按文档顺序展开直接子级 ``w:r`` 与超链接内的 ``w:r``。

python-docx 的 ``Paragraph.runs`` 只返回 ``w:p`` 的直接子级 ``w:r``，而
``CT_P.text``（即 ``Paragraph.text``）会把 ``w:hyperlink`` 内的文字算进来。
解析层与写回层各用一套口径时，run 下标会整体错位、超链接文字会被写两遍
或整段丢失，所以两边统一用本模块的迭代顺序。

行内包装（``w:sdt`` 内容控件、``w:ins`` 修订插入…）里的 run 两边都要算：
``CT_P.text`` 的 ``xpath("w:r | w:hyperlink")`` 不下钻，包在里面的文字会整段
消失——既不检查字体字号，也会在写回时被当成多余内容丢掉。包装白名单由
``docx_common/blocks.py`` 统一给出（它同时管块级包装），不在两个模块各写一份。

run 级字体/字距的写入也放在这里：解析层读字体、写回层写字体，口径必须同源
（原先 write_back/op_passage 与 write_back/op_table 各有一份 set_run_font，
字距换算也分成了 int 截断与 round 两种写法）。
"""
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.run import Run

from .blocks import wrapper_containers

# ``w:spacing`` 的单位是 twips，1pt = 20twips。
TWIPS_PER_POINT = 20


def _iter_entry_elements(element, hyperlink):
    """递归产出 ``(w:r 元素, 所属超链接下标或 None)``。

    ``hyperlink`` 是长度 1 的列表（当前超链接下标），递归时按引用共享，
    保证跨行内包装展开时下标仍然连续。
    """
    for child in element:
        if child.tag == qn("w:r"):
            yield child, None
        elif child.tag == qn("w:hyperlink"):
            hyperlink[0] += 1
            index = hyperlink[0]
            for run_el in child.findall(qn("w:r")):
                yield run_el, index
        else:
            for inner in wrapper_containers(child):
                yield from _iter_entry_elements(inner, hyperlink)


def iter_run_entries(p_element):
    """依次产出 ``(w:r 元素, 所属超链接下标或 None)``。

    顺序与 ``CT_P.text`` 的拼接顺序一致：直接 ``w:r``、``w:hyperlink`` 内的
    ``w:r``，以及行内包装（``w:sdt``／``w:ins``…）里的 ``w:r`` 都按它们在
    段落中出现的先后展开。删除修订（``w:del``）里的 run 不算——那些文字
    文档里看不到，不是本段内容。
    """
    return _iter_entry_elements(p_element, [-1])


def iter_runs(paragraph):
    """依次产出 ``(Run, 所属超链接下标或 None)``，parent 统一为段落实例。"""
    for run_el, hyperlink_index in iter_run_entries(paragraph._p):
        yield Run(run_el, paragraph), hyperlink_index


def set_run_font(run, ascii_font: str, east_asia_font: str):
    """同时写 ascii/hAnsi 与 eastAsia：只设 ``run.font.name`` 影响不到中文字形。"""
    run.font.name = ascii_font
    r_pr = run._element.get_or_add_rPr()
    r_fonts = r_pr.get_or_add_rFonts()
    r_fonts.set(qn("w:ascii"), ascii_font)
    r_fonts.set(qn("w:hAnsi"), ascii_font)
    r_fonts.set(qn("w:eastAsia"), east_asia_font)


def set_char_spacing(run, pt):
    """设置字距（``w:spacing``）；``None`` 与 0 都表示"取消字距"。

    ``w:rPr`` 里 ``w:spacing`` 只允许出现一次，原文档或历史版本可能留下重复项，
    因此先把多余的删掉；但**保留第一个元素的位置**，避免把子元素顺序改乱
    （``w:spacing`` 应在 ``w:color`` 之后、``w:sz`` 之前）。
    按 ``round`` 换算 twips 而非截断：``pt * 20`` 受浮点误差影响可能略小于
    整数（如 1.65 * 20），截断会凭空丢掉 1twips。
    """
    r_pr = run._element.get_or_add_rPr()
    existing = r_pr.findall(qn("w:spacing"))
    if not pt:
        for element in existing:
            r_pr.remove(element)
        return
    if existing:
        spacing = existing[0]
        for duplicate in existing[1:]:
            r_pr.remove(duplicate)
    else:
        spacing = OxmlElement("w:spacing")
        r_pr.append(spacing)
    spacing.set(qn("w:val"), str(round(pt * TWIPS_PER_POINT)))
