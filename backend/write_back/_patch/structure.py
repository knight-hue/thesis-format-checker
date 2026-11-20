# -*- coding: utf-8 -*-

"""结构性变更：先校验并出执行计划，再创建新对象，最后才一次性重排正文。"""

from docx.oxml.ns import qn

from ..op_passage import add_paragraph
from ..op_picture import add_image
from .locate import resolve_image_path


def patch_structure(doc, updated_blocks, source_ids, source_elements, images_dir, report, where):
    """结构性变更：先校验、再创建、最后一次性重建正文顺序（W6）。

    旧实现一边校验一边往正文追加新段落/图片，任何一步校验失败都会把已追加的
    半成品留在文档里。现在分成三阶段：阶段一纯校验并给出执行计划，阶段二创建
    新对象（失败即回滚），阶段三才动正文顺序。
    """
    body = doc.element.body
    if len(doc.sections) != 1 or any(
        child.tag not in {qn("w:p"), qn("w:tbl"), qn("w:sectPr")}
        for child in body
    ) or any(child.find(".//" + qn("w:sectPr")) is not None for child in body if child.tag == qn("w:p")):
        return report.skip("含多节或未识别正文对象的文档暂不支持结构性原位写回", where)
    if len(set(map(id, source_elements))) != len(source_elements):
        return report.skip("同一段落包含多张图片，无法安全移动单张图片块", where)
    original_nodes = [child for child in body if child.tag in {qn("w:p"), qn("w:tbl")}]
    if set(original_nodes) != set(source_elements):
        raise ValueError("正文存在解析契约未表示的块，无法安全调整顺序")

    plan = []
    retained = set()
    for block in updated_blocks:
        source_index = source_ids.get(id(block))
        if source_index is not None:
            retained.add(source_index)
            plan.append(("keep", source_elements[source_index]))
            continue
        block_type = block.get("type")
        if block_type == "paragraph":
            plan.append(("paragraph", block))
        elif block_type == "image":
            path = resolve_image_path(block, images_dir, report, where)
            if path is None:
                return False
            plan.append(("image", {**block, "path": path}))
        else:
            return report.skip(f"新增 {block_type} 块暂不支持安全原位写回", where)
    if retained != set(range(len(source_elements))):
        return report.skip("原始块被删除，已拒绝可能丢内容的写回", where)
    if len(retained) != sum(
        1 for block in updated_blocks if source_ids.get(id(block)) is not None
    ):
        return report.skip("原始块重复出现，已拒绝不一致的结构性写回", where)

    ordered = []
    created = []
    try:
        for action, payload in plan:
            if action == "keep":
                ordered.append(payload)
                continue
            paragraph = add_paragraph(doc, payload) if action == "paragraph" else add_image(doc, payload)
            if paragraph is None:
                raise ValueError("新增内容为空段落")
            created.append(paragraph._p)
            ordered.append(paragraph._p)
    except Exception as exc:
        for element in created:
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)
        return report.skip(f"新增内容创建失败：{exc}", where)

    for child in list(body):
        if child.tag in {qn("w:p"), qn("w:tbl")}:
            body.remove(child)
    sectpr = body.sectPr
    for element in ordered:
        body.insert(body.index(sectpr), element)
    return True
