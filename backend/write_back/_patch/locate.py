# -*- coding: utf-8 -*-

"""块定位：把契约里的 blocks 对齐到原 DOCX 的正文元素，对不上就拒绝写回。"""

import os

from docx.oxml.ns import qn
from docx.table import Table

from backend.docx_common.blocks import iter_block_elements

from .keys import VML_IMAGEDATA


def image_count(element, doc):
    """与解析器一样，只计入存在于正文关系中的图片引用。"""
    count = 0
    for drawing in element.iter(qn("w:drawing")):
        for blip in drawing.iter(qn("a:blip")):
            rid = blip.get(qn("r:embed")) or blip.get(qn("r:link"))
            count += rid in doc.part.related_parts
    for pict in element.iter(qn("w:pict")):
        for image in pict.iter(VML_IMAGEDATA):
            count += image.get(qn("r:id")) in doc.part.related_parts
    return count


def image_references(element, doc):
    references = []
    for drawing in element.iter(qn("w:drawing")):
        for blip in drawing.iter(qn("a:blip")):
            rid = blip.get(qn("r:embed")) or blip.get(qn("r:link"))
            if rid in doc.part.related_parts:
                references.append((blip, qn("r:embed") if blip.get(qn("r:embed")) else qn("r:link")))
    for pict in element.iter(qn("w:pict")):
        for image in pict.iter(VML_IMAGEDATA):
            if image.get(qn("r:id")) in doc.part.related_parts:
                references.append((image, qn("r:id")))
    return references


def resolve_image_path(block, images_dir, report=None, where=None):
    """图片块对应的实际文件；不可用时返回 None（W7：只告警，不再中止写回）。"""
    if not images_dir or not block.get("path"):
        if report is not None:
            report.skip("图片修复缺少可用的图片路径", where)
        return None
    path = os.path.join(images_dir, os.path.basename(block["path"]))
    if not os.path.isfile(path):
        if report is not None:
            report.skip(f"图片文件不存在：{path}", where)
        return None
    return path


def is_formula_element(element):
    """段落是否公式段（含 OMML / OLE 对象）。

    解析层对公式段始终生成 1 个 paragraph 块（kind=formula），
    即使带 OLE 预览图也不再展开成 image 块（W3 契约）。
    """
    for node in element.iter():
        if node.tag in (qn("m:oMath"), qn("w:object")):
            return True
    return False


def source_elements(doc, original_blocks):
    # 旧实现（W3 结构变更前）：
    #     elements = []
    #     for child in doc.element.body:
    #         if child.tag == qn("w:p"):
    #             elements.extend([child] * (image_count(child, doc) or 1))
    #         elif child.tag == qn("w:tbl") and len(Table(child, doc).rows):
    #             elements.append(child)
    #     ...
    # 公式段（w:object/m:oMath）现在固定映射 1 个 paragraph 块；
    # 普通段落仍按 image_count 展开成 image 块。
    # 块级元素必须与解析层同一口径（iter_block_elements 会下钻内容控件/修订插入等
    # 包装），否则带内容控件的文档块数对不上，写回会被整体拒绝。
    elements = []
    for child in iter_block_elements(doc.element.body):
        if child.tag == qn("w:p"):
            if is_formula_element(child):
                elements.append(child)
            else:
                elements.extend([child] * (image_count(child, doc) or 1))
        elif child.tag == qn("w:tbl") and len(Table(child, doc).rows):
            elements.append(child)
    if len(elements) != len(original_blocks):
        raise ValueError("原 DOCX 块定位与解析契约不一致，已拒绝写回")
    for index, (element, block) in enumerate(zip(elements, original_blocks)):
        if is_formula_element(element):
            expected = "paragraph"
        else:
            expected = "table" if element.tag == qn("w:tbl") else (
                "image" if image_count(element, doc) else "paragraph"
            )
        if block.get("type") != expected or block.get("index") != index:
            raise ValueError(f"block index {index} 无法安全定位到原 DOCX")
    return elements
