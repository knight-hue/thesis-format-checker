# -*- coding: utf-8 -*-

"""OPC 部件合并：新增图片时把关系表与内容类型表并回原包，冲突即拒绝。"""

from copy import deepcopy

from lxml import etree

from .keys import CONTENT_TYPES, DOCUMENT_RELS


def merge_xml_entries(original_bytes, rendered_bytes, identity_key):
    original = etree.fromstring(original_bytes)
    rendered = etree.fromstring(rendered_bytes)
    existing = {(child.tag, child.get(identity_key(child))) for child in original}
    for child in rendered:
        identity = (child.tag, child.get(identity_key(child)))
        if identity not in existing:
            original.append(deepcopy(child))
            existing.add(identity)
        else:
            prior = next(item for item in original if (item.tag, item.get(identity_key(item))) == identity)
            if dict(prior.attrib) != dict(child.attrib):
                raise ValueError("新图片关系与原 DOCX 部件冲突，已拒绝写回")
    return etree.tostring(original, encoding="UTF-8", xml_declaration=True, standalone=True)


def merged_relationships(original, rendered):
    return merge_xml_entries(
        original.read(DOCUMENT_RELS), rendered.read(DOCUMENT_RELS),
        lambda element: "Id",
    )


def merged_content_types(original, rendered):
    return merge_xml_entries(
        original.read(CONTENT_TYPES), rendered.read(CONTENT_TYPES),
        lambda element: "Extension" if element.tag.endswith("Default") else "PartName",
    )
