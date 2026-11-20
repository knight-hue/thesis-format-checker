"""公式检查（§8：居中、编号置于行末、编号格式与连续性、禁止虚线）。"""
import re

from backend.parser.formula import FORMULA_NUM_RE, dotted_between_formula_and_number
from ..numbering import expected_formula_number, normalize_formula_number
from .base import section_text_width_pt


# 判断"这段是不是一条独立成行的公式"时，允许剩下的文字量上限。
# 中文按字算、西文按**词**算：这样"Precision (3-7)"这种带西文名标签的公式
# 不会因为几个字母就被当成散文，而"…如公式（3-5）所示。"这类整句会被认出来。
_STANDALONE_MAX_WORD_UNITS = 6
_WORD_UNIT_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z]+")

# 句末标点：以它收尾的段落是正文句，不是公式本体。
_SENTENCE_END_RE = re.compile(r"[。！？；]$")

# "视觉居中"判定用到的空白字符（半角空格、全角空格）。
_FORMULA_SPACE_CHARS = " \u3000"

# 行内“居中装置”空白下限：≥2 个空白即视为手工撑排
# （实测样张：张心宇 30 空格、吕彦霖 2 制表位、黄书成 14 空格等）。
_FORMULA_PADDING_MIN_SPACES = 2

# OLE 对象的记录宽度：v:shape style="width:…pt"（实测匡瑞 375.25pt ↔ Word
# 渲染实测 375.0，吻合到 0.3pt 内）。
_OBJECT_WIDTH_RE = re.compile(r"width\s*:\s*([\d.]+)\s*pt")

# “铺满行宽”比例：对象宽 ≥ 该比例 × 版心时，“对象+编号”整体铺满行宽、视觉居中
# （实测锚点：匡瑞 362/375pt 对 425pt 版心 = 0.85/0.88，公式中点 274.8/272.7
# 与列中心 297.6 相差 25pt 内；张权辉左对齐 375~437pt 同理）。左对齐时对象从
# 左边线起铺，需要更宽才能把中点压回列中心附近，故比例分对齐取值；右/两端
# 对齐由编号槽把对象从右缘推开，取更宽放的比例。0.6 是几何外推（对象+
# 编号占右槽模型），样本无此区间实例——方向是宁可放过，不冤枉。
_OLE_WIDE_RATIO = {"left": 0.85, "right": 0.6, "justify": 0.6}


def _ole_object_fills_line(block, text_width_pt, align):
    """OLE 对象是否宽到“对象+编号”铺满行宽（视觉居中）。

    宽度或版心取不到时返回 True（判不了不报，维持旧口径）。
    """
    widths = []
    for blob in (block.get("formula_xml") or []):
        widths.extend(float(width) for width in _OBJECT_WIDTH_RE.findall(blob))
    if not widths or not text_width_pt:
        return True
    ratio = _OLE_WIDE_RATIO.get(align, _OLE_WIDE_RATIO["left"])
    return max(widths) >= ratio * text_width_pt


def formula_layout_centered(block, align, text_width_pt=None):
    """公式段落是否具备“居中书写”的**视觉形态**（文件侧可判定的部分）。

    模板原句只要求“公式应居中书写”——判的是结果，不是某一种实现手段。实测
    （Word COM 逐对象量渲染位置，列中心 297.6pt）：

    - ``＃``/``#`` 公式自动编号（Word 机制）：公式实测精确居中（公式起点
      211.2 ≈ 297.6 − 宽/2，两条独立样张都吻合），编号自动贴右缘；
    - 居中制表位（段落样式里定义 + 右对齐）：WPS 标准写法（10 份样张 129 条）；
    - 右/两端对齐 + 空白装置（制表位或 ≥2 空白）：空白把公式从右缘推回中部；
    - OLE 嵌入对象（MathType）：记录宽度 ≥ 0.85（左对齐）/0.6（右·两端）倍版心
      时“对象+编号”铺满行宽、视觉居中（实测 362/375pt 对 425pt 版心，中点
      274.8/272.7 与列中心 297.6 差 25pt 内）；窄对象落回对齐+装置判断；
      宽度或版心取不到时判不了，放过。

    返回 True = 视作已居中；False = 按未居中止报。
    """
    blob = (block.get("text") or "") + (block.get("formula_text") or "")
    if "\uFF03" in blob or "#" in blob:
        return True
    if block.get("center_tab"):
        return True
    # OLE 对象的记录宽度可读时按“铺满行宽”判（实测锚点见 _OLE_WIDE_RATIO）：
    # 宽对象无论对齐都视觉居中——左锚（张权辉 375~437pt）与右贴（匡瑞
    # 362/375pt）的中点都落在列中心附近；窄对象落回对齐+装置的判断；
    # 宽度或版心取不到时判不了，放过。
    if block.get("formula_type") == "ole":
        if _ole_object_fills_line(block, text_width_pt, align):
            return True
    if align not in ("right", "justify"):
        return False
    ftext = block.get("formula_text") or ""
    residual = block.get("text") or ""
    for fragment in (ftext, block.get("formula_number") or ""):
        if fragment:
            residual = residual.replace(fragment, "")
    tabs = residual.count("\t") + ftext.count("\t")
    spaces = (sum(1 for ch in residual if ch in _FORMULA_SPACE_CHARS)
              + sum(1 for ch in ftext if ch in _FORMULA_SPACE_CHARS))
    if tabs > 0 or spaces >= _FORMULA_PADDING_MIN_SPACES:
        return True
    return False


def is_standalone_formula(block):
    """整段是否**就是一条公式**（而不是"一句正文里夹了个行内公式"）。

    模板里"公式应居中书写""编号用圆括号括起放在公式右边行末"说的是**排成独立一行的
    公式**。可是含 OMML/OLE 公式对象的**句子**也会被解析成公式段落，于是被按显示公式
    量：实测 24 份样张里这样量出 71 条与事实不符的错——"公式应居中书写" 65 条、
    "编号置于行末" 3 条、编号不连续 3 条。例如
    "逻辑回归用一组权重 w=[w1，w2，w3] 以及偏置 b 来得到最终的融合概率如公式（3-5）所示。"
    是一句话，谈不上居中；其中的（3-5）是对公式的**引用**，不是这条公式的编号。

    判据：把段落文字里的公式本体（formula_text）与编号都去掉，剩下的中文/西文词
    超过 6 个，就说明这一段主要是文字，不是一条公式。另外，"以句末标点收尾"的段落
    一律不是公式——解析层对**纯文本公式**会把整段文字连同编号一起填进 formula_text，
    扣减后残句恒为空（实测 1 条"…如公式（3-5）所示。"被当公式报了编号位置）。
    """
    text = (block.get("text") or "").strip()
    if _SENTENCE_END_RE.search(text):
        return False
    residual = text
    for part in (block.get("formula_number"), block.get("formula_text")):
        if part:
            residual = residual.replace(part, "")
    return len(_WORD_UNIT_RE.findall(residual)) <= _STANDALONE_MAX_WORD_UNITS


class FormulaMixin:
    """公式段。"""

    def _check_formula(self, b, ctx):
        rule = self.rules.get("formula", {})
        loc = self._loc(b)
        # "居中""编号位置"只对独立成行的公式成立（见 is_standalone_formula）。
        standalone = is_standalone_formula(b)
        align = b.get("alignment")
        if standalone and align and rule.get("alignment") and align != rule["alignment"]:
            # “居中书写”按视觉结果判——各类“已经视觉居中”的写法与实测依据
            # 见 formula_layout_centered；剩下的贴边行照旧报“未居中”。
            if not formula_layout_centered(
                    b, align, section_text_width_pt(self._sections)):
                self._issue("formula.alignment", "公式", "error", loc,
                            align, rule["alignment"], "公式应居中书写")
        text = b.get("text") or ""
        number = b.get("formula_number")
        if not number:
            match = FORMULA_NUM_RE.search(text)
            number = match.group(0) if match else None

        # 编号位置由 rules 的 formula.number_position 决定：end=行末，start/begin=行首。
        if standalone and number:
            stripped = text.strip()
            at_start = rule.get("number_position") in ("start", "begin")
            where = "行首" if at_start else "行末"
            placed = stripped.startswith(number) if at_start else stripped.endswith(number)
            if not placed:
                # 编号不在本段文字里，说明解析层是按「公式一行、编号一行」的排版
                # 把后一行的编号挂到公式段上的：编号确实没落在公式的同一行。
                reason = (f"公式编号应与公式同行、置于{where}" if number not in text
                          else f"公式编号应置于{where}")
                self._issue("formula.number_position", "公式", "warning", loc,
                            text[:30], f"编号置于{where}", reason)

        # 编号格式：(章-序) 阿拉伯数字
        pattern = rule.get("number_pattern")
        num_re = re.compile(pattern) if pattern else None
        if number and num_re and not num_re.fullmatch(number):
            self._issue("formula.number_format", "公式", "error", loc,
                        number, "(章-序)", "公式编号格式应为(章-序)")

        # 编号连续性：章号 + 章内序号（规则文件可用 check_continuity 关闭本项检查）
        if standalone and rule.get("check_continuity", True) and number and num_re \
                and num_re.fullmatch(number):
            expected = expected_formula_number(ctx.blocks, ctx.index)
            if expected and normalize_formula_number(number) != normalize_formula_number(expected):
                first_break = self._first_formula_break(ctx)
                if first_break is None:
                    message, suggestion = "公式编号不连续，应逐个递增", ""
                else:
                    # 级联：前面已经断号，后面只是跟着错位；指向根因，别逐条改。
                    message = "公式编号不连续（受前一处断号影响）"
                    suggestion = (f"先修正文第 {first_break + 1} 段附近的第一处断号，"
                                  "后面的编号会随之归位；本条不必单独改")
                self._issue("formula.number_continuity", "公式", "warning", loc,
                            number, expected, message, suggestion=suggestion)

        # 公式与编号之间不应有虚线（只看编号紧邻处，见 dotted_between_formula_and_number）
        if rule.get("forbid_dotted_line", True) \
                and dotted_between_formula_and_number(text, number):
            self._issue("formula.dotted_line", "公式", "error", loc,
                        text[:30], "无虚线", "公式与编号之间不应有虚线")

    def _first_formula_break(self, ctx):
        """本章第一处公式断号所在块的下标；没有断号返回 None。

        与 expected_formula_number 同一套计数口径（只数独立成行、带编号的公式）。
        """
        from ..numbering import chapter_context

        _chapter, start = chapter_context(ctx.blocks, ctx.index)
        for pos in range(start, ctx.index):
            block = ctx.blocks[pos]
            if (block.get("kind") != "formula" or not block.get("formula_number")
                    or not is_standalone_formula(block)):
                continue
            number = block["formula_number"]
            expected = expected_formula_number(ctx.blocks, pos)
            if normalize_formula_number(number) != normalize_formula_number(expected):
                return pos
        return None
