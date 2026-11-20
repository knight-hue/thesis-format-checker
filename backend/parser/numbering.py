"""Word 自动编号（word/numbering.xml）的“渲染 oracle”。

与 `detection/numbering.py`（按文本推导题注/公式编号）分工不同：这里处理的是
**编号根本不在文本里**的那些段落，从 numbering.xml 还原出 Word 实际显示的编号。

段落编号不在文本里（见 font_reader.read_auto_number）：`<w:numPr>` 只说明“这一段
有编号”，编号长什么样要回到 numbering.xml 按 `numId -> abstractNum -> lvl` 取
`numFmt` 与 `lvlText`，再把 `%1`…`%9` 用计数器填出来（`[%1]` + 第 3 条 → `[3]`）。

只看文本的检查（参考文献条目序号、标题编号格式）此前只能跳过这类段落，于是
Word 自动编号的参考文献被报“缺序号”。有了渲染结果，检查有据可依。

官方模板的各级标题就是这种自动编号：文本里没有“1.1.1”，编号由 Word 渲染。

还原不出来时一律返回 None —— 宁可跳过，也不拿猜出来的编号报错。
"""
from docx.oxml.ns import qn

_CN_DIGITS = "零一二三四五六七八九"
_FULLWIDTH_DIGITS = "０１２３４５６７８９"
_ROMAN_UNITS = ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"),
                (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
                (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"))


def _val(element):
    """取元素的 w:val；元素不存在或没有该属性时返回 None。"""
    if element is None:
        return None
    return element.get(qn("w:val"))


def _int_val(element, default):
    value = _val(element)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _attr_int(element, name):
    """取元素自身属性上的整数（如 `<w:num w:numId="5">`）；缺失返回 None。"""
    raw = element.get(qn(name))
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _chinese_number(value):
    """1~99 的中文数字（Word 的 chineseCounting 系列）。"""
    if not 1 <= value <= 99:
        return None
    if value < 10:
        return _CN_DIGITS[value]
    tens, ones = divmod(value, 10)
    head = "十" if tens == 1 else _CN_DIGITS[tens] + "十"
    return head + (_CN_DIGITS[ones] if ones else "")


def _roman_number(value, upper):
    if not 1 <= value <= 3999:
        return None
    remain, out = value, []
    for amount, glyph in _ROMAN_UNITS:
        while remain >= amount:
            out.append(glyph)
            remain -= amount
    text = "".join(out)
    return text.upper() if upper else text


def _letter_number(value, upper):
    if not 1 <= value <= 26:
        return None
    letter = chr(ord("A") + value - 1)
    return letter if upper else letter.lower()


def format_counter(value, num_fmt):
    """按 numFmt 把一个计数值格式化成编号文字；不支持的形式返回 None。"""
    if num_fmt == "decimal":
        return str(value)
    if num_fmt == "decimalZero":
        return "%02d" % value
    if num_fmt == "decimalFullWidth":
        return "".join(_FULLWIDTH_DIGITS[int(ch)] for ch in str(value))
    if num_fmt in ("chineseCounting", "chineseCountingThousand",
                   "chineseLegalSimplified"):
        return _chinese_number(value)
    if num_fmt == "upperLetter":
        return _letter_number(value, True)
    if num_fmt == "lowerLetter":
        return _letter_number(value, False)
    if num_fmt == "upperRoman":
        return _roman_number(value, True)
    if num_fmt == "lowerRoman":
        return _roman_number(value, False)
    return None


def _read_levels(elements):
    """一组 w:lvl 元素 -> {ilvl: (numFmt, lvlText, start)}。

    注意 `w:ilvl` 是 `w:lvl` 的属性（不是子元素）：按子元素读会全部读成 None，
    于是每一级都落在同一个键上、被最后一级覆盖。
    """
    levels = {}
    for lvl in elements:
        ilvl = _attr_int(lvl, "w:ilvl")
        if ilvl is None:
            ilvl = 0
        levels[ilvl] = (
            _val(lvl.find(qn("w:numFmt"))),
            _val(lvl.find(qn("w:lvlText"))),
            _int_val(lvl.find(qn("w:start")), 1),
        )
    return levels


def _read_level_pprs(elements):
    """一组 w:lvl 元素 -> {ilvl: 该级的 w:pPr 元素}（没写 pPr 的级不登记）。

    numbering 级别里的 w:pPr（典型是 w:ind 缩进）在 Word 中作为一层段落格式
    生效：段落直接写 w:ind 时它被覆盖，否则它覆盖段落样式链的缩进。参考文献
    列表的“左顶格”就是这样构成的（lvl 自带 left=440 hanging=440，Word 实测
    渲染为 LeftIndent=22pt、FirstLine=-22pt）。没有这一层，缩进量出来的是
    样式链的假值（实测 5 份样张 100 条“未左顶格”误报）。
    """
    pprs = {}
    for lvl in elements:
        ppr = lvl.find(qn("w:pPr"))
        if ppr is None:
            continue
        ilvl = _attr_int(lvl, "w:ilvl")
        if ilvl is None:
            ilvl = 0
        pprs[ilvl] = ppr
    return pprs


class NumberingDefinitions:
    """numbering.xml 里 (numId, ilvl) -> (numFmt, lvlText, start) 的查表。"""

    def __init__(self, doc):
        self._levels = {}
        # (num_id, ilvl) -> 该级的 w:pPr 元素（见 level_ppr 的说明）。
        self._pprs = {}
        root = _numbering_root(doc)
        if root is None:
            return
        self._load(root)

    @classmethod
    def from_root(cls, root):
        """直接用 numbering 根元素建表（测试与离线核对用，不必先有 docx）。"""
        instance = cls.__new__(cls)
        instance._levels = {}
        instance._pprs = {}
        if root is not None:
            instance._load(root)
        return instance

    def _load(self, root):
        abstract = {}
        abstract_pprs = {}
        for element in root.findall(qn("w:abstractNum")):
            abstract[element.get(qn("w:abstractNumId"))] = _read_levels(
                element.findall(qn("w:lvl")))
            abstract_pprs[element.get(qn("w:abstractNumId"))] = _read_level_pprs(
                element.findall(qn("w:lvl")))
        for element in root.findall(qn("w:num")):
            num_id = _attr_int(element, "w:numId")
            if num_id is None:
                continue
            ref = element.find(qn("w:abstractNumId"))
            levels = dict(abstract.get(_val(ref), {}))
            pprs = dict(abstract_pprs.get(_val(ref), {}))
            for override in element.findall(qn("w:lvlOverride")):
                ilvl = _attr_int(override, "w:ilvl")
                if ilvl is None:
                    ilvl = 0
                inline = override.find(qn("w:lvl"))
                if inline is not None:
                    levels.update(_read_levels([inline]))
                    pprs.update(_read_level_pprs([inline]))
                    continue
                start = _int_val(override.find(qn("w:startOverride")), None)
                if start is not None and ilvl in levels:
                    fmt, text, _ = levels[ilvl]
                    levels[ilvl] = (fmt, text, start)
            for ilvl, spec in levels.items():
                self._levels[(num_id, ilvl)] = spec
            for ilvl, ppr in pprs.items():
                self._pprs[(num_id, ilvl)] = ppr

    def spec(self, num_id, ilvl):
        return self._levels.get((num_id, ilvl))

    def level_ppr(self, num_id, ilvl):
        """该编号坐标的 w:pPr 元素（编号自带的段落格式，典型是缩进）；无则 None。

        Word 里这一层介于段落直接格式与段落样式之间：段落直接写 w:ind 时它被
        覆盖，否则它覆盖样式链的缩进。参考文献列表的“左顶格”就是这样构成的
        （lvl 自带 left=440 hanging=440，实测崔镭明的列表渲染为 LeftIndent=22pt、
        FirstLine=-22pt）。
        """
        return self._pprs.get((num_id, ilvl))


def _numbering_root(doc):
    """numbering.xml 根元素；文件不存在或读不到时返回 None。"""
    try:
        return doc.part.numbering_part.element
    except Exception:
        return None


class NumberingRenderer:
    """按文档顺序把自动编号渲染成可见文字（带计数器）。

    计数器按 OOXML 的列表语义推进：同一 numId 下某级出现时，比它更深的级数
    清零（下一段用到时从各自的 start 重新开始）。
    """

    def __init__(self, doc):
        self._defs = NumberingDefinitions(doc)
        # (num_id, ilvl) -> 该级最近一次用掉的计数值
        self._last = {}

    @classmethod
    def from_root(cls, root):
        """直接用 numbering 根元素建渲染器（测试与离线核对用）。"""
        instance = cls.__new__(cls)
        instance._defs = NumberingDefinitions.from_root(root)
        instance._last = {}
        return instance

    def render(self, num_id, ilvl):
        """该编号坐标在这一段应显示的编号文字；无法还原返回 None。"""
        spec = self._defs.spec(num_id, ilvl)
        if spec is None:
            return None
        num_fmt, lvl_text, start = spec
        if not lvl_text or "%" not in lvl_text:
            return None
        if format_counter(start, num_fmt) is None:
            return None
        value = self._last.get((num_id, ilvl), start - 1) + 1
        self._last[(num_id, ilvl)] = value
        for key in [k for k in self._last if k[0] == num_id and k[1] > ilvl]:
            del self._last[key]
        return self._fill(num_id, ilvl, lvl_text, value, num_fmt)

    def _fill(self, num_id, ilvl, lvl_text, value, num_fmt):
        out = ""
        index = 0
        while index < len(lvl_text):
            char = lvl_text[index]
            if (char == "%" and index + 1 < len(lvl_text)
                    and lvl_text[index + 1].isdigit()):
                piece = self._piece(num_id, ilvl, int(lvl_text[index + 1]) - 1,
                                    value, num_fmt)
                if piece is None:
                    return None
                out += piece
                index += 2
                continue
            out += char
            index += 1
        return out or None

    def _piece(self, num_id, ilvl, level, value, num_fmt):
        if level == ilvl:
            return format_counter(value, num_fmt)
        # 上层编号：本层没渲染过就说明上层还没出现过，宁可不渲染也不猜
        current = self._last.get((num_id, level))
        if current is None:
            return None
        spec = self._defs.spec(num_id, level)
        if spec is None:
            return None
        return format_counter(current, spec[0])


def attach_auto_number_text(renderer, para, doc, block):
    """把自动编号的可见文字写进 block["auto_number_text"]（渲染不出就不写）。

    必须按文档顺序调用：编号计数器依赖调用顺序。
    """
    if renderer is None or not block.get("auto_number"):
        return
    from .font_reader import read_numbering_ref

    ref = read_numbering_ref(para, doc)
    if ref is None:
        return
    text = renderer.render(*ref)
    if text:
        block["auto_number_text"] = text
