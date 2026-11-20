# -*- coding: utf-8 -*-
"""新格式图片：规范里只给 index/type，复现时额外支持可选 path / width_inch / height_inch 字段。"""
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH

from backend.docx_common.alignment import to_ooxml

from ._defaults import DEFAULT_IMAGE, pick as _pick  # noqa: F401  （DEFAULT_IMAGE 供外部沿用）


def add_image(doc, image_config: dict):
    """
    image_config 结构：
    {
      "index": 3, "type": "image",
      "path": "./images/img_0004.png",
      "width_inch": 2.5,      # 可选，来自原文档显示尺寸
      "height_inch": 1.8,     # 可选
      "align": "center"       # 可选
    }
    """
    if not isinstance(image_config, dict):
        raise ValueError("image 配置必须是 dict")

    path = image_config.get("path")
    if not path:
        doc.add_paragraph()
        return None

    paragraph = doc.add_paragraph()
    picture = paragraph.add_run().add_picture(path)

    # ★ 宽高都未指定 → 保持图片原始尺寸
    w = _pick(image_config, "width_inch", DEFAULT_IMAGE["width_inch"])
    h = _pick(image_config, "height_inch", DEFAULT_IMAGE["height_inch"])

    if w is not None and h is not None:
        picture.width  = Inches(w)
        picture.height = Inches(h)
    elif w is not None:
        picture.width  = Inches(w)
    elif h is not None:
        picture.height = Inches(h)
    # 都不指定：不动 picture.width / picture.height，保持原图尺寸

    align = _pick(image_config, "align", DEFAULT_IMAGE["align"])
    paragraph.alignment = to_ooxml(align, WD_ALIGN_PARAGRAPH.CENTER)

    pf = paragraph.paragraph_format
    pf.space_before = Pt(_pick(image_config, "space_before_pt",
                               DEFAULT_IMAGE["space_before_pt"]))
    pf.space_after  = Pt(_pick(image_config, "space_after_pt",
                               DEFAULT_IMAGE["space_after_pt"]))
    return paragraph