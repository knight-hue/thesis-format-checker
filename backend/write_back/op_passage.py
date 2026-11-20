# -*- coding: utf-8 -*-
"""新格式段落：扁平字段 + runs 数组（一段可含多 run）。"""
from docx.shared import Pt
from docx.text.run import Run
from docx.enum.text import WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.oxml import OxmlElement, parse_xml

from backend.docx_common.alignment import to_ooxml
from backend.docx_common.runs import set_char_spacing, set_run_font

from ._defaults import DEFAULT_RUN, pick as _pick  # noqa: F401  （DEFAULT_RUN 供外部沿用）


# W3：原先这里有一份 DEFAULT_PARA =
# {"alignment": "justify", "first_line_indent_pt": 24.0, "line_spacing_multiple": 1.5,
#  "line_spacing_pt": None, "space_before_pt": 0.0, "space_after_pt": 6.0}。
# 它会在 JSON 缺字段时凭空写入两端对齐 / 1.5 倍行距 / 段后 6pt——
# rules 并没有针对“任意段落”的这类强制规定（正文段落格式由检测层判定），
# 重建只能忠实于解析结果：字段缺失就保持段落原样，故整体删除。


def _apply_run_format(run, r_cfg):
    """按 run 配置写字体/字号/粗斜/字间距/颜色（W6 域结果 run 复用）。"""
    ascii_font = _pick(r_cfg, "font_name",      DEFAULT_RUN["font_name"])
    ea_font    = _pick(r_cfg, "east_asia_font", DEFAULT_RUN["east_asia_font"])
    set_run_font(run, ascii_font, ea_font)
    run.font.size   = Pt(_pick(r_cfg, "font_size_pt", DEFAULT_RUN["font_size_pt"]))
    # W2：bold / italic 只在解析或修复层明确给出布尔值时才写。
    # None（例如修复层新建的 run）表示未指定，不写 w:b / w:i，
    # 让原文档样式继承继续生效；写 False 等于显式关闭加粗/斜体。
    if r_cfg.get("bold") is not None:
        run.font.bold = bool(r_cfg.get("bold"))
    if r_cfg.get("italic") is not None:
        run.font.italic = bool(r_cfg.get("italic"))
    set_char_spacing(run, _pick(r_cfg, "char_spacing_pt", DEFAULT_RUN["char_spacing_pt"]))
    # 标题颜色修复：解析层 color_hex 四级回退后写回（None 不写，保持默认黑）
    color_hex = r_cfg.get("color_hex")
    if color_hex:
        try:
            from docx.shared import RGBColor
            run.font.color.rgb = RGBColor.from_string(color_hex)
        except Exception:
            pass


# ---- W5：按 kind 设置大纲级别（导航窗格 / TOC 域可识别，不改外观） ----
_KIND_OUTLINE_LEVEL = {
    "heading_1": 0,
    "heading_2": 1,
    "heading_3": 2,
}


def _set_outline_level(paragraph, para_config):
    """W5：按 kind 写 w:outlineLvl（0=一级，1=二级，2=三级）。

    不用内置 Heading 样式：python-docx 默认模板的 Heading 自带蓝色等外观，
    会覆盖原文档黑色标题；outlineLvl 只影响大纲/导航/TOC，不改字形。
    """
    level = _KIND_OUTLINE_LEVEL.get(para_config.get("kind"))
    if level is None:
        return
    p_pr = paragraph._p.get_or_add_pPr()
    outline = OxmlElement("w:outlineLvl")
    outline.set(qn("w:val"), str(level))
    # W4：w:outlineLvl 在 w:pPr 里有固定顺序（…w:jc 之后、w:rPr/w:sectPr 之前）。
    # 原来直接 append，遇到已有 w:rPr/w:sectPr 就排到它们后面，位置非法，
    # Word 有可能会忽略这段 pPr 的后续内容。
    p_pr.insert_element_before(
        outline, "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange"
    )


# ---- W3：公式对象 r:id 重绑 ----
def _rebind_formula_rids(element, doc, assets):
    """把公式 XML 里的旧 rId（r:id / r:embed / r:link）重绑到新文档。"""
    assets_by_rid = {a.get("old_rid"): a for a in (assets or [])}
    if not assets_by_rid:
        return
    for node in element.iter():
        for attr in (qn("r:id"), qn("r:embed"), qn("r:link")):
            old_rid = node.get(attr)
            if not old_rid or old_rid not in assets_by_rid:
                continue
            new_rid = _import_formula_part(doc, assets_by_rid[old_rid])
            if new_rid:
                node.set(attr, new_rid)


def _import_formula_part(doc, asset):
    """把公式引用的部件导入新文档，返回新 rId；失败返回 None。"""
    import os
    try:
        content_type = asset.get("content_type") or ""
        if content_type.startswith("image/"):
            path = asset.get("path") or ""
            if not path or not os.path.isfile(path):
                return None
            rid, _ = doc.part.get_or_add_image(path)
            return rid
        return _import_binary_part(doc, asset)
    except Exception:
        return None


def _import_binary_part(doc, asset):
    """OLE 等二进制部件：手工建 Part 并建立关系（python-docx 无原生 API）。"""
    import os
    from docx.opc.part import Part
    from docx.opc.constants import RELATIONSHIP_TYPE as RT

    path = asset.get("path") or ""
    if not path or not os.path.isfile(path):
        return None
    with open(path, "rb") as handle:
        blob = handle.read()
    package = doc.part.package
    partname = _next_embeddings_partname(package, path)
    part = Part(partname, asset.get("content_type") or "application/octet-stream",
                blob, package=package)
    return doc.part.relate_to(part, RT.OLE_OBJECT)


def _next_embeddings_partname(package, path):
    """在 word/embeddings/ 下找一个未占用的部件名（oleObjectN.ext）。"""
    import os
    from docx.opc.packuri import PackURI
    existing = {str(p.partname) for p in package.iter_parts()}
    ext = os.path.splitext(path)[1] or ".bin"
    i = 1
    while True:
        name = f"/word/embeddings/oleObject{i}{ext}"
        if name not in existing:
            return PackURI(name)
        i += 1


# ---- W6：域代码重建 ----
def _add_field_run(paragraph, r_cfg, field):
    """把带 field 标记的 run 重建为复杂域结构。

    结构：begin + instrText + separate + 结果 run + end；
    结果 run 保留原字体/字号等格式，域类型/指令来自解析层 field 标记。
    """
    instr = (field.get("code") or field.get("type") or "").strip()
    if not instr:
        instr = field.get("type") or ""

    # begin
    r = paragraph.add_run()
    fld_char = OxmlElement("w:fldChar")
    fld_char.set(qn("w:fldCharType"), "begin")
    r._r.append(fld_char)
    # instr
    r = paragraph.add_run()
    instr_text = OxmlElement("w:instrText")
    instr_text.set(qn("xml:space"), "preserve")
    instr_text.text = instr
    r._r.append(instr_text)
    # separate
    r = paragraph.add_run()
    fld_char = OxmlElement("w:fldChar")
    fld_char.set(qn("w:fldCharType"), "separate")
    r._r.append(fld_char)
    # 结果 run（保留格式）
    result_run = paragraph.add_run(r_cfg.get("text", "") or "")
    _apply_run_format(result_run, r_cfg)
    # end
    r = paragraph.add_run()
    fld_char = OxmlElement("w:fldChar")
    fld_char.set(qn("w:fldCharType"), "end")
    r._r.append(fld_char)


# ---- 超链接重建（整体往返测试暴露：按 runs 重建会丢超链接文字） ----
def _make_hyperlink_element(doc, h_cfg, run_cfgs=None):
    """构造 w:hyperlink 元素：外部 URL 走关系，内部锚点用 w:anchor。

    P3 修复后解析层的 runs 包含超链接内部的 run（带 hyperlink 标记），
    hyperlinks[i] 给出 {text, target, run_index, run_count}。
    这里按 run_count 把它们整体装进同一个 w:hyperlink，并逐 run 写回字体，
    不再把链接压成单个只带 rStyle="Hyperlink" 的 run（那会丢掉链接内字体）。
    """
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    link = OxmlElement("w:hyperlink")
    target = (h_cfg.get("target") or "").strip()
    if target:
        if doc is not None and target.startswith(("http://", "https://", "mailto:", "ftp://")):
            rid = doc.part.relate_to(target, RT.HYPERLINK, is_external=True)
            link.set(qn("r:id"), rid)
        else:
            link.set(qn("w:anchor"), target)

    cfgs = [cfg for cfg in (run_cfgs or [])]
    if not cfgs:
        cfgs = [{"text": h_cfg.get("text") or ""}]
    for cfg in cfgs:
        r = OxmlElement("w:r")
        r_pr = OxmlElement("w:rPr")
        r_style = OxmlElement("w:rStyle")
        r_style.set(qn("w:val"), "Hyperlink")
        r_pr.append(r_style)
        r.append(r_pr)
        t = OxmlElement("w:t")
        t.set(qn("xml:space"), "preserve")
        t.text = cfg.get("text") or ""
        r.append(t)
        link.append(r)
        if any(cfg.get(key) is not None for key in _RUN_FORMAT_KEYS):
            _apply_run_format(Run(r, None), cfg)
    return link


_RUN_FORMAT_KEYS = (
    "font_name", "east_asia_font", "font_size_pt",
    "bold", "italic", "char_spacing_pt", "color_hex",
)


def _has_field_run(runs):
    """段落里是否存在域结果 run（用于识别 fldChar / instrText 脚手架 run）。"""
    return any(cfg.get("field") for cfg in runs)


def _populate_paragraph(paragraph, para_config, doc=None):
    """把 paragraph block 渲染进已存在的段落对象（正文与页眉/页脚共用）。"""
    pf = paragraph.paragraph_format

    # 0) 先插回公式对象（OMML / OLE），保持其在编号 run 之前
    formula_xml = para_config.get("formula_xml") or []
    formula_assets = para_config.get("formula_assets") or []
    for xml_str in formula_xml:
        if not xml_str:
            continue
        try:
            element = parse_xml(xml_str)
        except Exception:
            continue
        # W3：公式 XML 里的 r:id 是原文档相对 ID，重建时重绑到新文档
        if doc is not None:
            _rebind_formula_rids(element, doc, formula_assets)
        paragraph._p.append(element)

    # 1) 对齐
    # W3：只在解析层真的读到了 w:jc 时才写；缺失就保持文档默认，
    # 不再无条件套两端对齐（rules 没规定“所有段落两端对齐”）。
    align = to_ooxml(para_config.get("alignment"))
    if align is not None:
        paragraph.alignment = align

    # 2) 首行缩进 / 悬挂缩进
    fli = para_config.get("first_line_indent_pt", None)
    if fli is not None:
        if fli > 0:
            pf.first_line_indent = Pt(fli)
        elif fli < 0:
            pf.left_indent       = Pt(-fli)
            pf.first_line_indent = Pt(fli)
        else:
            pf.first_line_indent = Pt(0)

    # 3) 段前 / 段后
    # W3：解析层没给值就不写，不再默认补 0pt / 6pt。
    sb = para_config.get("space_before_pt", None)
    if sb is not None:
        pf.space_before = Pt(sb)
    sa = para_config.get("space_after_pt", None)
    if sa is not None:
        pf.space_after = Pt(sa)

    # 4) 行距
    lsp_pt  = para_config.get("line_spacing_pt", None)
    lsp_mul = para_config.get("line_spacing_multiple", None)
    if lsp_pt is not None:
        pf.line_spacing = Pt(lsp_pt)
        pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    elif lsp_mul is not None:
        pf.line_spacing = float(lsp_mul)
    # W3：两者都为 None 时保持段落原样，不再强行写 1.5 倍行距。

    # 5) W5：按 kind 设大纲级别（标题识别/导航依赖它）
    _set_outline_level(paragraph, para_config)

    # 6) 逐 run 写入（W6：带 field 标记的 run 重建为域结构；
    #    超链接按 hyperlinks 的 run_index / run_count 原位插回，不再丢失）
    runs = para_config.get("runs") or []
    text = para_config.get("text", "") or ""
    hyperlinks = para_config.get("hyperlinks") or []

    # P3 修复后 hyperlinks[i] = {text, target, run_index, run_count}：
    # runs[run_index : run_index + run_count] 即该超链接内部的 run。
    # 旧的 after_run 字段解析层已不再输出（命名有歧义，见 P9）。
    links_by_start = {}
    for link_index, h in enumerate(hyperlinks):
        start = h.get("run_index")
        if isinstance(start, int) and start >= 0:
            links_by_start.setdefault(start, []).append((link_index, h))
    used_links = set()

    if not runs:
        # ★ 空段落：text 也为空时什么都不加，只留一个纯空段落
        if text:
            run = paragraph.add_run(text)
            _apply_run_format(run, DEFAULT_RUN)
    else:
        has_field = _has_field_run(runs)
        index = 0
        while index < len(runs):
            pending = links_by_start.get(index)
            if pending:
                link_index, head = pending[0]
                count = head.get("run_count")
                count = int(count) if isinstance(count, int) and count > 0 else 1
                count = max(1, min(count, len(runs) - index))
                paragraph._p.append(
                    _make_hyperlink_element(doc, head, runs[index:index + count])
                )
                used_links.add(link_index)
                index += count
                continue

            r_cfg = runs[index]
            field = r_cfg.get("field")
            run_text = r_cfg.get("text") or ""
            if field:
                _add_field_run(paragraph, r_cfg, field)
            elif not run_text and r_cfg.get("hyperlink") is None and has_field:
                # 域段落里的空 run 是 fldChar / instrText 脚手架（域结构由
                # _add_field_run 重建），此处跳过，避免留下一堆空 run。
                pass
            else:
                # 带 hyperlink 标记但找不到对应 hyperlinks 条目的 run
                # （旧 JSON / 数据不一致）退化成普通 run，绝不丢文字。
                run = paragraph.add_run(run_text)
                _apply_run_format(run, r_cfg)
            index += 1

        # 兜底：run_index 对不上任何 run 的超链接，仍把文字补回段落末尾
        for link_index, h in enumerate(hyperlinks):
            if link_index in used_links:
                continue
            text_only = h.get("text") or ""
            if not text_only:
                continue
            paragraph._p.append(
                _make_hyperlink_element(doc, h, [{"text": text_only}])
            )

    return paragraph


def add_paragraph(doc, para_config: dict):
    """根据一个 paragraph block 生成段落（正文路径）。"""
    if not isinstance(para_config, dict):
        raise ValueError("paragraph 配置必须是 dict")

    # 兼容旧的 {"paragraphs": [...]} 配置；新契约仍使用单个 block。
    if "paragraphs" in para_config:
        paragraphs = para_config.get("paragraphs") or []
        if not paragraphs:
            raise ValueError("至少需要提供一个段落，请检查 'paragraphs' 字段")
        for item in paragraphs:
            item = dict(item)
            style = item.pop("format", {}) or {}
            item.update(style)
            add_paragraph(doc, item)
        return None

    paragraph = doc.add_paragraph()
    return _populate_paragraph(paragraph, para_config, doc=doc)


def add_paragraph_to(container, para_config: dict, doc=None):
    """W4：向页眉/页脚容器追加段落（container 为 section.header/footer 等）。"""
    paragraph = container.add_paragraph()
    return _populate_paragraph(paragraph, para_config, doc=doc)
