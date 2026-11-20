# -*- coding: utf-8 -*-
"""写回层的缺省值，以及"缺省字段该取什么"的统一入口。

这里的字典是**契约字段缺省时**的兜底镜像，不是第二份规则来源：`config/rules/*.json`
才是判定依据，本模块只是取不到值时的具体数字/字体。一旦 rules 改了而这里没跟着
改，重建出的文档就会与检测结论自相矛盾，因此字段与 rules 的对应关系由
``tests/test_defaults_consistency.py`` 逐个断言。

原先四份 ``_pick`` / ``_val`` 的实现完全等价（None 表示未指定，其余取值——含
显式 0 / False——一律按给定值处理），合并为 pick。
"""

DEFAULT_RUN = {
    "font_name":      "Times New Roman",
    "east_asia_font": "宋体",
    "font_size_pt":   12.0,
    # W2：这里不给 bold/italic 默认值。None 表示"未指定"，此时不写 w:b / w:i；
    # 写 False 会生成显式的"不加粗"，把原文档靠样式继承来的加粗覆盖掉
    # （标题变细就是这个问题）。
    "char_spacing_pt": 0.0,
}

DEFAULT_CELL = {
    # 表内中文取宋体、字号取五号（§5.5「表内字体字号：宋体五号字」）：这两个槽由 rules
    # 驱动，必须与 rules 一致。
    "east_asia_font": "宋体",             # 中文（rules: tables.cell_east_asia_font）
    "font_size_pt":   10.5,               # 五号（rules: tables.cell_font_size_pt）
    # 西文槽是**写回层自己的兜底**，rules 不管：模板在图表那一章没有“表内西文/数字
    # 用什么字体”的说明句（只有 §4.3 正文那句「西文、数字：Times New Roman」），
    # 判定层据此不查这一项（table.cell_latin_font 已撤销）。重建文档时仍需要一个具体
    # 字体，故保留，同 DEFAULT_SECTION 的 paper_size/orientation 的做法。
    "font_name":      "Times New Roman",
}

DEFAULT_IMAGE = {
    # width_inch / height_inch 为 None 表示"未指定尺寸时不缩放，保持图片原始尺寸"，
    # 而不是强行拉成某个固定宽度。
    "width_inch":      None,
    "height_inch":     None,
    "align":          "center",
    "space_before_pt": 0.0,
    "space_after_pt":  6.0,
}

DEFAULT_SECTION = {
    # paper_size / orientation 是**写回层自己的兜底**，不受 rules 驱动：官方模板
    # 通篇没有“纸张尺寸/页面方向”的要求句（它自己就是 A4 纵向），所以判定层不查
    # 这两项，rules 里也就没有对应键（见 tests/test_defaults_consistency.py 的
    # _SECTION_NOT_FROM_RULES）。重建文档时仍需要一个具体尺寸，故保留。
    "paper_size": "A4",
    "orientation": "portrait",
    "left_cm": 3.0,
    "right_cm": 3.0,
    "top_cm": 2.0,
    "bottom_cm": 2.0,
    "header_cm": 1.5,
    "footer_cm": 1.5,
}


def pick(config, key, default):
    """取配置项；None 表示"未指定"，此时用 default，显式 0 / False 仍按给定值。"""
    value = config.get(key, None)
    return default if value is None else value
