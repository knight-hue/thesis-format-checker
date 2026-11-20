# -*- coding: utf-8 -*-
"""修复层（facade）：把确认的 issue 应用到解析契约的 JSON 数据块上。

原则：
1. 不打开、不修改原 DOCX——修复层只改错误数据块，写回方式由上层选择；
2. 只对 ``document = {sections, blocks}`` 做确定性修改；
3. 结构性增删（补题注 / 插图 / 调题注位置）放在最后统一处理，
   内容类修改（字体 / 字号 / 编号 / 文字改写）先行，避免 index 漂移串味。

issue 结构（来自 checker_service 或前端）：
    {rule_id, category, severity, location, actual, expected,
     message, suggestion, source}
    - source == "rule_engine"：规则引擎产出，走格式/文字修复；
    - source == "rag"：语义检查产出，走文字改写；
    - image.replace / image.insert 前端注入项没有 source，
      由 apply_format_fixes / apply_structural_fixes 直接处理。

实现按职责拆分到同名目录下的私有模块，本文件只做转发：
    _core       —— 常量、数据块工具、规则→JSON 字段映射
    _content    —— 题注 / 公式 / 图片的内容变换
    _apply      —— issue 分派与格式层入口
    _structural —— 结构性增删 / 移动
    _text       —— 文字层应用
    _document   —— fix_document 总入口
"""
from ._core import (
    _NUM_RE, _CAPTION_FONT, _RUN_STYLE_KEYS,
    _TEXT_LAYER_RULE_IDS, _STRUCTURAL_RULE_IDS, _IMG_MAGIC,
    _parse_number, _to_int, _get_block, _get_run, _set_paragraph_text,
    _record_skip, _parse_size_spec, _resolve_format, _apply_generic_format,
)
from ._content import (
    _extract_caption_title, _rewrite_caption_number_block, _make_caption_block,
    _nearest_block, _move_caption_relative, _move_floating_group,
    _replace_formula_number_block, _move_formula_number_to_end_block,
    _remove_formula_dotted_line_block, _pin_image_paths, _save_image_bytes,
)
from ._apply import (
    _apply_section_fix, _apply_cell_fix, _apply_image_fix,
    _apply_paragraph_fix, _apply_formula_fix, apply_format_fixes,
)
from ._structural import _reindex_blocks, _apply_structural_fixes
from ._text import paragraph_texts, apply_text_changes
from ._document import fix_document

__all__ = [
    # 常量
    "_NUM_RE", "_CAPTION_FONT", "_RUN_STYLE_KEYS",
    "_TEXT_LAYER_RULE_IDS", "_STRUCTURAL_RULE_IDS", "_IMG_MAGIC",
    # 基础工具
    "_parse_number", "_to_int", "_get_block", "_get_run", "_set_paragraph_text",
    "_record_skip", "_parse_size_spec",
    # 格式映射
    "_resolve_format", "_apply_generic_format",
    # 题注
    "_extract_caption_title", "_rewrite_caption_number_block", "_make_caption_block",
    "_nearest_block", "_move_caption_relative", "_move_floating_group",
    # 公式
    "_replace_formula_number_block", "_move_formula_number_to_end_block",
    "_remove_formula_dotted_line_block",
    # 图片
    "_pin_image_paths", "_save_image_bytes",
    # issue 应用
    "_apply_section_fix", "_apply_cell_fix", "_apply_image_fix",
    "_apply_paragraph_fix", "_apply_formula_fix",
    # 结构性
    "_reindex_blocks", "_apply_structural_fixes",
    # 入口
    "apply_format_fixes", "paragraph_texts", "apply_text_changes", "fix_document",
]
