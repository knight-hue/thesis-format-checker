# -*- coding: utf-8 -*-
"""结构识别可疑信号：把"解析层认出了什么"折成几条给人看的告警（只读，不参与判定）。

异校模板里某个章节锚点没被认出来时，系统**一声不响**——学生看到的是"标题类问题一条
没有、正文类问题暴增"，看不出根因是"这一段没被认成标题"。这里把最该人工核对的情况
列出来，由服务端解析后写进日志（见 `api._log_structure_warnings`）。

刻意不做的事：不改 `kind`、不产生 issue、不进解析契约——告警不参与判定，多了少了都
不会改变报告里的条数与聚合。所以要判断某条告警是否成立，看的是识别结果本身。
"""
import re

from .classification import anchor_kind_of
from .context import _REGION_ANCHORS
from .structure import DEFAULT_STRUCTURE, StructureSpec

_HEADING_KINDS = ("heading_1", "heading_2", "heading_3")

# 像声明页/授权书的字眼：前置区里带这些词、又被认成"中文题目"的段落是高危信号
# （真正的中文题目会被它挤掉）。"诚信/信用"这类词也可能是真实题目
# （"基于X的诚信评价系统"），故不列出，避免天天误报。
_DECLARATION_HINTS = ("声明", "承诺", "授权", "独创性")

_WS_RE = re.compile(r"\s+")


def _compact(text):
    """去全部空白：模板里"独 创 性 声 明"这种加空格的写法也要认。"""
    return _WS_RE.sub("", text or "")


def _front_matter_cutoff(blocks):
    """第一个章节锚点或标题出现的位置：它之前算前置区（封面与声明页）。"""
    for position, block in enumerate(blocks):
        if block.get("type") != "paragraph":
            continue
        if block.get("kind") in _REGION_ANCHORS or block.get("kind") in _HEADING_KINDS:
            return position
    return len(blocks)


def structure_warnings(blocks, spec=None):
    """返回可疑信号的中文说明；空列表 = 没发现可疑。纯读取，不修改 blocks。"""
    spec = spec or StructureSpec(DEFAULT_STRUCTURE)
    paragraphs = [(i, b) for i, b in enumerate(blocks) if b.get("type") == "paragraph"]

    anchors, toc_entries, toc_title = set(), 0, False
    for _, block in paragraphs:
        kind = block.get("kind")
        if kind not in _REGION_ANCHORS:
            continue
        anchors.add(kind)
        # 目录标题与目录条目同为 "toc"：文字能查回锚点的是标题，其余算条目。
        # 只有条目、没有标题时目录区没法按目录的要求判，条目会被当成正文逐条量。
        if kind == "toc":
            if anchor_kind_of(block.get("text"), spec) == "toc":
                toc_title = True
            else:
                toc_entries += 1

    cutoff = _front_matter_cutoff(blocks)
    front_titles = [_compact(b.get("text")) for i, b in paragraphs
                    if i < cutoff and b.get("kind") == "title" and _compact(b.get("text"))]
    front_bodies = sum(1 for i, b in paragraphs
                       if i < cutoff and b.get("kind") == "body" and _compact(b.get("text")))

    warnings = []
    if not anchors:
        warnings.append("没有认出任何章节锚点（摘要/目录/参考文献/致谢一个都没有）："
                        "这份文档可能不是该模板的写法，或模板的标题另有写法")
    if not anchors & {"abstract_title", "en_abstract_title"}:
        warnings.append("没有认出摘要标题（abstract_title）")
    if "reference_title" not in anchors:
        warnings.append("没有认出参考文献标题（reference_title）")
    if "acknowledgement_title" not in anchors:
        warnings.append("没有认出致谢标题（acknowledgement_title）")
    if not any(b.get("kind") in _HEADING_KINDS for _, b in paragraphs):
        warnings.append("没有认出任何一、二、三级标题："
                        "标题可能是别的写法，或没套 Word 标题样式")
    for text in front_titles:
        if any(word in text for word in _DECLARATION_HINTS):
            warnings.append("前置区这段像声明页/授权书却被认成中文题目：%s"
                            "（该院声明页的叫法没配到结构识别表？真正的中文题目会被它挤掉）"
                            % text[:30])
    if front_bodies:
        warnings.append("前置区里有 %d 段被判成正文：这些段落会按正文规则逐条量，"
                        "请核对是不是封面或声明页内容" % front_bodies)
    if toc_entries and not toc_title:
        warnings.append("有目录条目但没认出目录标题")
    return warnings
