# -*- coding: utf-8 -*-
"""修复层基础：常量、数据块读写工具、规则 → JSON 字段映射。"""
import re

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")

# 题注统一格式：黑体 10.5pt 居中（与 config/rules/jlu/cs/jlu_cs_rules.json 的 caption 规范一致）。
_CAPTION_FONT = {"font_name": "黑体", "font_size_pt": 10.5}

_RUN_STYLE_KEYS = (
    "font_name", "east_asia_font", "font_size_pt",
    "bold", "italic", "char_spacing_pt",
)

# 语义改写拆分 run：字母/数字（含相邻西文标点并入）用西文字体，其余用中文字体。
_LATIN_CHAR_RE = re.compile(r"[A-Za-z0-9]")
_LATIN_SEGMENT_RE = re.compile(r"[A-Za-z0-9]+|[^A-Za-z0-9]+")
_DEFAULT_LATIN_FONT = "Times New Roman"

# 由文字改写层（rag/text_rewrite_service）处理的规则引擎文字类规则，
# 格式层不重复修改。集合必须与 rag 的 _DETERMINISTIC_FIXERS 键完全一致，
# 由 tests/test_rules_wiring.py 守住：多一项则前端承诺了修不了的修复。
_TEXT_LAYER_RULE_IDS = {
    "note.prefix", "reference.entry_period", "reference.entry_number",
    "caption.number_space", "heading.heading_1.number",
    "body.fullwidth_punct", "keyword.terminal_punct", "en_keyword.terminal_punct",
    "list_item.marker_punct", "list_item.number_format",
}

# 由结构性处理层（_apply_structural_fixes）处理的规则，格式层静默跳过。
_STRUCTURAL_RULE_IDS = {
    "table.caption_position", "figure.caption_position",
    "table.placement", "figure.placement",
    "table.continued_header", "table.continued_note_header",
    "image.insert",
}

# 需要图片二进制的写回规则：修复这些 issue 时前端按它们的出现顺序上传图片，
# 由 api 层注入 issue["image_bytes"]。经 fix/__init__ 对外暴露给 api 使用。
IMAGE_RULE_IDS = {"image.replace", "image.insert"}

# 图片二进制 -> 扩展名嗅探。
_IMG_MAGIC = (
    (b"\x89PNG", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF8", ".gif"),
    (b"BM", ".bmp"),
)


def _parse_number(value):
    """从 '3.0 cm' / '12pt' / '1.5 倍' 等描述里取第一个数字；失败返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _NUM_RE.search(str(value or ""))
    return float(match.group()) if match else None


def _to_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_block(blocks, index, kind=None):
    """按 index 取 block；可选校验 type。找不到返回 None。"""
    if not isinstance(index, int) or not 0 <= index < len(blocks):
        return None
    block = blocks[index]
    if kind is not None and block.get("type") != kind:
        return None
    return block


def _get_run(block, run_index):
    """取指定 run；缺 run 时造一个空 run（保持契约可渲染）。"""
    runs = block.setdefault("runs", [])
    if isinstance(run_index, int) and 0 <= run_index < len(runs):
        return runs[run_index]
    if run_index is None and runs:
        return runs[0]
    if not runs:
        runs.append({
            "text": "",
            "font_name": None,
            "east_asia_font": None,
            "font_size_pt": None,
            "bold": False,
            "italic": False,
            "char_spacing_pt": 0.0,
        })
    return runs[0]


def _set_paragraph_text(block, new_text, preserve_latin=False):
    """整段替换文本，保留首 run 的排版格式（公式的 formula_xml 不受影响）。

    preserve_latin=True 供语义改写使用：改写后新产生的数字/西文（如“五年”→“5年”）
    必须继续用原段落的西文字体，否则会变成中文字体（宋体），重检时多报一条
    “正文西文/数字字体不符合要求”。拆成按字符类别的多个 run 写回。
    """
    runs = block.get("runs") or []
    inherited = runs[0] if runs else {}
    style = {k: inherited.get(k) for k in _RUN_STYLE_KEYS}
    block["text"] = new_text
    if not preserve_latin:
        block["runs"] = [{**style, "text": new_text}]
        return

    latin_font = _latin_font_of(block)
    segments = _LATIN_SEGMENT_RE.findall(new_text) or [new_text]
    block["runs"] = []
    for segment in segments:
        run_style = dict(style)
        if latin_font and _LATIN_CHAR_RE.search(segment):
            run_style["font_name"] = latin_font
        block["runs"].append({**run_style, "text": segment})


def _latin_font_of(block):
    """原段落里西文/数字 run 用的字体，供改写后新产生的西文沿用。"""
    runs = block.get("runs") or []
    if not runs:
        return None
    first = runs[0]
    base_cjk = first.get("east_asia_font") or first.get("font_name")
    for run in runs:
        name = run.get("font_name")
        if name and name != base_cjk and _LATIN_CHAR_RE.search(run.get("text") or ""):
            return name
    name = first.get("font_name")
    if name and name != base_cjk:
        return name
    return _DEFAULT_LATIN_FONT


def _record_skip(skipped, rule_id, reason, **extra):
    skipped.append({"rule_id": rule_id, "reason": reason, **extra})


def _parse_size_spec(expected):
    """expected 为 {width_inch, height_inch} 或单数字（当宽度）。"""
    if isinstance(expected, dict):
        w = expected.get("width_inch", expected.get("width"))
        h = expected.get("height_inch", expected.get("height"))
        try:
            return (
                float(w) if w is not None else None,
                float(h) if h is not None else None,
            )
        except (TypeError, ValueError):
            return None, None
    try:
        return float(expected), None
    except (TypeError, ValueError):
        return None, None


# ====================== 规则后缀 -> JSON 字段 ======================
def _resolve_format(rule_id):
    """把 rule_id 的后缀映射到 (scope, json_key, parser)。

    scope:
        run      -> 改 runs[i] 的字段；
        para     -> 改 block 的段落级字段；
        run_both -> 同时改 runs[i] 的 font_name 与 east_asia_font。
    注意顺序：先匹配更具体的后缀（east_asia_font / latin_font / font_size …），
    "font" 放最后，避免把 body.latin_font 误判成 "font"。
    """
    suffixes = (
        # 图内文字的中文字体（figure.inner_east_asia_font）走的**就是**这一条：
        # 它以 east_asia_font 收尾，比 general 的 "font" 更具体，所以先命中这里，
        # 只改中文槽。若落到最后那条 "font"（run_both），会把图内西文/数字也改成
        # 宋体，而模板对图内西文数字没有规定（改了就是与事实不符）。
        ("east_asia_font", "run", "east_asia_font", str),
        ("latin_font", "run", "font_name", str),
        ("char_spacing", "run", "char_spacing_pt", _parse_number),
        ("font_size", "run", "font_size_pt", _parse_number),
        ("first_line_indent", "para", "first_line_indent_pt", _parse_number),
        ("entry_indent", "para", "first_line_indent_pt", _parse_number),
        ("line_spacing", "para", "line_spacing_multiple", _parse_number),
        ("space_before", "para", "space_before_pt", _parse_number),
        ("alignment", "para", "alignment", str),
        ("bold", "run", "bold", lambda v: v),
        ("font", "run_both", None, str),
    )
    for suffix, scope, key, parser in suffixes:
        if rule_id.endswith(suffix):
            return scope, key, parser
    return None


def _apply_generic_format(block, rule_id, expected, run_index=None):
    """按 _resolve_format 的映射改一个 paragraph block；成功返回 True。"""
    resolved = _resolve_format(rule_id)
    if resolved is None:
        return False
    scope, key, parser = resolved
    value = parser(expected)
    if value is None:
        return False

    if scope == "run_both":
        run = _get_run(block, run_index)
        run["font_name"] = str(value)
        run["east_asia_font"] = str(value)
        return True
    if scope == "run":
        run = _get_run(block, run_index)
        run[key] = value
        return True
    if scope == "para":
        block[key] = value
        if key == "line_spacing_multiple":
            # 切到倍数行距时清除固定行距，避免 render 优先用固定值。
            block["line_spacing_pt"] = None
        return True
    return False
