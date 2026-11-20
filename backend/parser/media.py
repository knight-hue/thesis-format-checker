"""DOCX 图片提取。"""
from docx.oxml.ns import qn

CONTENT_TYPE_EXT = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
    "image/gif": ".gif", "image/bmp": ".bmp", "image/tiff": ".tiff",
    "image/x-emf": ".emf", "image/x-wmf": ".wmf",
}
EMU_PER_INCH = 914400
# python-docx 的 nsmap 不含 v 前缀，直接使用 VML 命名空间 URI
_VML_NS = "urn:schemas-microsoft-com:vml"
_V_IMAGEDATA = f"{{{_VML_NS}}}imagedata"


def _image_part(doc, relation_id):
    if not relation_id:
        return None
    try:
        return doc.part.related_parts[relation_id]
    except KeyError:
        return None


def _vml_size(data):
    width = height = None
    shape = data.getparent()
    style = shape.get("style", "") if shape is not None else ""
    for item in style.split(";"):
        key, _, value = item.strip().partition(":")
        if key in ("width", "height") and value.strip().endswith("pt"):
            size = round(float(value.strip()[:-2]) / 72, 3)
            if key == "width":
                width = size
            else:
                height = size
    return width, height


def iter_image_refs(para, doc):
    """按解析顺序产出段落内 (image_part, width_inch, height_inch)。

    不读取图片字节，供只需要图片数量的调用方（如写回定位）使用。
    """
    for drawing in para._element.iter(qn("w:drawing")):
        width = height = None
        extent = next(drawing.iter(qn("wp:extent")), None)
        if extent is not None and extent.get("cx") and extent.get("cy"):
            width = round(int(extent.get("cx")) / EMU_PER_INCH, 3)
            height = round(int(extent.get("cy")) / EMU_PER_INCH, 3)
        for blip in drawing.iter(qn("a:blip")):
            part = _image_part(doc, blip.get(qn("r:embed")) or blip.get(qn("r:link")))
            if part is not None:
                yield part, width, height
    for pict in para._element.iter(qn("w:pict")):
        for data in pict.iter(_V_IMAGEDATA):
            part = _image_part(doc, data.get(qn("r:id")))
            if part is None:
                continue
            width, height = _vml_size(data)
            yield part, width, height
    # P6：MathType / 公式编辑器 OLE 对象。旧实现只在 w:drawing（a:blip）
    # 与 w:pict（v:imagedata）里找图，而 OLE 预览图位于
    # <w:object><v:shape><v:imagedata/></v:shape></w:object>，不在 w:pict 下，
    # 导致公式块只有 ole 标记、没有预览图。
    for obj in para._element.iter(qn("w:object")):
        for data in obj.iter(_V_IMAGEDATA):
            part = _image_part(doc, data.get(qn("r:id")))
            if part is None:
                continue
            width, height = _vml_size(data)
            yield part, width, height


def find_images_in_paragraph(para, doc):
    # 旧实现：
    #     return [
    #         {
    #             "blob": part.blob,
    #             "ext": CONTENT_TYPE_EXT.get(part.content_type, ".png"),
    #             "width_inch": width,
    #             "height_inch": height,
    #         }
    #         for part, width, height in iter_image_refs(para, doc)
    #     ]
    # 同一预览图可能同时以 w:drawing/w:pict/w:object 多种结构出现，
    # 按 (图片部件, 尺寸) 去重，避免重复提取同一张图。
    seen = set()
    images = []
    for part, width, height in iter_image_refs(para, doc):
        key = (id(part), width, height)
        if key in seen:
            continue
        seen.add(key)
        images.append({
            "blob": part.blob,
            "ext": CONTENT_TYPE_EXT.get(part.content_type, ".png"),
            "width_inch": width,
            "height_inch": height,
        })
    return images
