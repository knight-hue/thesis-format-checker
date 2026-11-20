# -*- coding: utf-8 -*-
"""对齐取值的唯一换算处：契约字面量 ↔ OOXML ``w:jc`` ↔ python-docx 枚举。

解析层从 ``w:jc`` 读、写回层往枚举写，两边必须共用同一套取值表；各写一份就会
出现"解析认识 distribute、写回只列出三种"这类静默丢值（表格级 ``w:jc`` 与
段落级 ``w:jc`` 的取值域本来就不同，尤其容易被混用）。

契约字面量本身定义在 document_contract.ALIGNMENT，本模块从那里派生，不重复定义。
段落级取值域是 ``VALID``，表格级（``w:tblPr/w:jc``）是 ``VALID_TABLE``，两者不通用：
把段落多出的 ``justify`` / ``distribute`` 写到表格上，Word 会直接判定文档损坏。
"""
from typing import get_args

from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH

from backend.document_contract import ALIGNMENT

# 契约允许的全部取值，由 ALIGNMENT 派生，避免两处清单漂移。
VALID = frozenset(get_args(ALIGNMENT))

# 契约值 → python-docx 段落对齐枚举。
TO_OOXML = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
    "distribute": WD_ALIGN_PARAGRAPH.DISTRIBUTE,
}

# 表格级对齐（w:tblPr/w:jc）只认 Word 支持的三种，段落多出的取值不能混用。
# 键集即解析层认的合法值域，故一并派生 VALID_TABLE，避免解析/写回各写一份清单。
TO_OOXML_TABLE = {
    "left": WD_TABLE_ALIGNMENT.LEFT,
    "center": WD_TABLE_ALIGNMENT.CENTER,
    "right": WD_TABLE_ALIGNMENT.RIGHT,
}

VALID_TABLE = frozenset(TO_OOXML_TABLE)

# python-docx 枚举 → 契约值。kashida / thaiDistribute 等没有独立语义，
# 一律归入 justify，避免产出契约校验不通过的 alignment。
FROM_OOXML = {
    WD_ALIGN_PARAGRAPH.LEFT: "left",
    WD_ALIGN_PARAGRAPH.CENTER: "center",
    WD_ALIGN_PARAGRAPH.RIGHT: "right",
    WD_ALIGN_PARAGRAPH.JUSTIFY: "justify",
    WD_ALIGN_PARAGRAPH.DISTRIBUTE: "distribute",
    WD_ALIGN_PARAGRAPH.JUSTIFY_MED: "justify",
    WD_ALIGN_PARAGRAPH.JUSTIFY_HI: "justify",
    WD_ALIGN_PARAGRAPH.JUSTIFY_LOW: "justify",
    WD_ALIGN_PARAGRAPH.THAI_JUSTIFY: "justify",
}

# OOXML 的 start/end 是逻辑左右对齐，python-docx 的 from_xml 不认，这里补上。
RAW_JC_ALIASES = {"start": "left", "end": "right"}


class UnsupportedAlignment(ValueError):
    """``w:jc`` 有值但契约不认识，调用方据此终止样式链回退。"""


def to_ooxml(value, default=None):
    """契约值 → 段落对齐枚举；取值未知时返回 ``default``。"""
    return TO_OOXML.get(value, default)


def to_ooxml_table(value):
    """契约值 → 表格对齐枚举；非 left/center/right 返回 None，由调用方跳过并告警。"""
    return TO_OOXML_TABLE.get(value)


def from_jc(value):
    """``w:jc`` 的 ``w:val`` → 契约值。

    - 缺省或空串：返回 None，表示"本层没规定"，调用方继续沿样式链向上回退。
    - 有值且契约认识：返回契约值。
    - 有值但契约不认识：抛 UnsupportedAlignment。此时必须**停止**回退——原文档
      显式写了 Word 认、契约不认的对齐，继续向上继承只会给出错误结论。
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        align = WD_ALIGN_PARAGRAPH.from_xml(text)
    except Exception:
        align = None
    mapped = FROM_OOXML.get(align) if align is not None else None
    if mapped is not None:
        return mapped
    alias = RAW_JC_ALIASES.get(text)
    if alias is not None:
        return alias
    if text in VALID:
        return text
    raise UnsupportedAlignment(text)
