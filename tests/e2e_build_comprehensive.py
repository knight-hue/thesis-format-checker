# -*- coding: utf-8 -*-
"""构建综合测试文档 comprehensive_thesis.docx：
覆盖解析/复现两条链路的全部特性（论文常见元素）。

- 2 个 section：S1 纵向 A4（正文），S2 横向（附录表）
- S1 首页不同 + 奇偶页页眉页脚（页脚 PAGE 域）
- 封面/摘要/关键词/目录(TOC 域)/多级标题(样式+无空格编号+outlineLvl+自动编号numPr)
- 混排正文（多 run 分槽字体/粗斜/样式字号）、超链接、书签
- 图片 + 图题(SEQ 域)、合并表格 + 表题、MathType OLE 公式
- 参考文献、致谢
"""
import base64
import os

OUT = os.path.join(os.environ.get("TEMP", "/tmp"), "docx_parser_verify")
PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
PNG_PATH = os.path.join(OUT, "pixel.png")
DOCX_PATH = os.path.join(OUT, "comprehensive_thesis.docx")
OLE_BLOB = b"FAKEOLEBLOB-COMPREHENSIVE-0123456789"


def set_fonts(run, ascii_font, ea_font):
    from docx.oxml.ns import qn
    run.font.name = ascii_font
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.get_or_add_rFonts()
    rFonts.set(qn("w:ascii"), ascii_font)
    rFonts.set(qn("w:hAnsi"), ascii_font)
    rFonts.set(qn("w:eastAsia"), ea_font)


def _append_field_xml(paragraph, field_xml):
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls
    for child in parse_xml(f'<w:p {nsdecls("w")}>{field_xml}</w:p>'):
        paragraph._p.append(child)


def _fld(instruction, result="1"):
    """复杂域 XML：begin + instrText + separate + 结果 + end"""
    from docx.oxml.ns import nsdecls
    return (
        f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="begin"/></w:r>'
        f'<w:r {nsdecls("w")}><w:instrText xml:space="preserve"> {instruction} </w:instrText></w:r>'
        f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="separate"/></w:r>'
        f'<w:r {nsdecls("w")}><w:t>{result}</w:t></w:r>'
        f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="end"/></w:r>'
    )


def build():
    from docx import Document
    from docx.enum.style import WD_STYLE_TYPE
    from docx.enum.section import WD_SECTION, WD_ORIENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls, qn
    from docx.opc.part import Part
    from docx.opc.packuri import PackURI
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.shared import Pt, Inches

    with open(PNG_PATH, "wb") as f:
        f.write(base64.b64decode(PNG_B64))

    doc = Document()

    # ================= S1 页面：A4 纵向，首页不同 + 奇偶页页眉页脚 =================
    s1 = doc.sections[0]
    s1.page_width, s1.page_height = Inches(8.27), Inches(11.69)
    # 顺序不能反：先设标志，再取容器（W4）
    s1.different_first_page_header_footer = True
    s1.even_page_headers_footers = True

    odd_header = s1.header
    odd_header.is_linked_to_previous = False
    odd_header.paragraphs[0].text = "吉林大学硕士学位论文"
    odd_header.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

    even_header = s1.even_page_header
    even_header.is_linked_to_previous = False
    even_header.paragraphs[0].text = "计算机科学与技术学院"
    even_header.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

    first_header = s1.first_page_header
    first_header.is_linked_to_previous = False
    first_header.paragraphs[0].text = ""  # 首页无页眉

    footer = s1.footer
    footer.is_linked_to_previous = False
    fp = footer.paragraphs[0]
    fp.text = ""
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _append_field_xml(fp, _fld("PAGE", "1"))

    # ================= 样式 =================
    h2_style = doc.styles.add_style("标题 2", WD_STYLE_TYPE.PARAGRAPH)
    h2_style.font.name = "黑体"
    h2_style.font.size = Pt(14)
    small5 = doc.styles.add_style("小五正文", WD_STYLE_TYPE.PARAGRAPH)
    small5.font.name = "Times New Roman"
    small5.font.size = Pt(9)
    small5.font.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    # ================= 封面标题 =================
    p = doc.add_paragraph("吉林大学硕士学位论文")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for r in p.runs:
        set_fonts(r, "Times New Roman", "黑体")
        r.font.size = Pt(22)
        r.font.bold = True
    p = doc.add_paragraph("基于深度学习的文本格式校验研究")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for r in p.runs:
        set_fonts(r, "Times New Roman", "宋体")
        r.font.size = Pt(16)

    # ================= 摘要 =================
    p = doc.add_paragraph("摘 要")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for r in p.runs:
        set_fonts(r, "Times New Roman", "黑体")
        r.font.size = Pt(16)
    p = doc.add_paragraph("论文格式校验系统将 DOCX 文档解析为结构化 JSON 契约，"
                          "再根据修改后的契约重建新的 DOCX 文档。本文围绕解析与写回两条链路，"
                          "对标题识别、表格合并、公式对象、域代码与页眉页脚等环节进行了完整测试。")
    for r in p.runs:
        set_fonts(r, "Times New Roman", "宋体")
        r.font.size = Pt(12)
    p = doc.add_paragraph("关键词：格式校验；文档解析；契约重建；DOCX")
    for r in p.runs:
        set_fonts(r, "Times New Roman", "宋体")
        r.font.size = Pt(12)
        r.font.bold = True

    # ================= 目录 =================
    p = doc.add_paragraph("目 录")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for r in p.runs:
        set_fonts(r, "Times New Roman", "黑体")
        r.font.size = Pt(16)
    toc_para = doc.add_paragraph()
    _append_field_xml(toc_para, _fld('TOC \\o "1-3" \\h \\z \\u', "1.1 研究背景 ......... 3"))

    # ================= 正文：多级标题 + 混排段落 =================
    p = doc.add_paragraph("第1章 绪论", style="Heading 1")
    for r in p.runs:
        set_fonts(r, "Times New Roman", "黑体")
    p = doc.add_paragraph()
    r1 = p.add_run("近年来，")
    set_fonts(r1, "Times New Roman", "宋体")
    r1.font.size = Pt(12)
    r2 = p.add_run("deep learning")
    set_fonts(r2, "Times New Roman", "Times New Roman")
    r2.font.size = Pt(12)
    r2.font.italic = True
    r3 = p.add_run("方法在自然语言处理任务上取得了显著进展，")
    set_fonts(r3, "Times New Roman", "宋体")
    r3.font.size = Pt(12)
    r4 = p.add_run("如图 2-1 所示。")
    set_fonts(r4, "Times New Roman", "宋体")
    r4.font.size = Pt(12)
    r4.font.bold = True

    # 无空格编号标题（P4 宽松正则 + 样式）
    p = doc.add_paragraph("1.1研究背景", style="Heading 2")
    for r in p.runs:
        set_fonts(r, "Times New Roman", "黑体")

    # 样式字号 + 超链接正文段
    p = doc.add_paragraph("论文格式规范对字号有严格要求，本段由样式决定为小五号。", style=small5)
    for r in p.runs:
        set_fonts(r, "Times New Roman", "宋体")
    p = doc.add_paragraph("详见 ")
    rid = doc.part.relate_to("https://example.com/thesis", RT.HYPERLINK, is_external=True)
    link = parse_xml(
        f'<w:hyperlink {nsdecls("w")} {nsdecls("r")} r:id="{rid}" w:history="1">'
        f'<w:r><w:t>参考文献链接</w:t></w:r></w:hyperlink>'
    )
    p._p.append(link)
    p.add_run(" 之后的内容。")

    # 书签段
    p = doc.add_paragraph("这是带书签的正文段落，用于目录跳转。")
    p._p.insert(0, parse_xml(f'<w:bookmarkStart {nsdecls("w")} w:id="0" w:name="_Toc123"/>'))
    p._p.append(parse_xml(f'<w:bookmarkEnd {nsdecls("w")} w:id="0"/>'))

    # 样式标题 2
    p = doc.add_paragraph("1.2 相关工作", style=h2_style)
    for r in p.runs:
        set_fonts(r, "Times New Roman", "黑体")

    # outlineLvl 三级标题
    p = doc.add_paragraph("1.2.1 深度学习方法")
    pPr = p._p.get_or_add_pPr()
    pPr.append(parse_xml(f'<w:outlineLvl {nsdecls("w")} w:val="2"/>'))
    for r in p.runs:
        set_fonts(r, "Times New Roman", "黑体")

    # 自动编号标题：文本无编号，样式 + numPr（P4 场景 2）
    p = doc.add_paragraph("研究背景", style="Heading 2")
    pPr = p._p.get_or_add_pPr()
    pPr.insert(0, parse_xml(f'<w:numPr {nsdecls("w")}><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'))
    for r in p.runs:
        set_fonts(r, "Times New Roman", "黑体")

    # ================= 图片 + 图题（SEQ 域） =================
    doc.add_picture(PNG_PATH, width=Inches(1.5))
    p = doc.add_paragraph("图 2-1 系统架构图")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for r in p.runs:
        set_fonts(r, "Times New Roman", "宋体")
    _append_field_xml(p, _fld('SEQ 图 \\* ARABIC', "2"))

    # ================= 表题 + 合并表格 =================
    p = doc.add_paragraph("表 2-1 实验环境配置")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for r in p.runs:
        set_fonts(r, "Times New Roman", "宋体")
        r.font.bold = True

    table = doc.add_table(rows=3, cols=3)
    table.style = "Table Grid"
    table.cell(0, 0).merge(table.cell(0, 1))
    cell00 = table.cell(0, 0)
    para = cell00.paragraphs[0]
    rr = para.add_run("合并单元格标题")
    set_fonts(rr, "Times New Roman", "宋体")
    rr.font.size = Pt(10.5)
    table.cell(0, 2).merge(table.cell(2, 2))
    table.cell(0, 2).text = "纵合"
    cell10 = table.cell(1, 0)
    p10 = cell10.paragraphs[0]
    rA = p10.add_run("数量")
    set_fonts(rA, "Arial", "宋体")
    rB = p10.add_run("12")
    set_fonts(rB, "Times New Roman", "宋体")
    rB.font.size = Pt(10.5)
    cell20 = table.cell(2, 0)
    p20 = cell20.paragraphs[0]
    p20.style = small5
    r20 = p20.add_run("样式字号")
    set_fonts(r20, "Times New Roman", "宋体")

    # ================= MathType OLE 公式 + 编号 =================
    img_rid, _ = doc.part.get_or_add_image(PNG_PATH)
    ole_part = Part(PackURI("/word/embeddings/oleObject1.bin"),
                    "application/vnd.openxmlformats-officedocument.oleObject",
                    OLE_BLOB, package=doc.part.package)
    ole_rid = doc.part.relate_to(ole_part, RT.OLE_OBJECT)
    ole_para = doc.add_paragraph()
    ole_xml = (
        f'<w:object {nsdecls("w")} {nsdecls("r")} '
        f'xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">'
        f'<o:OLEObject Type="Embed" ProgID="Equation.DSMT4" r:id="{ole_rid}"/>'
        f'<v:shape style="width:36pt;height:18pt">'
        f'<v:imagedata r:id="{img_rid}"/>'
        f'</v:shape>'
        f'</w:object>'
    )
    ole_para._p.append(parse_xml(ole_xml))
    rnum = ole_para.add_run("(2-1)")
    set_fonts(rnum, "Times New Roman", "宋体")

    # ================= 参考文献 / 致谢 =================
    p = doc.add_paragraph("参考文献")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for r in p.runs:
        set_fonts(r, "Times New Roman", "黑体")
        r.font.size = Pt(16)
    for ref in ("[1] 张三, 李四. 深度学习综述[J]. 计算机学报, 2024, 47(1): 1-20.",
                "[2] Smith J. Neural Network Methods[M]. New York: Springer, 2023."):
        p = doc.add_paragraph(ref)
        for r in p.runs:
            set_fonts(r, "Times New Roman", "宋体")
            r.font.size = Pt(10.5)
    p = doc.add_paragraph("致 谢")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for r in p.runs:
        set_fonts(r, "Times New Roman", "黑体")
        r.font.size = Pt(16)
    p = doc.add_paragraph("感谢导师与实验室全体同学的帮助。")
    for r in p.runs:
        set_fonts(r, "Times New Roman", "宋体")
        r.font.size = Pt(12)

    # ================= S2：横向附录页（多 section / 不同页眉 / 普通表格） =================
    s2 = doc.add_section(WD_SECTION.NEW_PAGE)
    s2.orientation = WD_ORIENT.LANDSCAPE
    w, h = s2.page_width, s2.page_height
    s2.page_width, s2.page_height = h, w  # 交换宽高
    s2.different_first_page_header_footer = False
    s2.even_page_headers_footers = False
    s2.header.is_linked_to_previous = False
    s2.header.paragraphs[0].text = "附录部分"
    s2.header.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    s2.footer.is_linked_to_previous = False
    fp2 = s2.footer.paragraphs[0]
    fp2.text = ""
    fp2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _append_field_xml(fp2, _fld("PAGE", "2"))

    p = doc.add_paragraph("附录 A 数据说明")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for r in p.runs:
        set_fonts(r, "Times New Roman", "黑体")
    p = doc.add_paragraph("表 1-1 附录数据")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for r in p.runs:
        set_fonts(r, "Times New Roman", "宋体")
        r.font.bold = True
    t2 = doc.add_table(rows=2, cols=2)
    t2.style = "Table Grid"
    t2.cell(0, 0).text = "指标"
    t2.cell(0, 1).text = "数值"
    t2.cell(1, 0).text = "准确率"
    t2.cell(1, 1).text = "98.2%"

    doc.save(DOCX_PATH)
    print("built:", DOCX_PATH)
    print("blocks 数（正文区）:", len(doc.element.body))


if __name__ == "__main__":
    build()
