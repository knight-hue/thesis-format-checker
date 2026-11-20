# -*- coding: utf-8 -*-
"""修复层内容变换：题注、公式、图片三类数据块的改写 / 生成 / 定位。"""
import os
import re

from backend.docx_common.assets import find_image_file, image_path
from backend.parser.formula import FORMULA_NUM_RE, strip_dotted_between_formula_and_number
from backend.detection.numbering import caption_key, first_mention_index

from ._core import _CAPTION_FONT, _IMG_MAGIC, _set_paragraph_text


# ====================== 题注 ======================
def _extract_caption_title(text, kind):
    """剥离题注文本开头的编号，返回题名；无法剥离时返回原文。"""
    prefix = "图" if kind == "figure" else "表"
    text = (text or "").strip()
    if not text.startswith(prefix):
        return text
    rest = text[len(prefix):].lstrip()
    match = re.match(r"[\d一二三四五六七八九十百]+[.\-—]?\d*", rest)
    if match:
        return rest[match.end():].strip()
    return rest


def _rewrite_caption_number_block(block, new_number, kind):
    """重写题注编号，保留原题名与首 run 格式；无变化返回 False。"""
    title = _extract_caption_title(block.get("text") or "", kind)
    new_text = new_number + (f" {title}" if title else "")
    if new_text == (block.get("text") or ""):
        return False
    _set_paragraph_text(block, new_text)
    return True


def _make_caption_block(text, kind, style=None):
    """按检测 issue 携带的 rules 样式生成题注 paragraph block。"""
    caption_kind = "table_caption" if kind == "table" else "figure_caption"
    if not isinstance(style, dict):
        style = {}
    font = style.get("font_name") or _CAPTION_FONT["font_name"]
    east_asia_font = style.get("east_asia_font") or font
    size = style.get("font_size_pt") or _CAPTION_FONT["font_size_pt"]
    alignment = style.get("alignment") or "center"
    return {
        "type": "paragraph",
        "kind": caption_kind,
        "text": text,
        "alignment": alignment,
        "first_line_indent_pt": 0.0,
        "line_spacing_multiple": 1.5,
        "line_spacing_pt": None,
        "space_before_pt": 0.0,
        "space_after_pt": 6.0,
        "runs": [{
            "text": text,
            "font_name": font,
            "east_asia_font": east_asia_font,
            "font_size_pt": size,
            "bold": False,
            "italic": False,
            "char_spacing_pt": 0.0,
        }],
    }


def _nearest_block(blocks, pos, btype, forward_first=False):
    """找离 pos 最近的指定 type block；forward_first 决定先向前还是先后。"""
    rng1 = range(pos + 1, len(blocks)) if forward_first else range(pos - 1, -1, -1)
    rng2 = range(pos - 1, -1, -1) if forward_first else range(pos + 1, len(blocks))
    for i in rng1:
        if blocks[i].get("type") == btype:
            return i
    for i in rng2:
        if blocks[i].get("type") == btype:
            return i
    return None


def _move_caption_relative(blocks, caption_idx, kind):
    """把题注移动到对应图表旁：图题移到图片下方、表题移到表格上方。"""
    caption = blocks[caption_idx]
    if kind == "figure":
        anchor = _nearest_block(blocks, caption_idx, "image", forward_first=False)
        if anchor is None:
            return False
        target = anchor + 1  # 图题在图片下方
    else:
        anchor = _nearest_block(blocks, caption_idx, "table", forward_first=True)
        if anchor is None:
            return False
        target = anchor  # 表题在表格上方

    blocks.pop(caption_idx)
    if caption_idx < target:
        target -= 1
    target = max(0, min(target, len(blocks)))
    blocks.insert(target, caption)
    return True


def _move_floating_group(blocks, index, kind):
    """随文排：把图/表及其题注整体移动到首次提及该编号的正文段之后。

    index 是 image/table 块的位置。图组 = [image, figure_caption]，
    表组 = [table_caption, table]。返回是否发生移动。
    """
    if kind == "figure":
        if index + 1 >= len(blocks) or blocks[index + 1].get("kind") != "figure_caption":
            return False
        cap = blocks[index + 1]
        group_start, group_end = index, index + 2
    else:
        if index - 1 < 0 or blocks[index - 1].get("kind") != "table_caption":
            return False
        cap = blocks[index - 1]
        group_start, group_end = index - 1, index + 1

    key = caption_key(cap.get("text"), kind)
    mention = first_mention_index(blocks, key)
    if mention is None or mention < group_start:
        # 无正文提及，或组已在提及之后——保持原位。
        return False

    group = blocks[group_start:group_end]
    del blocks[group_start:group_end]

    # 删除后重找提及位置（组在提及之前，故提及 index 前移 group 长度）。
    mention = first_mention_index(blocks, key)
    if mention is None:
        blocks[group_start:group_start] = group  # 回滚
        return False
    blocks[mention + 1:mention + 1] = group
    return True


# ====================== 公式 ======================
def _replace_formula_number_block(block, new_number):
    text = block.get("text") or ""
    match = FORMULA_NUM_RE.search(text)
    if not match or match.group(0) == new_number:
        return False
    new_text = text[:match.start()] + new_number + text[match.end():]
    _set_paragraph_text(block, new_text)
    block["formula_number"] = new_number
    return True


def _move_formula_number_to_end_block(block):
    text = block.get("text") or ""
    match = FORMULA_NUM_RE.search(text)
    if not match:
        return False
    number = match.group(0)
    prefix = text[:match.start()].strip()
    suffix = text[match.end():].strip()
    rest = f"{prefix} {suffix}".strip() if suffix else prefix
    new_text = f"{rest} {number}".strip() if rest else number
    if new_text == text:
        return False
    _set_paragraph_text(block, new_text)
    block["formula_number"] = number
    return True


def _number_on_own_line(block):
    """公式段带编号、但段内文字里没有编号——编号在另一段里（独占一行）。

    "公式一行、编号一行"的排版解析后就是这种状态：编号由一个独立的
    kind=formula_number 段承载，公式段自己只存 formula_number 字段。
    """
    number = (block.get("formula_number") or "").strip()
    return bool(number) and not FORMULA_NUM_RE.search(block.get("text") or "")


def _remove_formula_dotted_line_block(block):
    """只删“公式与编号之间”的虚线：公式内部的省略号照留（与判定口径一致）。"""
    text = block.get("text") or ""
    new_text = strip_dotted_between_formula_and_number(text, block.get("formula_number"))
    if new_text == text:
        return False
    _set_paragraph_text(block, new_text)
    return True


# ====================== 图片 ======================
def _pin_image_paths(document, images_dir):
    """给无 path 的 image block 绑定显式 path，使其不依赖 index 反查。

    结构性增删会改变 block 的 index，而解析时图片文件名按 index 命名
    （img_0004.png）。绑定 path 后，增删块不再影响图片文件的定位。
    """
    if not images_dir or not os.path.isdir(images_dir):
        return
    blocks = document.get("blocks", [])
    for block in blocks:
        if block.get("type") != "image" or block.get("path"):
            continue
        # 按 index 反查图片文件（返回绝对路径），再落成契约约定的
        # "images/<文件名>" 写法，保证复现时 json 与 images 目录同级。
        found = find_image_file(images_dir, block.get("index", 0))
        if found:
            block["path"] = image_path(os.path.basename(found))


def _save_image_bytes(images_dir, data, index):
    """把新图片字节写入 images 目录，返回文件名。"""
    os.makedirs(images_dir, exist_ok=True)
    ext = ".png"
    for magic, candidate in _IMG_MAGIC:
        if data.startswith(magic):
            ext = candidate
            break
    fname = f"img_replaced_{index:04d}{ext}"
    with open(os.path.join(images_dir, fname), "wb") as f:
        f.write(data)
    return fname
