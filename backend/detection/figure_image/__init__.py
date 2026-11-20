# -*- coding: utf-8 -*-
"""位图图内文字的能力边界（模板 §5.3「图内字体字号：宋体五号字」）。

位图里的字是像素，字体名与字号都无法从图上可靠取得，所以这里只产出「这条没查、
请人工核对」的说明（理由见 `service.py`）；矢量图形里的文字在 docx 里是真字符，
由 `detection/rules/figure_content.py` 精确判定与修复。

（曾实现过像素级估字高/判字形/判坐标图；2026-10 用真实样张证伪：行投影找到的带是
网格线/方框边框/界面行，不是文字行，量出的不是字高，已整体删除。）
"""
from .service import bitmap_figures, summarize  # noqa: F401

__all__ = ["bitmap_figures", "summarize"]
