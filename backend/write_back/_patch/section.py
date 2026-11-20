# -*- coding: utf-8 -*-

"""分节级原位写回：页边距、纸张、方向、页眉页脚距（单位 cm）。"""

from docx.enum.section import WD_ORIENT
from docx.shared import Cm

from .diff import value_changed
from ..op_section import PAPER_SIZES_CM


def patch_section(section, old, new, report, where):
    changed = False
    for key, attribute in (
        ("left_cm", "left_margin"), ("right_cm", "right_margin"),
        ("top_cm", "top_margin"), ("bottom_cm", "bottom_margin"),
        ("header_cm", "header_distance"), ("footer_cm", "footer_distance"),
    ):
        if value_changed(old, new, key):
            setattr(section, attribute, Cm(new[key]))
            changed = True
    if value_changed(old, new, "paper_size"):
        size = new.get("paper_size")
        if size not in PAPER_SIZES_CM:
            report.skip(f"不支持的纸张尺寸：{size}", where)
        else:
            width, height = PAPER_SIZES_CM[size]
            if new.get("orientation") == "landscape":
                width, height = height, width
            section.page_width, section.page_height = Cm(width), Cm(height)
            changed = True
    elif value_changed(old, new, "orientation"):
        section.page_width, section.page_height = section.page_height, section.page_width
        changed = True
    if value_changed(old, new, "orientation"):
        section.orientation = (
            WD_ORIENT.LANDSCAPE if new.get("orientation") == "landscape"
            else WD_ORIENT.PORTRAIT
        )
        changed = True
    return changed
