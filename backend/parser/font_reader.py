"""DOCX 主题字体、run 字体和段落样式读取。"""
import re
import weakref
import xml.etree.ElementTree as ET

from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn
# python-docx 没有公开导出 StyleFactory（它在 docx.styles.style 里），
# 缓存 base_style 时要用它把 CT_Style 元素包回样式对象，保证返回类型与原实现一致。
from docx.styles.style import StyleFactory

from backend.docx_common.alignment import UnsupportedAlignment, from_jc

from .numbering import NumberingDefinitions

# ---------------------------------------------------------------------------
# 样式解析缓存
# ---------------------------------------------------------------------------
# python-docx 的 ``run.style`` / ``paragraph.style`` 每次调用都会：
#   1. 重新解析 Part→styles.xml 的关系（``part_related_by``）；
#   2. ``Styles.get_by_id``：没有 w:rStyle 时落到 ``default_for``，那是
#      "遍历整张样式表筛 type/default" 的 O(样式数) 扫描（每个属性读一次 XML）。
# 实测一份真论文（508 段 / 6030 个 run）：**52,280 次 get_style、71,024 次
# part_related_by、36,962 次 default_for**，解析 26 秒里大部分花在这里，
# 而同一个文件 python-docx 只花 0.09 秒就能打开。
# 样式表在一份文档内是不变的，故按 (part, style_id, 类型) 缓存，把调用降到几十次。
# 缓存键用 WeakKeyDictionary 挂在 part 上：文档对象被回收时缓存自动消失，
# 不会跨文档串味（part 是不可哈希 lxml 元素之外的原生 Python 对象，可弱引用）。
_STYLE_CACHE = weakref.WeakKeyDictionary()


def _cache_for(part):
    """取（或建）某个 part 的样式缓存；part 不支持弱引用时退回一次性字典。"""
    try:
        cache = _STYLE_CACHE.get(part)
        if cache is None:
            cache = {}
            _STYLE_CACHE[part] = cache
        return cache
    except TypeError:
        return {}


def _style_of(part, style_id, style_type):
    """等价于 ``part.get_style(style_id, style_type)``，但走缓存。

    python-docx 的语义保持原样：``style_id`` 为 None 或查不到、或类型不符时返回
    ``style_type`` 的默认样式（可能为 None）；异常与调用方原来的 try/except 一致。
    """
    cache = _cache_for(part)
    key = ("style", style_id, int(style_type))
    if key in cache:
        return cache[key]
    try:
        value = part.styles.get_by_id(style_id, style_type)
    except Exception:
        value = None
    cache[key] = value
    return value


def _run_style(run):
    """run 的字符样式；等价于 ``run.style``，但走缓存。"""
    try:
        return _style_of(run.part, run._element.style, WD_STYLE_TYPE.CHARACTER)
    except Exception:
        return None


def _paragraph_style(paragraph):
    """段落的段落样式；等价于 ``paragraph.style``，但走缓存。"""
    try:
        return _style_of(paragraph.part, paragraph._p.style, WD_STYLE_TYPE.PARAGRAPH)
    except Exception:
        return None


def _styles_element(part):
    """styles.xml 的根元素；等价于 ``doc.styles.element``，但走缓存。"""
    cache = _cache_for(part)
    key = ("styles_element",)
    if key in cache:
        return cache[key]
    try:
        value = part.styles.element
    except Exception:
        value = None
    cache[key] = value
    return value


def _part_of(item):
    """元素所属的 part；取不到时返回 None（run/paragraph 都有可用的 ``.part``）。"""
    try:
        return item.part
    except Exception:
        return None


def _base_style(style, part=None):
    """等价于 ``style.base_style``，但走缓存。

    **必须显式传 part**：python-docx 的样式对象是 ``StyleFactory`` 造出来的、
    ``_parent`` 为 None，所以 ``style.part`` 会抛
    ``ValueError('part is not accessible from this element')``，而
    ``style.element.getparent()``（styles.xml 根）也没有 ``.part``。
    踩过这个坑：靠 ``style.part`` 取缓存桶 → 每次都进 except 返回 None →
    **整条 basedOn 继承链被截断**，写在样式里的字体/字号/颜色/加粗全部丢失。
    （是 `tests/test_style_cache.py` 里"缓存版必须等于 python-docx 原方法"的断言抓到的。）
    part 缺失时退回 python-docx 原实现：只慢不错。
    """
    if style is None:
        return None
    if part is None:
        try:
            return style.base_style
        except Exception:
            return None
    try:
        base_id = style.element.basedOn_val
        if not base_id:
            return None
        cache = _cache_for(part)
        key = ("base", base_id)
        if key in cache:
            return cache[key]
        styles = _styles_element(part)
        found = styles.get_by_id(base_id) if styles is not None else None
        value = StyleFactory(found) if found is not None else None
        cache[key] = value
        return value
    except Exception:
        return None


def _doc_styles_element(doc):
    """``doc.styles.element`` 的缓存版（doc 为 None 时返回 None）。"""
    if doc is None:
        return None
    try:
        return _styles_element(doc.part)
    except Exception:
        return None


_NS_DRAWING = "http://schemas.openxmlformats.org/drawingml/2006/main"
THEME_FONT_DEFAULTS = {
    "majorHAnsi": "Cambria", "minorHAnsi": "Calibri",
    "majorAscii": "Cambria", "minorAscii": "Calibri",
    "majorEastAsia": "宋体", "minorEastAsia": "宋体",
    "majorBidi": "Times New Roman", "minorBidi": "Arial",
}


def read_theme_fonts(doc):
    try:
        theme_part = next(
            (rel.target_part for rel in doc.part.rels.values()
             if rel.reltype.endswith("/theme")),
            None,
        )
        if theme_part is None:
            return {}
        root = ET.fromstring(theme_part.blob)
        scheme = root.find(f".//{{{_NS_DRAWING}}}fontScheme")
        if scheme is None:
            return {}
        result = {}
        for tag, prefix in (("majorFont", "major"), ("minorFont", "minor")):
            elem = scheme.find(f"{{{_NS_DRAWING}}}{tag}")
            if elem is None:
                continue
            for child, suffixes in (("latin", ("HAnsi", "Ascii")), ("ea", ("EastAsia",))):
                font = elem.find(f"{{{_NS_DRAWING}}}{child}")
                if font is not None and font.get("typeface"):
                    for suffix in suffixes:
                        result[f"{prefix}{suffix}"] = font.get("typeface")
        return result
    except Exception:
        return {}


def read_font_aliases(doc):
    """字体表（word/fontTable.xml）里声明的替换名：字体名 → w:altName。

    例：`<w:font w:name="宋体(中文)"><w:altName w:val="宋体"/>`——
    这份文档自己声明"宋体(中文)"要按"宋体"渲染。
    """
    try:
        part = next(
            (rel.target_part for rel in doc.part.rels.values()
             if rel.reltype.endswith("/fontTable")),
            None,
        )
        if part is None:
            return {}
        root = ET.fromstring(part.blob)
        result = {}
        for font in root.iter(qn("w:font")):
            name = font.get(qn("w:name"))
            alt = font.find(qn("w:altName"))
            value = alt.get(qn("w:val")) if alt is not None else None
            if name and value and name != value:
                result[name] = value
        return result
    except Exception:
        return {}


# 带括注的字体名，如"宋体(中文)""Times New Roman (标题 CS)"。
_ALIAS_QUALIFIER_RE = re.compile(r"^(?P<base>.+?)\s*[（(]\s*[^（()）]+\s*[)）]$")


def canonical_font_name(name, aliases=None):
    """把"带括注、且文档自己声明了它就是括注前那个字体"的名字归一。

    只归一**同时满足**两条的名字：①形如"基础名(括注)"；②字体表把它的 altName
    声明成那个基础名。实测样本里"宋体(中文)"就是这样（本机根本没装这个名字的字体，
    Word/WPS 按 altName 渲染成宋体），不归一会被报成"正文字体不是宋体"（78 条）。

    反过来，"Helvetica→Arial""方正楷体_GB2312→宋体""Open Sans→Times New Roman"
    这类是**字体缺失时的替换建议**，两者是不同的字体：照单归一等于把真错藏掉，
    所以只认"括注名"这一种形态。
    """
    if not name or not aliases:
        return name
    match = _ALIAS_QUALIFIER_RE.match(str(name))
    if not match:
        return name
    base = match.group("base").strip()
    declared = str(aliases.get(name) or "").strip()
    return base if declared and declared == base else name


class DocumentFonts(dict):
    """文档级字体信息：主题字体槽位（dict 本体，键如 majorHAnsi）+ 字体表别名。

    别名单独挂在 `aliases` 上，不混进主题槽位的键空间，`theme_map.get(key)`
    的既有用法一字不改。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.aliases = {}


def read_document_fonts(doc):
    """一次读齐解析需要的字体信息（主题字体 + 字体表别名）。"""
    fonts = DocumentFonts(read_theme_fonts(doc))
    fonts.aliases = read_font_aliases(doc)
    return fonts


def _font_from_rfonts(r_fonts, theme_map=None):
    if r_fonts is None:
        return None, None
    theme_map = theme_map or {}
    ascii_font = r_fonts.get(qn("w:ascii")) or r_fonts.get(qn("w:hAnsi"))
    east_asia = r_fonts.get(qn("w:eastAsia"))
    if not ascii_font:
        key = r_fonts.get(qn("w:asciiTheme")) or r_fonts.get(qn("w:hAnsiTheme"))
        ascii_font = theme_map.get(key) or THEME_FONT_DEFAULTS.get(key)
    if not east_asia:
        key = r_fonts.get(qn("w:eastAsiaTheme"))
        east_asia = theme_map.get(key) or THEME_FONT_DEFAULTS.get(key)
    return ascii_font, east_asia


def _font_from_style(style, theme_map=None, visited=None, part=None):
    if style is None:
        return None, None
    visited = visited or set()
    if id(style) in visited:
        return None, None
    visited.add(id(style))
    try:
        r_pr = style.element.find(qn("w:rPr"))
    except Exception:
        r_pr = None
    if r_pr is not None:
        result = _font_from_rfonts(r_pr.find(qn("w:rFonts")), theme_map)
        if any(result):
            return result
    base = _base_style(style, part)
    return _font_from_style(base, theme_map, visited, part)


def _font_from_paragraph(paragraph, theme_map=None):
    """沿段落样式继承链解析字体。"""
    if paragraph is None:
        return None, None
    return _font_from_style(_paragraph_style(paragraph), theme_map,
                            part=_part_of(paragraph))


def _font_from_defaults(doc, theme_map=None):
    try:
        element = _doc_styles_element(doc)
        defaults = element.find(qn("w:docDefaults")) if element is not None else None
        r_pr_default = defaults.find(qn("w:rPrDefault")) if defaults is not None else None
        r_pr = r_pr_default.find(qn("w:rPr")) if r_pr_default is not None else None
        return _font_from_rfonts(r_pr.find(qn("w:rFonts")), theme_map) if r_pr is not None else (None, None)
    except Exception:
        return None, None


def _size_from_rpr(r_pr):
    """从 rPr 读取 w:sz（半磅值），换算为磅并保留两位小数；无则返回 None。"""
    if r_pr is None:
        return None
    sz = r_pr.find(qn("w:sz"))
    if sz is None:
        return None
    val = sz.get(qn("w:val"))
    try:
        return round(int(val) / 2, 2)
    except (TypeError, ValueError):
        return None


def _font_size_from_style(style, visited=None, part=None):
    """沿样式继承链解析字号（与字体名的 _font_from_style 对称）。"""
    if style is None:
        return None
    visited = visited or set()
    if id(style) in visited:
        return None
    visited.add(id(style))
    try:
        r_pr = style.element.find(qn("w:rPr"))
    except Exception:
        r_pr = None
    size = _size_from_rpr(r_pr)
    if size is not None:
        return size
    return _font_size_from_style(_base_style(style, part), visited, part)


def _font_size_from_defaults(doc):
    try:
        element = _doc_styles_element(doc)
        defaults = element.find(qn("w:docDefaults")) if element is not None else None
        r_pr_default = defaults.find(qn("w:rPrDefault")) if defaults is not None else None
        r_pr = r_pr_default.find(qn("w:rPr")) if r_pr_default is not None else None
        return _size_from_rpr(r_pr)
    except Exception:
        return None


def _font_size_from_paragraph(paragraph):
    """沿段落样式继承链解析字号。"""
    if paragraph is None:
        return None
    return _font_size_from_style(_paragraph_style(paragraph),
                                 part=_part_of(paragraph))


def read_run_fonts(run, doc=None, theme_map=None, paragraph=None):
    r_pr = run._element.rPr
    ascii_font, east_asia = _font_from_rfonts(
        r_pr.find(qn("w:rFonts")) if r_pr is not None else None,
        theme_map,
    )
    style_fonts = _font_from_style(_run_style(run), theme_map,
                                   part=_part_of(run))
    ascii_font = ascii_font or style_fonts[0]
    east_asia = east_asia or style_fonts[1]
    if not ascii_font or not east_asia:
        para_fonts = _font_from_paragraph(paragraph, theme_map)
        ascii_font = ascii_font or para_fonts[0]
        east_asia = east_asia or para_fonts[1]
    if doc is not None and (not ascii_font or not east_asia):
        defaults = _font_from_defaults(doc, theme_map)
        ascii_font = ascii_font or defaults[0]
        east_asia = east_asia or defaults[1]
    aliases = getattr(theme_map, "aliases", None)
    return (canonical_font_name(ascii_font, aliases),
            canonical_font_name(east_asia, aliases))


def read_char_spacing_pt(run):
    r_pr = run._element.rPr
    spacing = r_pr.find(qn("w:spacing")) if r_pr is not None else None
    value = spacing.get(qn("w:val")) if spacing is not None else None
    try:
        return round(int(value) / 20, 2) if value is not None else None
    except (TypeError, ValueError):
        return None


def _color_from_rpr(r_pr):
    """从 rPr 元素读 w:color/@w:val（十六进制），主题色不解析。"""
    if r_pr is None:
        return None
    color = r_pr.find(qn("w:color"))
    if color is None:
        return None
    val = color.get(qn("w:val"))
    if not val or val.lower() in ("auto", "000000"):
        return None
    return val.upper()


def _color_from_style_chain(style, part=None):
    """沿样式 basedOn 链找颜色。"""
    seen = set()
    while style is not None and style.style_id not in seen:
        seen.add(style.style_id)
        try:
            r_pr = style.element.find(qn("w:rPr"))
        except Exception:
            r_pr = None
        c = _color_from_rpr(r_pr)
        if c:
            return c
        style = _base_style(style, part)
    return None


def _color_from_defaults(doc):
    """docDefaults / rPrDefault / rPr / color。"""
    try:
        el = _doc_styles_element(doc)
        rpr_default = el.find(qn("w:docDefaults") + "/" + qn("w:rPrDefault")) \
            if el is not None else None
        r_pr = rpr_default.find(qn("w:rPr")) if rpr_default is not None else None
        return _color_from_rpr(r_pr)
    except Exception:
        return None


def read_run_color(run, doc=None, paragraph=None):
    """run 颜色四级回退：run 直接 -> 字符样式链 -> 段落样式链 -> docDefaults。"""
    c = _color_from_rpr(run._element.rPr)
    if c:
        return c
    c = _color_from_style_chain(_run_style(run), _part_of(run))
    if c:
        return c
    if paragraph is not None:
        c = _color_from_style_chain(_paragraph_style(paragraph),
                                    _part_of(paragraph))
    if c:
        return c
    if doc is not None:
        c = _color_from_defaults(doc)
    return c


def _bool_from_rpr(r_pr, tag):
    """从 rPr 读 w:b / w:i 三态：True / False / None（本层未指定）。"""
    if r_pr is None:
        return None
    el = r_pr.find(qn(tag))
    if el is None:
        return None
    val = el.get(qn("w:val"))
    if val is None:
        return True
    return val.strip().lower() not in ("0", "false", "off")


def _bool_from_style_chain(style, tag, part=None):
    """沿样式 basedOn 链读 w:b / w:i。"""
    seen = set()
    while style is not None:
        try:
            style_id = style.style_id
        except Exception:
            break
        if style_id in seen:
            break
        seen.add(style_id)
        try:
            r_pr = style.element.find(qn("w:rPr"))
        except Exception:
            r_pr = None
        value = _bool_from_rpr(r_pr, tag)
        if value is not None:
            return value
        style = _base_style(style, part)
    return None


def _bool_from_defaults(doc, tag):
    try:
        element = _doc_styles_element(doc)
        defaults = element.find(qn("w:docDefaults")) if element is not None else None
        r_pr_default = defaults.find(qn("w:rPrDefault")) if defaults is not None else None
        r_pr = r_pr_default.find(qn("w:rPr")) if r_pr_default is not None else None
    except Exception:
        return None
    return _bool_from_rpr(r_pr, tag)


def read_run_bool(run, tag, doc=None, paragraph=None):
    """run 加粗/倾斜的四级回退：run 直接 -> 字符样式链 -> 段落样式链 -> docDefaults。

    全链都未显式设置时返回 False：Word 的默认渲染值就是“不加粗、不倾斜”，
    这样既不会把“写在样式里的加粗”误报成未加粗，也不会因为缺省而漏检。
    """
    value = _bool_from_rpr(run._element.rPr, tag)
    if value is None:
        value = _bool_from_style_chain(_run_style(run), tag, _part_of(run))
    if value is None and paragraph is not None:
        value = _bool_from_style_chain(_paragraph_style(paragraph), tag,
                                       _part_of(paragraph))
    if value is None and doc is not None:
        value = _bool_from_defaults(doc, tag)
    return False if value is None else value


def read_run(run, doc=None, theme_map=None, paragraph=None):
    ascii_font, east_asia = read_run_fonts(run, doc, theme_map, paragraph)
    # 字号四级回退（与 Word 实际渲染优先级一致）：
    # run 直接值 -> 字符样式链 -> 段落样式链 -> docDefaults，
    # 避免“字号写在样式里”被漏检。
    size_pt = run.font.size.pt if run.font.size is not None else None
    if size_pt is None:
        size_pt = _font_size_from_style(_run_style(run), part=_part_of(run))
    if size_pt is None:
        size_pt = _font_size_from_paragraph(paragraph)
    if size_pt is None and doc is not None:
        size_pt = _font_size_from_defaults(doc)
    return {
        "text": run.text or "",
        "font_name": ascii_font,
        "east_asia_font": east_asia,
        "font_size_pt": size_pt,
        "bold": read_run_bool(run, "w:b", doc, paragraph),
        "italic": read_run_bool(run, "w:i", doc, paragraph),
        "char_spacing_pt": read_char_spacing_pt(run),
        # 标题颜色修复：run 颜色四级回退（run 直接->字符样式->段落样式->docDefaults）
        "color_hex": read_run_color(run, doc, paragraph),
    }


def _int_attr(el, tag):
    raw = el.get(qn(tag))
    if raw is None:
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _pPr_from_defaults(doc):
    try:
        element = _doc_styles_element(doc)
        defaults = element.find(qn("w:docDefaults")) if element is not None else None
        p_pr_default = defaults.find(qn("w:pPrDefault")) if defaults is not None else None
        return p_pr_default.find(qn("w:pPr")) if p_pr_default is not None else None
    except Exception:
        return None


def _paragraph_pPr_chain(para, doc=None):
    """段落属性查找顺序：段落直接 -> 段落样式链 -> docDefaults/pPrDefault。

    与 run 的四级回退对称，避免“对齐/行距/缩进写在样式里”被当成未设置，
    从而既漏检（None 直接判合规）又把继承来的居中/左对齐报成两端对齐。
    """
    chain = []
    if para is not None and para._p.pPr is not None:
        chain.append(para._p.pPr)
    style = _paragraph_style(para) if para is not None else None
    part = _part_of(para)
    seen = set()
    while style is not None:
        try:
            style_id = style.style_id
        except Exception:
            break
        if style_id in seen:
            break
        seen.add(style_id)
        try:
            p_pr = style.element.find(qn("w:pPr"))
        except Exception:
            p_pr = None
        if p_pr is not None:
            chain.append(p_pr)
        style = _base_style(style, part)
    if doc is not None:
        p_pr = _pPr_from_defaults(doc)
        if p_pr is not None:
            chain.append(p_pr)
    return chain


def _alignment_from_chain(chain):
    for p_pr in chain:
        jc = p_pr.find(qn("w:jc"))
        if jc is None:
            continue
        try:
            mapped = from_jc(jc.get(qn("w:val")))
        except UnsupportedAlignment:
            # 契约不认识的取值按未知处理，不能让无法校验的值流进 JSON；
            # 同时停止向上回退：本层已经显式写了 Word 认、契约不认的对齐。
            return None
        if mapped is None:
            continue
        return mapped
    return None


def _paragraph_font_size(para, doc=None):
    """段落字号：首个 run 的直接值 -> 段落样式链 -> docDefaults -> 12pt（小四）。"""
    try:
        for run in para.runs:
            if run.font.size is not None:
                return run.font.size.pt
    except Exception:
        pass
    size = _font_size_from_paragraph(para)
    if size is None and doc is not None:
        size = _font_size_from_defaults(doc)
    return size if size is not None else 12.0


def _indent_value(ind, chars_attr, twips_attr, size_pt, negative=False):
    """按 Word 语义取一个缩进值；该 ind 里没写这两个属性时返回 None。

    - 字符单位非 0 → 用字符单位换算（WPS/中文模板常用）；
    - 字符单位写了 0、且 twips 孪生值非 0 → 用 twips（`w:left="420" w:leftChars="0"`
      这种写法里 0 只是"没填字符单位"，真实值是 420 twips = 21 pt）；
    - 字符单位写了 0、又没有 twips → **显式置 0**，到此为止，不再往下继承样式
      （实测张权辉的参考文献条目就是只写 `w:firstLineChars="0"`，若继续继承样式
      的 21 pt 会凭空多报 151 条"未左顶格"）。
    """
    chars = _int_attr(ind, "w:" + chars_attr)
    twips = _int_attr(ind, "w:" + twips_attr)
    if chars is not None:
        if chars:
            value = round(chars / 100.0 * (size_pt or 12.0), 2)
            return -value if negative else value
        if twips:
            value = round(twips / 20.0, 2)
            return -value if negative else value
        return 0.0
    if twips is not None:
        value = round(twips / 20.0, 2)
        return -value if negative else value
    return None


def _indent_from_chain(chain, size_pt):
    """首行缩进（磅）。悬挂缩进按 python-docx 口径返回负值。

    w:firstLineChars / w:hangingChars 以 1/100 字符为单位，需要按字号换算；
    WPS 与中文模板常用字符单位，只读 w:firstLine 会整段漏检。

    2026-10-12（Word 实测三例后修订）：
    - 元素内悬挂优先于 firstLineChars=0：蒙博条目 `left=425 hanging=425
      firstLineChars=0` → Word First=-21.25（悬挂生效）；
    - 只写 `firstLineChars="0"` 的“弱零”不阻断（编号/样式）低层的**悬挂**：
      张权辉条目 `direct[firstLineChars=0] + 编号[left=420 hanging=420]`
      → Word First=-21（[3] 顶格，实测 Left=21/First=-21）；
    - 但它仍阻断低层的**非零首行值**：张权辉样式链的 +21 不得进量（曾凭空
      多报 151 条“未左顶格”）。故弱零记下后继续找“悬挂”，找不到悬挂就
      返回 0、不再向样式链继承首行值。
    - 写了 twips 的 `w:firstLine="0"` 是“强零”，立即生效（暂无实测样例，
      沿用原行为；见 test_direct_format_overrides_numbering_layer）。
    """
    zero_first = None
    for p_pr in chain:
        ind = p_pr.find(qn("w:ind"))
        if ind is None:
            continue
        hanging = _indent_value(ind, "hangingChars", "hanging", size_pt, negative=True)
        if hanging is not None:
            return hanging
        first = _indent_value(ind, "firstLineChars", "firstLine", size_pt)
        if first is None:
            continue
        if first != 0:
            return zero_first if zero_first is not None else first
        if _int_attr(ind, "w:firstLine") is not None:
            return 0.0
        if zero_first is None:
            zero_first = first
    return zero_first


def _left_indent_from_chain(chain, size_pt):
    """段落左侧缩进（磅）。用来算“这一段从哪里开始排”。

    审核版式位置（“续表与表格左边线对齐”）需要它，只读首行缩进区分不出“整段往右挪了”。
    单位换算与显式 0 的语义都走 _indent_value：段自身写了 0（chars 或 twips）就是 0，
    到此为止、不再往上继承样式——直接格式覆盖样式，与首行缩进同一口径。WPS 文档
    常给段落写 `w:leftChars="0" w:left="0"` 这类显式零，继续回退会把真实不缩进的
    段落读成样式里的缩进值（曾有一个“零视同未设置”的副本遮蔽本实现，2026-10-12
    收敛为一份，不要再改回“零则继续回退”）。
    """
    for p_pr in chain:
        ind = p_pr.find(qn("w:ind"))
        if ind is None:
            continue
        left = _indent_value(ind, "leftChars", "left", size_pt)
        if left is None:
            left = _indent_value(ind, "startChars", "start", size_pt)
        if left is not None:
            return left
    return None


def _line_spacing_from_chain(chain):
    """返回 (倍数, 固定磅值)。倍数行距 lineRule=auto，固定/最小值按磅返回。"""
    for p_pr in chain:
        spacing = p_pr.find(qn("w:spacing"))
        if spacing is None:
            continue
        line = _int_attr(spacing, "w:line")
        if line is None:
            continue
        rule = (spacing.get(qn("w:lineRule")) or "auto").strip().lower()
        if rule == "auto":
            return round(line / 240.0, 2), None
        return None, round(line / 20.0, 2)
    return None, None


def _space_from_chain(chain, tag):
    """段前/段后间距（磅）。全链都没写时按 Word 默认 0 磅返回。"""
    for p_pr in chain:
        spacing = p_pr.find(qn("w:spacing"))
        if spacing is None:
            continue
        val = _int_attr(spacing, tag)
        if val is not None:
            return round(val / 20.0, 2)
    return 0.0


def read_auto_number(para, doc=None):
    """段落是否使用 Word 自动编号（编号由 numbering.xml 生成，不在段落文本里）。

    官方模板的标题就用自动编号（`<w:numPr><w:ilvl w:val="2"/><w:numId w:val="1"/>`），
    段落文本里没有“1.1.1”——判定层必须能把它和“压根没写编号”区分开，否则
    “标题编号格式”这类看文本的检查会把模板自己的标题报成编号错。
    numId 写在段落上或样式链上都算；`<w:numId w:val="0"/>` 是取消编号，不算。
    """
    for p_pr in _paragraph_pPr_chain(para, doc):
        num_pr = p_pr.find(qn("w:numPr"))
        if num_pr is None:
            continue
        num_id = num_pr.find(qn("w:numId"))
        if num_id is None:
            return True
        return _int_attr(num_id, "w:val") != 0
    return False


def read_numbering_ref(para, doc=None):
    """段落使用的自动编号坐标 `(numId, ilvl)`；没有自动编号时返回 None。

    `read_auto_number` 只回答“有没有编号”，这里回答“用的是哪套编号的哪一级”：
    numbering.xml 里同一 numId 的不同 ilvl 是不同格式（`[%1]` 与 `%1.` 之类），
    要把编号还原成可见文字（见 parser.numbering）必须拿到 ilvl。
    """
    for p_pr in _paragraph_pPr_chain(para, doc):
        num_pr = p_pr.find(qn("w:numPr"))
        if num_pr is None:
            continue
        num_id = num_pr.find(qn("w:numId"))
        if num_id is None:
            return None
        value = _int_attr(num_id, "w:val")
        if not value:
            return None
        ilvl = num_pr.find(qn("w:ilvl"))
        return value, (_int_attr(ilvl, "w:val") or 0)
    return None


def _numbering_definitions(doc):
    """文档的 numbering.xml 查表；按 part 缓存（与样式缓存同一机制）。"""
    part = _part_of(doc)
    if part is None:
        return None
    cache = _cache_for(part)
    key = ("numbering_defs",)
    if key not in cache:
        try:
            cache[key] = NumberingDefinitions(doc)
        except Exception:
            cache[key] = None
    return cache[key]


def _numbering_level_ppr(para, doc=None):
    """段落所用编号级别的 pPr 元素；没有自动编号/还原不出时返回 None。"""
    if para is None or doc is None:
        return None
    try:
        ref = read_numbering_ref(para, doc)
        if ref is None:
            return None
        definitions = _numbering_definitions(doc)
        if definitions is None:
            return None
        return definitions.level_ppr(*ref)
    except Exception:
        return None


def _chain_with_numbering(chain, own_ppr, num_ppr):
    """把编号级别的 pPr 插到“段落直接格式”与“段落样式链”之间。

    Word 的段落格式层顺序：直接格式 > 编号级别（w:numPr 指向的 w:lvl/pPr）>
    段落样式链 > docDefaults。chain[0] 是段落自己的 pPr 时插在它后面，
    否则（直接格式没写任何东西）插在样式链前面。
    """
    if own_ppr is not None and chain and chain[0] is own_ppr:
        return [chain[0], num_ppr, *chain[1:]]
    return [num_ppr, *chain]


def _has_center_tab_stop(chain):
    """有效段落属性里是否定义了居中制表位（w:tab w:val="center"）。"""
    for p_pr in chain:
        tabs = p_pr.find(qn("w:tabs"))
        if tabs is None:
            continue
        for tab in tabs.findall(qn("w:tab")):
            if (tab.get(qn("w:val")) or "").strip().lower() == "center":
                return True
    return False


def read_explicit_alignment(para, doc=None):
    """段落显式设置的对齐（含样式链）；未设置时返回 None。

    与 read_paragraph_style 的区别：这里不按“Word 默认左对齐”兜底。
    写回层据此区分“原文就是左对齐”与“原文没写 w:jc”，
    避免重建时给每个单元格硬塞一个虚构的居中/左对齐。
    """
    if para is None:
        return None
    return _alignment_from_chain(_paragraph_pPr_chain(para, doc))


def read_paragraph_style(para, doc=None):
    chain = _paragraph_pPr_chain(para, doc)
    # 编号级别自带的段落格式（numPr 指向的 w:lvl 里的 w:pPr）插到“直接格式”与
    # “段落样式”之间——Word 里这一层介于两者之间：缩进（挂起）由编号级别给出，
    # 段落直接写 w:ind 才覆盖它。不插进来会把“编号+悬挂”的参考文献列表按
    # 样式链读成 60pt“未左顶格”（实测 5 份样张 100 条误报）。
    num_ppr = _numbering_level_ppr(para, doc)
    if num_ppr is not None:
        own_ppr = None
        if para is not None:
            own_ppr = para._p.pPr
        chain = _chain_with_numbering(chain, own_ppr, num_ppr)
    align = _alignment_from_chain(chain)
    if align is None:
        # OOXML 未指定 w:jc 时按左对齐渲染（Word 默认段落对齐）
        align = "left"
    size_pt = _paragraph_font_size(para, doc)
    first = _indent_from_chain(chain, size_pt)
    if first is None and align in ("center", "right"):
        first = 0.0
    multiple, fixed_pt = _line_spacing_from_chain(chain)
    return {
        "alignment": align,
        "first_line_indent_pt": first,
        "left_indent_pt": _left_indent_from_chain(chain, size_pt),
        "line_spacing_multiple": multiple,
        "line_spacing_pt": fixed_pt,
        "space_before_pt": _space_from_chain(chain, "w:before"),
        "space_after_pt": _space_from_chain(chain, "w:after"),
        # 有效段落属性里带“居中制表位”（含样式链里定义）：WPS 公式段落的
        # “公式居中、编号行末”就靠它（见 detection/rules/formula 的 alignment）。
        "center_tab": _has_center_tab_stop(chain),
    }
