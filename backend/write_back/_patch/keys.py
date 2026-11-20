# -*- coding: utf-8 -*-

"""契约字段白名单与 OOXML 常量：写回层判断"这个字段认不认识"的依据。"""

from docx.oxml.ns import qn


PARA_FORMAT = {
    "alignment", "first_line_indent_pt", "line_spacing_multiple",
    "line_spacing_pt", "space_before_pt", "space_after_pt",
}
PARA_KEYS = {
    "index", "text", "runs", "kind", "formula_number", "formula_xml", "hyperlinks",
}
RUN_FORMAT = {
    "font_name", "east_asia_font", "font_size_pt", "bold", "italic",
    "char_spacing_pt",
}
# 解析层输出的 run 元信息：超链接归属随 w:hyperlink 元素原样保留，域信息不可重写
RUN_IGNORED = {"hyperlink"}
RUN_READONLY = {"field"}
# 只能承载文字的 run 子元素；含 w:tab/w:br/fldChar/图形的 run 不能整段换字
RUN_TEXT_CHILDREN = {qn("w:rPr"), qn("w:t")}
TABLE_KEYS = {"index", "cells", "header_repeat", "style", "align"}
CELL_KEYS = {
    "row", "col", "text", "font_name", "east_asia_font", "font_size_pt",
    "runs", "grid_span", "vmerge", "align",
}
# 合并方式属于结构信息，原位写回不动
CELL_STRUCTURAL = {"grid_span", "vmerge"}
DOCUMENT_XML = "word/document.xml"
DOCUMENT_RELS = "word/_rels/document.xml.rels"
CONTENT_TYPES = "[Content_Types].xml"
VML_IMAGEDATA = "{urn:schemas-microsoft-com:vml}imagedata"
