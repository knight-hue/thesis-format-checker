# -*- coding: utf-8 -*-

"""写回主流程：定位 → 逐块写回 → 结构性变更 → 打包（未改部件逐字节保留）。"""

import os
import shutil
import tempfile
from zipfile import ZipFile

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml import etree

from .diff import block_changed, value_changed
from .keys import CONTENT_TYPES, DOCUMENT_RELS, DOCUMENT_XML
from .locate import image_references, resolve_image_path, source_elements
from .opc import merged_content_types, merged_relationships
from .paragraph import patch_paragraph
from .report import PatchReport
from .section import patch_section
from .structure import patch_structure
from .table import patch_table


def patch_docx(source_docx, output_docx, original_document, updated_document, source_ids, images_dir=None):
    """把修复结果原样写回原 DOCX：保留 ZIP 里未改动的部件，只替换改动的 XML。

    与旧版的关键区别（W5/W6/W7）：单点写不动不再让整次写回失败，而是登记到
    返回的报告里继续写其余部分；只有契约层面的错误（原 DOCX 与解析结果对不上、
    分节数量不一致、缺少 word/document.xml、图片部件名冲突）才抛 ValueError。

    返回 write_back.patch_docx.PatchReport.to_dict() 的结果：
    ``{"output": 输出路径, "changed": 是否真的改过, "skipped": [跳过明细]}``。
    """
    old_blocks = original_document["blocks"]
    new_blocks = updated_document["blocks"]
    report = PatchReport()
    doc = Document(source_docx)
    elements = source_elements(doc, old_blocks)
    if len(doc.sections) != len(original_document["sections"]):
        raise ValueError("sections 与原 DOCX 不一致，已拒绝写回")
    if len(updated_document["sections"]) != len(original_document["sections"]):
        raise ValueError("增删分节暂不支持安全原位写回")

    changed = False
    for index, (old, new, section) in enumerate(zip(
        original_document["sections"], updated_document["sections"], doc.sections
    )):
        if {k: v for k, v in old.items() if k != "index"} != {
            k: v for k, v in new.items() if k != "index"
        }:
            changed |= patch_section(section, old, new, report, f"第{index + 1}节")

    source_order = [source_ids.get(id(block)) for block in new_blocks]
    structural = source_order != list(range(len(old_blocks)))
    new_image_blocks = any(
        block.get("type") == "image" and source_ids.get(id(block)) is None
        for block in new_blocks
    )
    replaced_image = False
    for block in new_blocks:
        source_index = source_ids.get(id(block))
        if source_index is None:
            continue
        before = old_blocks[source_index]
        element = elements[source_index]
        where = f"第{source_index + 1}块"
        if block["type"] == "paragraph":
            if block_changed(before, block, {"index"}):
                changed |= patch_paragraph(Paragraph(element, doc), before, block, report, where)
        elif block["type"] == "table":
            if block_changed(before, block, {"index"}):
                changed |= patch_table(Table(element, doc), before, block, report, where)
        elif block["type"] == "image":
            for key in before.keys() | block.keys():
                if key in {"index", "path"} or not value_changed(before, block, key):
                    continue
                report.skip(f"图片属性 {key} 修改暂不支持安全原位写回", where)
            path = block.get("path") or ""
            if path and not os.path.basename(path).startswith(f"img_{source_index:04d}."):
                references = image_references(element, doc)
                ordinal = source_index - next(
                    index for index, source in enumerate(elements) if source is element
                )
                if ordinal >= len(references):
                    report.skip("图片索引与原 DOCX 引用不一致", where)
                    continue
                resolved = resolve_image_path(block, images_dir, report, where)
                if resolved is None:
                    continue
                try:
                    rid, _ = doc.part.get_or_add_image(resolved)
                except Exception:
                    report.skip("替换图片不是可识别的图片文件", where)
                    continue
                reference, attribute = references[ordinal]
                reference.set(attribute, rid)
                replaced_image = changed = True
    structural_applied = False
    if structural:
        structural_applied = patch_structure(
            doc, new_blocks, source_ids, elements, images_dir, report, "结构变更",
        )
        changed |= structural_applied

    if not changed:
        shutil.copyfile(source_docx, output_docx)
        return report.to_dict(output_docx, False)
    xml = etree.tostring(doc.element, encoding="UTF-8", xml_declaration=True, standalone=True)
    with tempfile.TemporaryDirectory(prefix="thesis_patch_") as temporary:
        rendered_path = os.path.join(temporary, "rendered.docx")
        needs_media = replaced_image or (structural_applied and new_image_blocks)
        if needs_media:
            doc.save(rendered_path)
        with ZipFile(source_docx, "r") as original:
            if DOCUMENT_XML not in original.namelist():
                raise ValueError("DOCX 缺少 word/document.xml")
            rendered = ZipFile(rendered_path, "r") if needs_media else None
            try:
                replacements = {DOCUMENT_XML: xml}
                additions = []
                if rendered is not None:
                    replacements[DOCUMENT_RELS] = merged_relationships(original, rendered)
                    replacements[CONTENT_TYPES] = merged_content_types(original, rendered)
                    for part in rendered.infolist():
                        if not part.filename.startswith("word/media/"):
                            continue
                        if part.filename in original.namelist():
                            if original.read(part.filename) != rendered.read(part.filename):
                                raise ValueError("新图片部件与原 DOCX 文件名冲突")
                        else:
                            additions.append(part)
                with ZipFile(output_docx, "w") as output:
                    for part in original.infolist():
                        output.writestr(part, replacements.get(part.filename, original.read(part)))
                    for part in additions:
                        output.writestr(part, rendered.read(part))
            finally:
                if rendered is not None:
                    rendered.close()
    return report.to_dict(output_docx, True)
