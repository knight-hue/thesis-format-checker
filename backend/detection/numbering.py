# -*- coding: utf-8 -*-
"""编号与章节推导：解析契约上的纯函数，供规则引擎与写回层共用。

集中“中文数字、章号提取、公式/题注编号推导”。

这里的编号都**已经写在段落文本里**；编号由 Word 自动编号生成、文本里没有的段落
由 `parser/numbering.py` 从 numbering.xml 还原，两者不要混用。
"""
import re

# 章节标题：第1章 / 第十二章 / 第12章。
CHAPTER_RE = re.compile(r"第([一二三四五六七八九十百零\d]+)章")

_CN_DIGIT = {
    "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}

# 题注编号头：图1-1 / 表 2-3 等。
CAPTION_HEAD_RE = re.compile(r"^[图表]\s*[一二三四五六七八九十百\d]+[.\-—]?\d*")


def cn_to_int(token):
    """中文数字转整数，如 十二 -> 12、二十三 -> 23；非法输入回退 1。"""
    token = (token or "").strip()
    if not token:
        return 1
    if token.isdigit():
        return int(token)
    section = 0
    number = 0
    for ch in token:
        if ch in _CN_DIGIT:
            number = _CN_DIGIT[ch]
        elif ch == "十":
            section += (number or 1) * 10
            number = 0
        elif ch == "百":
            section += (number or 1) * 100
            number = 0
        else:
            return 1
    return section + number


def chapter_no(text):
    """从标题文本提取章号；没有匹配返回 None。"""
    match = CHAPTER_RE.search(text or "")
    if not match:
        return None
    return cn_to_int(match.group(1))


_HEADING_KINDS = ("heading_1", "heading_2", "heading_3")
# 一级标题（章）。chapter_context 只在标题块里找章号。
_CHAPTER_KIND = "heading_1"
# 参与“正文提及”搜索的段落 kind（题注/标题/目录/公式/图片除外）。
_TEXT_KINDS = {
    "body",
    "abstract_body",
    "en_abstract_body",
    "keyword_body",
    "en_keyword_body",
    "acknowledgement_body",
    "reference_continuation",
    "note",
    "list_item",
}


def chapter_context(blocks, index):
    """返回 (章号, 最近章节标题块的位置)；无标题时 (1, 0)。

    P6：旧实现用 CHAPTER_RE.search 扫**所有**段落，正文里引用“第3章介绍了…”
    的句子会把当前章号整体抬到 3，导致公式/题注编号推导全部错位。现在只在
    标题块里认章号；正文里的“第N章”仅作为完全没有标题时的兜底。

    一级标题用 Word 自动编号、正文文本里没有“第N章”时，按已出现的一级
    标题个数推导章号：有编号文本的标题用“其章号 + 其后无编号标题个数”，
    标题全都没有编号文本时，内容前方有几个一级标题就是第几章。
    """
    heading_positions = []
    numbered = None
    text_fallback = None
    for pos in range(index - 1, -1, -1):
        blk = blocks[pos]
        if blk.get("type") != "paragraph":
            continue
        kind = blk.get("kind")
        if kind == _CHAPTER_KIND:
            heading_positions.append(pos)
            number = chapter_no(blk.get("text") or "")
            if number:
                numbered = (number, pos)
                break
            continue
        if kind in _HEADING_KINDS:
            continue
        if text_fallback is None:
            number = chapter_no(blk.get("text") or "")
            if number:
                text_fallback = (number, pos)

    if numbered is not None:
        number, start = numbered
        # 有编号标题之后还出现了几个无编号一级标题 → 章号顺延
        extra = sum(1 for pos in heading_positions if pos > start)
        if extra:
            return number + extra, heading_positions[0]
        return number, start
    if heading_positions:
        # 一级标题都没写“第N章”（编号由 Word 自动编号）时，按已出现的一级
        # 标题个数定章：内容位于第 k 个一级标题之后时，其前方恰有 k 个一级标题。
        # 旧实现返回 len + 1，章号整体上浮一格——实测蒙博第 2 章的公式被要求改成
        # (3-1)，24 份样张 44 条 formula.number_continuity 全由此而来。
        return len(heading_positions), heading_positions[0]
    if text_fallback is not None:
        return text_fallback
    return 1, 0


def _fullwidth_to_ascii(text):
    """全角数字/括号/短横线归一化为半角，供编号比较使用。"""
    out = []
    for ch in text or "":
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def normalize_formula_number(number):
    """公式编号归一化：全角括号/数字统一成半角，便于“（1-1）”与“(1-1)”比较。"""
    return _fullwidth_to_ascii(number or "").replace(" ", "").replace("—", "-").replace("－", "-")


def _paren_style(blocks, start, index):
    """本段之前已有公式编号用的括号风格：全角"（）" 或半角"()"。"""
    for pos in range(index - 1, start - 1, -1):
        number = (blocks[pos].get("formula_number") or "").strip()
        if number.startswith("（"):
            return "（", "）"
        if number.startswith("("):
            return "(", ")"
    return "(", ")"


def expected_formula_number(blocks, index):
    """按“章-序号”规则推导当前公式应有的编号，如 (1-2)。

    括号风格跟随本章已有编号（全角/半角），避免把一份通篇用“（1-1）”的论文
    判成编号不连续、并在自动修复时被悄悄改成半角。
    """
    # 循环导入：规则层反过来依赖本模块（rules/formula 顶部 import 本函数），
    # 故在这里惰性导入。
    from .rules.formula import is_standalone_formula

    chapter, chapter_start = chapter_context(blocks, index)
    seq = 0
    for pos in range(chapter_start, index):
        blk = blocks[pos]
        # 只数“独立成行的带编号公式”：本章已用掉多少个公式号。
        # 句内公式（OMML/OLE 句内公式、题干里的公式）也是 kind=formula 且
        # 常常带着提到的编号，但它们不是一条公式，数进去会把后续公式的期望号
        # 整体抬高（实测程昊男 (2-2) 被要求 (2-3)、高子鸿 (4-3) 要求 (4-4)）。
        if (blk.get("kind") == "formula" and blk.get("formula_number")
                and is_standalone_formula(blk)):
            seq += 1
    left, right = _paren_style(blocks, chapter_start, index)
    return f"{left}{chapter}-{seq + 1}{right}"


def caption_text(blocks, index, kind):
    """推导题注编号：章号取最近标题，序号取同章已有题注数 + 1。

    P6：旧实现用“段落文本以图/表开头”计数，正文里“图2-1给出了…”这类
    提及句会被算成一条题注，导致推导出的编号偏大。现在只统计已经分类为
    题注（table_caption/figure_caption）且带规范编号头的块。
    """
    prefix = "表" if kind == "table" else "图"
    caption_kind = "table_caption" if kind == "table" else "figure_caption"
    chapter, chapter_start = chapter_context(blocks, index)
    seq = 0
    for pos in range(chapter_start, index):
        blk = blocks[pos]
        if blk.get("type") != "paragraph" or blk.get("kind") != caption_kind:
            continue
        text = (blk.get("text") or "").strip()
        if CAPTION_HEAD_RE.match(text):
            seq += 1
    return f"{prefix}{chapter}-{seq + 1}"


_CAPTION_KEY_RE = re.compile(r"^([图表])\s*(\d+)\s*[-－—]\s*(\d+)")


def caption_key(text, kind=None):
    """从题注/引用文本提取归一化编号，如 '图1-1'；无法识别返回 None。

    kind 为 'table' / 'figure' 时，仅接受对应前缀；否则按文本自带前缀。
    """
    match = _CAPTION_KEY_RE.match((text or "").strip())
    if not match:
        return None
    prefix = match.group(1)
    if kind == "table" and prefix != "表":
        return None
    if kind == "figure" and prefix != "图":
        return None
    return f"{prefix}{match.group(2)}-{match.group(3)}"


def first_mention_index(blocks, key):
    """返回正文段中首次提及编号 key（如 '图1-1'）的 block index；无则 None。

    P6：旧实现只扫描 kind == "body"，摘要、英文摘要、关键词、致谢等区域里
    的提及（“图2-1给出了…”）被忽略，导致“图表未在正文中提及”误报。
    """
    match = _CAPTION_KEY_RE.match((key or "").strip())
    if not match:
        return None
    prefix, a, b = match.group(1), match.group(2), match.group(3)
    sep = r"[\s\-－—]*"
    pattern = re.compile(
        re.escape(prefix) + sep + re.escape(a) + sep + re.escape(b) + r"(?!\d)"
    )
    for index, block in enumerate(blocks or []):
        if block.get("type") != "paragraph":
            continue
        if block.get("kind") not in _TEXT_KINDS:
            continue
        if pattern.search(block.get("text") or ""):
            return index
    return None
