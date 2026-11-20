# -*- coding: utf-8 -*-
"""将 DOCX 解析为项目统一的混排文档 JSON。"""
import json
import os
import sys

from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.table import Table

from backend.docx_common.assets import image_path, images_dir
from backend.docx_common.blocks import is_inside_textbox, iter_block_elements
from backend.docx_common.runs import iter_runs

from backend.recognition.classification import classify_kind
from backend.recognition.context import apply_contextual_kinds
from .fields import field_run_map, paragraph_field_types
from .font_reader import (read_auto_number, read_document_fonts,
                          read_numbering_ref, read_paragraph_style, read_run)
from .formula import detect_formula, is_formula_number_line, is_plain_formula_body
from .media import find_images_in_paragraph
from .numbering import NumberingRenderer, attach_auto_number_text
from .table_reader import parse_table_block

SCHEMA_VERSION = "1.0"
PAPER_SIZES_CM = {
    "A4": (21.0, 29.7), "A3": (29.7, 42.0), "A5": (14.8, 21.0),
    "B5": (17.6, 25.0), "Letter": (21.59, 27.94), "Legal": (21.59, 35.56),
}


def detect_paper_size(width_cm, height_cm):
    if width_cm is None or height_cm is None:
        return None
    for name, (width, height) in PAPER_SIZES_CM.items():
        if (
            abs(width_cm - width) < 0.3 and abs(height_cm - height) < 0.3
        ) or (
            abs(width_cm - height) < 0.3 and abs(height_cm - width) < 0.3
        ):
            return name
    return None


def _paragraph_style_info(para):
    """提取段落样式名与大纲级别（P4）。

    标题识别不再只看正文编号文本：
    - style_name：Word 样式名（如“标题 1”/“Heading 2”），
      覆盖“用样式排的标题判不出”；
    - outline_lvl：<w:outlineLvl>（0 起），覆盖 Word 自动编号标题
      （编号由 numPr 生成、不在文本里）。
    """
    style_name = None
    try:
        style_name = para.style.name if para.style is not None else None
    except Exception:
        style_name = None
    outline_lvl = None
    p_pr = para._p.find(qn("w:pPr"))
    if p_pr is not None:
        lvl = p_pr.find(qn("w:outlineLvl"))
        if lvl is not None:
            val = lvl.get(qn("w:val"))
            if val is not None and val.isdigit():
                outline_lvl = int(val)
    return style_name, outline_lvl


def _count_images_in_paragraph(para):
    """统计段落内图片结构数量（drawing/pict/object），供页眉页脚内容描述。

    页眉页脚里的图片属于 header/footer 部件，关系 id 不在 doc.part.related_parts
    中，无法提取 blob；这里只记录数量与存在性，避免误导调用方。
    """
    count = 0
    for _ in para._p.iter(qn("w:drawing")):
        count += 1
    for _ in para._p.iter(qn("w:pict")):
        count += 1
    for _ in para._p.iter(qn("w:object")):
        count += 1
    return count


def parse_sections(doc, theme_map=None):
    sections = []
    for index, section in enumerate(doc.sections):
        width = section.page_width.cm if section.page_width is not None else None
        height = section.page_height.cm if section.page_height is not None else None
        section_dict = {
            "index": index,
            "paper_size": detect_paper_size(width, height),
            "orientation": "landscape" if width is not None and height is not None and width > height else "portrait",
            # 页面宽高（厘米）：审版式位置（如“续表与表格左边线对齐”）要算版心宽度，
            # 只留纸型名字不够。
            "width_cm": round(width, 2) if width is not None else None,
            "height_cm": round(height, 2) if height is not None else None,
            "left_cm": round(section.left_margin.cm, 2) if section.left_margin is not None else None,
            "right_cm": round(section.right_margin.cm, 2) if section.right_margin is not None else None,
            "top_cm": round(section.top_margin.cm, 2) if section.top_margin is not None else None,
            "bottom_cm": round(section.bottom_margin.cm, 2) if section.bottom_margin is not None else None,
            "header_cm": round(section.header_distance.cm, 2) if section.header_distance is not None else None,
            "footer_cm": round(section.footer_distance.cm, 2) if section.footer_distance is not None else None,
        }
        # P5：旧实现只取 header_cm/footer_cm 距离，页眉页脚内容在独立的
        # header/footer part 里、不在 doc.element.body，文字/页码从未进 JSON。
        # 现在遍历默认/首页/奇偶页页眉页脚，并保留 is_linked_to_previous，
        # 供后续页码格式、页眉文字等规则使用。
        headers = _collect_header_footers(section, doc, theme_map, "header")
        footers = _collect_header_footers(section, doc, theme_map, "footer")
        if headers:
            section_dict["headers"] = headers
        if footers:
            section_dict["footers"] = footers
        sections.append(section_dict)
    return sections


def _collect_header_footers(section, doc, theme_map, kind):
    """收集一个 section 的页眉（或页脚）子项列表。

    kind: "header" | "footer"；type: "default" | "first_page" | "even_page"。
    仅收录有内容或非“链接到上一节”的项，纯空的继承项不占位。
    """
    attrs = (
        (kind, "default"),
        (f"first_page_{kind}", "first_page"),
        (f"even_page_{kind}", "even_page"),
    )
    entries = []
    for attr, hf_type in attrs:
        try:
            container = getattr(section, attr)
        except Exception:
            continue
        try:
            linked = container.is_linked_to_previous
        except Exception:
            linked = True
        paragraphs = []
        tables = []
        image_count = 0
        try:
            block_elements = list(iter_block_elements(container._element))
        except Exception:
            block_elements = []
        for element in block_elements:
            if element.tag == qn("w:tbl"):
                table_block = parse_table_block(Table(element, doc), doc, theme_map)
                if table_block is not None:
                    tables.append(table_block)
                continue
            para = Paragraph(element, doc)
            image_count += _count_images_in_paragraph(para)
            block = _paragraph_block(para, doc, theme_map)
            if block.get("text"):
                paragraphs.append(block)
        text = "".join(b.get("text", "") for b in paragraphs)
        if bool(linked) and not text and not tables:
            continue
        entries.append({
            "kind": kind,
            "type": hf_type,
            "is_linked_to_previous": bool(linked),
            "text": text,
            "paragraphs": paragraphs,
            "tables": tables,
            "image_count": image_count,
        })
    return entries


def _paragraph_hyperlinks(para, doc):
    """提取段落直接子级 w:hyperlink（文字 + 目标 + 在 run 流中的位置）。

    runs 与 hyperlinks 必须同一口径：run_index 是该超链接首个 run 在
    runs 里的下标，run_count 是它包含的 run 个数，两者都含超链接内的 run。
    """
    hyperlinks = []
    position = 0
    for child in para._p:
        if child.tag == qn("w:r"):
            position += 1
        elif child.tag == qn("w:hyperlink"):
            rid = child.get(qn("r:id"))
            target = None
            if rid:
                rel = doc.part.rels.get(rid)
                if rel is not None:
                    target = rel.target_ref
            anchor = child.get(qn("w:anchor"))
            if anchor:
                target = anchor  # 内部锚点（书签）
            text = "".join(t.text or "" for t in child.iter(qn("w:t")))
            hyperlinks.append({
                "text": text,
                "target": target,
                "run_index": position,
                "run_count": len(child.findall(qn("w:r"))),
            })
            position += len(child.findall(qn("w:r")))
    return hyperlinks


def _paragraph_block(para, doc, theme_map, structure=None):
    style = read_paragraph_style(para, doc)
    formula = detect_formula(para)
    # P4：把 Word 样式名/大纲级别传给 classify_kind，标题识别不再只靠文本正则。
    style_name, outline_lvl = _paragraph_style_info(para)
    # P7：解析域代码，给域结果 run 打标、段落级记录 fields。
    field_map = field_run_map(para._p)
    # run 流必须与段落文字同口径：para.runs 只含 w:p 直接子级的 w:r，不含超链接
    # 内的 run；CT_P.text 会把超链接文字算进来、却不下钻行内包装（内容控件、
    # 修订插入的片段）。两边各用一套口径时，那部分文字既不在 runs 里
    # （字体/字号漏检）又会被整段改写丢掉，故文字直接由 run 流拼出来。
    runs = []
    for run, link_index in iter_runs(para):
        info = read_run(run, doc, theme_map, para)
        fld = field_map.get(run._element)
        if fld:
            info["field"] = fld
        if link_index is not None:
            info["hyperlink"] = link_index
        runs.append(info)
    text = "".join(info.get("text") or "" for info in runs) or para.text
    if not text.strip():
        # 空段落/只有空白的段落不带 run 信息（与旧契约一致）。
        text = para.text
        runs = []
    kind = "formula" if formula else (
        classify_kind(text, style_name=style_name, outline_lvl=outline_lvl,
                      structure=structure)
        if text.strip() else "unknown"
    )
    block = {
        "type": "paragraph",
        "kind": kind,
        "text": text if text.strip() else "",
        **style,
        "runs": runs,
    }
    # 图形内部（形状/文本框）的段落：它的文字属于“图内”，模板要求
    # 「图内字体字号：宋体五号字」，检测层据此走图内规则而不是正文规则。
    if is_inside_textbox(para._p):
        block["figure_text"] = True
    # 编号由 Word 自动编号生成（模板标题就是）：文本里没有编号，
    # “编号格式”一类的检查据此跳过，不再把“编号不在文本里”当成编号错。
    if read_auto_number(para, doc):
        block["auto_number"] = True
        ref = read_numbering_ref(para, doc)
        if ref is not None:
            block["auto_number_ref"] = [ref[0], ref[1]]
    if formula:
        block.update(formula)
    fields = paragraph_field_types(para._p, field_map)
    if fields:
        block["fields"] = fields
    # 超链接：para.runs 不含超链接内 run，若契约不单独记录，
    # 复现层按 runs 重建时超链接文字会整体丢失。
    hyperlinks = _paragraph_hyperlinks(para, doc)
    if hyperlinks:
        block["hyperlinks"] = hyperlinks
    return block


def _image_blocks(para, doc):
    images = find_images_in_paragraph(para, doc)
    if not images:
        return [], []
    style = read_paragraph_style(para, doc)
    align = style["alignment"]
    # 段落里除了图形还有文字 ⇒ 行内图形（MathType/公式的 OLE 预览图、符号图），
    # 不是论文插图：模板的“图号和图题名排于图的下方”针对的是独立成段的插图。
    inline = bool("".join(run.text or "" for run in para._p.iter(qn("w:t"))).strip())
    blocks = []
    for image in images:
        block = {"type": "image", "align": align}
        if inline:
            block["inline"] = True
        for key in ("width_inch", "height_inch"):
            if image.get(key) is not None:
                block[key] = image[key]
        blocks.append(block)
    return blocks, images


def _following_formula_number(children, pos, doc):
    """下一个段落整段只有公式编号时返回该编号，否则 None。

    用于「公式独占一行、编号另起一行」的排版：这种公式行的段内没有编号，
    detect_formula 认不出来，只能靠紧邻的下一段是不是纯编号来确认。
    """
    nxt = pos + 1
    if nxt >= len(children) or children[nxt].tag != qn("w:p"):
        return None
    return is_formula_number_line(Paragraph(children[nxt], doc).text)


def parse_blocks(doc, theme_map, structure=None):
    blocks = []
    images = []
    # 旧实现只遍历 body：for child in doc.element.body
    # 页眉页脚在独立 part，改由 parse_sections 负责，body 仍只处理正文；
    # 块级元素由 iter_block_elements 产出（下钻 w:sdt 内容控件、w:ins 修订插入等，
    # 否则带内容控件的目录会被整体丢掉，检测侧报“未检测到目录”）。
    children = list(iter_block_elements(doc.element.body))
    # 自动编号渲染器：编号文字不在段落文本里，只有按文档顺序把 numbering.xml 的
    # 定义填成文字（[%1] + 第 3 条 → [3]），判定层才看得到“序号是什么样”。
    number_renderer = NumberingRenderer(doc)
    # 编号独占一段时，该段位置记在这里：它属于公式，不能再按正文段落判。
    number_line_pos = None
    for pos, child in enumerate(children):
        if child.tag == qn("w:p"):
            para = Paragraph(child, doc)
            formula = detect_formula(para)
            if formula:
                # 公式段落（OMML / OLE / 纯文本）：保持 paragraph 块（kind=formula），
                # 这样 formula_xml / formula_assets / runs 都保留，写回层才能重绑 r:id（W3）。
                # A 批 P6 的 OLE 预览图不再拆成 image 块，改为挂到 formula_preview。
                block = _paragraph_block(para, doc, theme_map, structure)
                attach_auto_number_text(number_renderer, para, doc, block)
                if formula.get("formula_type") in ("omml", "ole"):
                    previews = find_images_in_paragraph(para, doc)
                    if previews:
                        preview = previews[0]
                        block["formula_preview"] = {
                            "width_inch": preview.get("width_inch"),
                            "height_inch": preview.get("height_inch"),
                        }
                blocks.append(block)
            else:
                image_blocks, found_images = _image_blocks(para, doc)
                if image_blocks:
                    blocks.extend(image_blocks)
                    images.extend(found_images)
                else:
                    block = _paragraph_block(para, doc, theme_map, structure)
                    attach_auto_number_text(number_renderer, para, doc, block)
                    number = _following_formula_number(children, pos, doc)
                    if (number and block.get("kind") == "body"
                            and is_plain_formula_body(block.get("text"))):
                        # 公式本体独占一行：按公式判（居中、编号位置…），
                        # 编号由紧随的下一段给出，因此编号没落在公式同一行行末。
                        block["kind"] = "formula"
                        block["formula_type"] = "text"
                        block["formula_text"] = (block.get("text") or "").strip()
                        block["formula_number"] = number
                        number_line_pos = pos + 1
                    elif number_line_pos == pos:
                        # 编号独占一段：它在文档里的位置由公式的编号位置条款判，
                        # 段内格式不再套正文条款（正文条款针对正文段落）。
                        block["kind"] = "formula_number"
                        number_line_pos = None
                    # 分节符空段：段落 pPr 内嵌 sectPr（如 add_section 产生的
                    # 分节符）。打标记供写回层在正确位置重建分节符，
                    # 否则写回层只能把分节符塞到文档末尾，正文与节错位。
                    pPr = para._p.pPr
                    if pPr is not None and pPr.find(qn("w:sectPr")) is not None:
                        block["section_break"] = True
                    blocks.append(block)
        elif child.tag == qn("w:tbl"):
            table_block = parse_table_block(Table(child, doc), doc, theme_map)
            if table_block is not None:
                blocks.append(table_block)
    apply_contextual_kinds(blocks, structure)
    return blocks, images


def _index_blocks(blocks, images, image_dir):
    image_cursor = 0
    for index, block in enumerate(blocks):
        if block["type"] == "image":
            image = images[image_cursor]
            image_cursor += 1
            filename = f"img_{index:04d}{image['ext']}"
            with open(os.path.join(image_dir, filename), "wb") as output:
                output.write(image["blob"])
        blocks[index] = {"index": index, **block}


def _index_formula_assets(blocks, image_dir):
    """W3：把公式引用的部件（预览图 / OLE 二进制）落盘。

    formula_assets 由 parser.formula._collect_formula_assets 收集，
    此时 blob 还在内存；落盘后在 JSON 里只保留 path / content_type / old_rid，
    写回层（write_back.op_passage）按 path 读取并重绑 r:id。
    path 与 image block 的 path 同口径：相对 JSON 文件所在目录（如 images/xxx）。
    """
    for block in blocks:
        assets = block.get("formula_assets") or []
        if not assets:
            continue
        indexed = []
        for i, asset in enumerate(assets):
            filename = f"formula_{block.get('index', 0):04d}_{i}{asset['ext']}"
            with open(os.path.join(image_dir, filename), "wb") as output:
                output.write(asset["blob"])
            indexed.append({
                "old_rid": asset["old_rid"],
                "content_type": asset["content_type"],
                "path": image_path(filename),
            })
        block["formula_assets"] = indexed


def docx_to_json(docx_path: str, output_dir: str = "output", structure=None) -> str:
    os.makedirs(output_dir, exist_ok=True)
    image_dir = images_dir(output_dir)
    os.makedirs(image_dir, exist_ok=True)
    doc = Document(docx_path)
    theme_map = read_document_fonts(doc)
    blocks, images = parse_blocks(doc, theme_map, structure)
    _index_blocks(blocks, images, image_dir)
    # W3：公式引用的部件（OLE 预览图 / OLE 二进制）落盘，供重建链路重绑 r:id。
    _index_formula_assets(blocks, image_dir)
    result = {
        "schema_version": SCHEMA_VERSION,
        "file_name": os.path.basename(docx_path),
        "sections": parse_sections(doc, theme_map),
        "blocks": blocks,
    }
    output_path = os.path.join(output_dir, "document.json")
    with open(output_path, "w", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("用法：python -m backend.parser.docx_to_json 输入.docx [输出目录]")
    source = sys.argv[1]
    destination = sys.argv[2] if len(sys.argv) >= 3 else "output"
    print(f"已生成: {docx_to_json(source, destination)}")
