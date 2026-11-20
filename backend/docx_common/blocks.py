# -*- coding: utf-8 -*-
"""正文块级元素的统一遍历口径：下钻块级包装元素。

python-docx 的 ``Document.paragraphs`` / ``Document.tables`` 只返回 ``w:body``
的直接子级，被块级包装包住的段落/表格整块看不见。Word「引用→目录→自动目录」
插入的目录就带内容控件：

    <w:sdt><w:sdtPr><w:docPartObj><w:docPartGallery w:val="Table of Contents"/>
    </w:docPartObj></w:sdtPr><w:sdtContent>…目录条目…</w:sdtContent></w:sdt>

解析层若只遍历直接子级，目录（连“目录”二字标题）在解析结果里根本不存在，
检测侧于是报“未检测到目录”——文档明明有目录。修订插入（``w:ins``）、
旧版 ``w:customXml``、``w:smartTag`` 同理，会让整段内容既不被检查（漏检）
又不会被写回层定位到。段内 run 也有同样的包装（行内内容控件、修订插入的片段），
那部分文字在 ``CT_P.text`` 里同样看不到，故 run 流（``docx_common/runs.py``）也要下钻。

解析层与写回层必须用同一口径，否则块下标会整体错位、写回拒绝执行。
"""
from docx.oxml.ns import qn

# 契约里的块级元素
BLOCK_TAGS = {qn("w:p"), qn("w:tbl")}

# 块级包装：能包住整段/整表，其内容属于正文，必须下钻。
# 值是"装着块的容器"在包装元素里的位置（``w:sdt`` 的正文在 ``w:sdtContent`` 里，
# ``w:sdtPr`` 只是控件属性、不产出块）；``None`` 表示包装元素自身就是容器。
_WRAPPER_CONTENT = {
    qn("w:sdt"): (qn("w:sdtContent"),),
    qn("w:ins"): None,
    qn("w:customXml"): None,
    qn("w:smartTag"): None,
    qn("w:moveTo"): None,
}

# 修订删除的内容（``w:del`` / ``w:moveFrom``）：文档里已经看不到这些文字，
# 既不检查也不进契约，与旧行为一致。
_DELETED_TAGS = {qn("w:del"), qn("w:moveFrom")}


def wrapper_containers(element):
    """包装元素里"装着内容"的容器；不是包装元素时返回空元组。

    包装元素既可能包住整段/整表（``w:body`` 级的内容控件），也可能包住段内
    run（行内控件、修订插入的片段），解析层两处都要下钻，故由本模块统一给出，
    避免两个模块各写一份白名单。
    """
    if element.tag not in _WRAPPER_CONTENT:
        return ()
    tags = _WRAPPER_CONTENT[element.tag]
    if tags is None:
        return (element,)
    return tuple(node for node in element if node.tag in tags)


def _textbox_contents(paragraph):
    """段落里内嵌的文本框内容容器（``w:txbxContent``）。

    VML（``w:pict``→``v:textbox``）与 DrawingML（``w:drawing``→``wps:txbx``）
    两种画法都落在 ``w:txbxContent`` 上。文本框里的段落完全不在 ``w:body`` 里，
    按普通段落那样遍历是看不到的（目录整块放在文本框里时，检测会报“未检测到目录”）。
    """
    return list(paragraph.iter(qn("w:txbxContent")))


def is_inside_textbox(element):
    """元素是否位于图形内部的文本框里（祖先含 ``w:txbxContent``）。

    图内文字属于“图”：模板对它的要求是「图内字体字号：宋体五号字」，
    与正文/标题规则不同。解析层据此给块打 ``figure_text``，检测层照此分派。
    """
    node = element.getparent()
    while node is not None:
        if node.tag == qn("w:txbxContent"):
            return True
        node = node.getparent()
    return False


def iter_block_elements(container):
    """按文档顺序产出容器下的块级元素（``w:p`` / ``w:tbl``），下钻块级包装。

    参数是容器的 XML 元素（``w:body``、``w:sdtContent`` 或 ``w:ins`` 等）。
    包装内若还有包装（如内容控件里带修订插入）会逐层展开，顺序与文档一致；
    段落内嵌文本框的内容排在该段落之后（文本框的定位段落在前）。
    """
    for child in container:
        if child.tag in BLOCK_TAGS:
            yield child
            if child.tag == qn("w:p"):
                for inner in _textbox_contents(child):
                    yield from iter_block_elements(inner)
        elif child.tag in _DELETED_TAGS:
            continue
        else:
            for inner in wrapper_containers(child):
                yield from iter_block_elements(inner)
