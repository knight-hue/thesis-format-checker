# -*- coding: utf-8 -*-
"""公式段落识别。

论文里的公式主要有三种形态：

1. Word 原生公式（OMML，``<m:oMath>``）：公式本体是数学标记，``paragraph.text``
   取不到公式内容，只能拿到段内普通文字（通常是编号）；
2. MathType / 公式编辑器 OLE 对象（``<w:object>``）：本质是嵌入图片，
   文本不可读，只能识别“这里是公式”；
3. 纯文本公式：普通文字写成的公式（如 ``E = mc^2  (1-1)``）。

本模块把“是不是公式”的判断集中在一处，供解析层、规则引擎和写回层共用，
保证三处口径一致。
"""
import re

# W3: reuse media CONTENT_TYPE_EXT for asset extension inference.
from .media import CONTENT_TYPE_EXT

# 公式编号：(1-1) / （1-1），阿拉伯数字，章-序号。
# 连字符两侧允许空白与全角连字符：学生写成“（ 4 - 3 ）”也是公式的编号，
# 认不出来整段就会掉进正文分支，被按正文的对齐/缩进判错（真误报）。
FORMULA_NUM_RE = re.compile(r"[（(]\s*(\d+)\s*[-－]\s*(\d+)\s*[）)]")

# 纯文本公式的保守识别：含等号 + 含编号 + 段落足够短。
_PLAIN_FORMULA_RE = re.compile(r"=")

_PLAIN_MAX_LEN = 100

# 公式与编号之间的虚线（应禁止）：点、间隔号、省略号、⋯（U+22EF）连续 3 个以上。
DOTTED_RE = re.compile(r"[.·…⋯]{3,}")

# 判定“公式与编号之间”的虚线时，只看编号紧邻的这一小段：虚线是紧贴编号的前导符，
# 而公式内部的省略号（如 a₁+a₂+⋯+aₙ=S）隔着算式，不该算成“公式与编号之间”。
_DOTTED_WINDOW = 12

# 句末标点：以它收尾的短句不可能是公式本体（如"其中系数 a = 3。"）。
_SENTENCE_END_RE = re.compile(r"[。；，：、！？;,!?]$")


def is_formula_number_line(text):
    """整段只有公式编号（如 ``(2-1)``）时返回该编号，否则 None。"""
    stripped = (text or "").strip()
    if not stripped:
        return None
    match = FORMULA_NUM_RE.fullmatch(stripped)
    return match.group(0) if match else None


def is_plain_formula_body(text):
    """纯文本公式本体（本段不含编号）：短、含等号、不以句末标点收尾。

    "公式独占一行、编号另起一行"的排版里，公式行本身没有编号，
    ``detect_formula`` 认不出来，只能靠后一行是不是纯编号来确认。
    """
    stripped = (text or "").strip()
    if not stripped or len(stripped) >= _PLAIN_MAX_LEN:
        return False
    if not _PLAIN_FORMULA_RE.search(stripped):
        return False
    return not _SENTENCE_END_RE.search(stripped)


def _iter_omml_text(paragraph_element):
    """拼接 OMML 公式里的 ``<m:t>`` 文本，用于展示与粗略判断。"""
    from docx.oxml.ns import qn
    parts = []
    for element in paragraph_element.iter(qn("m:t")):
        if element.text:
            parts.append(element.text)
    return "".join(parts)


def extract_formula_number(text):
    """从文本中提取公式编号，如 ``(1-1)``；没有则返回 None。"""
    match = FORMULA_NUM_RE.search(text or "")
    return match.group(0) if match else None


def _serialize_xml(element):
    """把 XML 元素序列化成自包含字符串，供重建链路原样插回。"""
    try:
        xml = element.xml
        if xml:
            return xml
    except Exception:
        pass
    from lxml import etree
    return etree.tostring(element, encoding="unicode")


def _ext_for_part(part):
    """按 content_type 推断扩展名；失败时按部件名后缀，最后兜底 .bin。"""
    ext = CONTENT_TYPE_EXT.get(part.content_type)
    if ext:
        return ext
    try:
        name = str(part.partname)
        base = name.rsplit("/", 1)[-1]
        if "." in base:
            return "." + base.rsplit(".", 1)[-1]
    except Exception:
        pass
    return ".bin"


def _collect_formula_assets(element, paragraph):
    """扫描公式 XML 里所有 r:id / r:embed / r:link 引用，收集部件信息。

    返回形如 [{"old_rid", "content_type", "ext", "blob"}] 的列表；
    blob 是内存字节，docx_to_json 负责落盘后替换成 path 字段。
    写回层据此把旧 rId 重绑到新文档，修复 W3 公式对象引用断裂。
    """
    from docx.oxml.ns import qn
    assets = []
    seen = set()
    try:
        part = paragraph.part
    except Exception:
        return assets
    for node in element.iter():
        for attr in (qn("r:id"), qn("r:embed"), qn("r:link")):
            rid = node.get(attr)
            if not rid or rid in seen:
                continue
            try:
                target = part.related_parts[rid]
            except (KeyError, AttributeError):
                continue
            seen.add(rid)
            assets.append({
                "old_rid": rid,
                "content_type": target.content_type,
                "ext": _ext_for_part(target),
                "blob": target.blob,
            })
    return assets


def detect_formula(paragraph):
    """识别公式段落。

    返回 dict（含 formula_type / formula_text / formula_number / formula_xml）
    或 None。formula_xml 保存 OMML / OLE 对象的原始 XML，重建 docx 时原样插回。
    """
    from docx.oxml.ns import qn

    element = paragraph._p
    text = paragraph.text or ""
    stripped = text.strip()

    has_math = False
    has_ole = False
    formula_xml = []
    formula_assets = []
    for child in element.iter():
        if child.tag == qn("m:oMath"):
            has_math = True
            formula_xml.append(_serialize_xml(child))
            # W3：OMML 公式里也可能引用图片（r:embed），一并收集。
            formula_assets.extend(_collect_formula_assets(child, paragraph))
        elif child.tag == qn("w:object"):
            has_ole = True
            formula_xml.append(_serialize_xml(child))
            # W3：MathType OLE 对象含 imagedata(r:id) 与 OLEObject(r:id)，
            #     都是原文档相对 ID，重建时必须重绑。
            formula_assets.extend(_collect_formula_assets(child, paragraph))

    number = extract_formula_number(stripped)

    if has_math:
        return {
            "formula_type": "omml",
            "formula_text": _iter_omml_text(element),
            "formula_number": number,
            "formula_xml": formula_xml,
            # W3：公式引用的部件（预览图 / OLE 二进制），写回时用于 r:id 重绑。
            "formula_assets": formula_assets,
        }

    if has_ole:
        return {
            "formula_type": "ole",
            "formula_text": "",
            "formula_number": number,
            "formula_xml": formula_xml,
            # W3：OLE 预览图（imagedata）与 OLEObject 的 r:id 一并重绑。
            "formula_assets": formula_assets,
        }

    # 纯文本公式：含等号、含编号、段落短，避免把正文误判成公式。
    if (
        stripped
        and len(stripped) < _PLAIN_MAX_LEN
        and _PLAIN_FORMULA_RE.search(stripped)
        and number
    ):
        return {
            "formula_type": "text",
            "formula_text": _strip_number(stripped, number),
            "formula_number": number,
        }

    return None


def _strip_number(text, number):
    """去掉文本里的编号，返回公式本体（保留首尾空白之外的原文）。"""
    idx = text.find(number)
    if idx == -1:
        return text
    return (text[:idx] + text[idx + len(number):]).strip()


def _dotted_span(text, number):
    """编号紧邻处那段虚线的位置区间；不在编号旁边就不算“公式与编号之间”。

    :return: ``(start, end)`` 或 None
    """
    if not text or not number:
        return None
    pos = text.find(number)
    if pos < 0:
        return None
    before = text[:pos].rstrip()
    base = max(0, len(before) - _DOTTED_WINDOW)
    match = DOTTED_RE.search(before[base:])
    if match:
        return base + match.start(), base + match.end()
    rest = text[pos + len(number):]
    lead = len(rest) - len(rest.lstrip())
    match = DOTTED_RE.search(rest[lead:lead + _DOTTED_WINDOW])
    if match:
        origin = pos + len(number) + lead
        return origin + match.start(), origin + match.end()
    return None


def dotted_between_formula_and_number(text, number):
    """公式与编号之间是否出现虚线（§6.1：不加虚线）。"""
    return _dotted_span(text, number) is not None


def strip_dotted_between_formula_and_number(text, number):
    """去掉公式与编号之间的虚线；没有则原样返回（供写回层使用）。"""
    span = _dotted_span(text, number)
    if span is None:
        return text
    return text[:span[0]] + text[span[1]:]
