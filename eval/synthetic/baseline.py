# -*- coding: utf-8 -*-
"""合规基线论文构建器（按 jlu_cs 规范原文逐条对齐）。

生成一份"应当零问题"的论文 DOCX：页边距、题目/摘要/关键词/英文摘要、
多级标题、正文、图+图题、表+表题、参考文献、致谢全部按 config/rules 的
要求书写。注入器在此基础上做单点破坏，产出金标准样张。

基线自检（tests 由 eval 运行期执行）：解析 + 检测后 issues 应为空；
若有基线问题（如结构识别把某段认错），先修本文件而不是掩盖问题。
"""
import io

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

# ---- 字号（磅） ----
SZ_SAN = 16        # 三号
SZ_XIAO_SAN = 15   # 小三
SZ_SI = 14         # 四号
SZ_XIAO_SI = 12    # 小四
SZ_WU = 10.5       # 五号


def _png() -> bytes:
    import struct
    import zlib

    def chunk(kind, data):
        checksum = zlib.crc32(kind + data)
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x20\x60\xa0"))
        + chunk(b"IEND", b"")
    )


def set_font(run, name=None, east_asia=None, size=None, bold=None, italic=None):
    """同时设置西文/中文字体、字号、粗斜体（east_asia 走 rFonts 属性）。"""
    if name:
        run.font.name = name
    if east_asia:
        rpr = run._element.get_or_add_rPr()
        rfonts = rpr.get_or_add_rFonts()
        rfonts.set(qn("w:eastAsia"), east_asia)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if italic is not None:
        run.font.italic = italic


def _add_para(doc, text, *, east_asia, size, align=None, name="Times New Roman",
              bold=None, line_spacing=None):
    """添加一个段落并设置 run 级格式（整个段落一个 run）。"""
    p = doc.add_paragraph(text)
    if align is not None:
        p.alignment = align
    if line_spacing is not None:
        p.paragraph_format.line_spacing = line_spacing
    for r in p.runs:
        set_font(r, name=name, east_asia=east_asia, size=size, bold=bold)
    return p


def build_baseline(path, mutate=None):
    """构建合规基线论文并保存到 path。

    mutate(handles) 若提供：在保存前被调用，handles 提供各段句柄：
        {段名: python-docx 对象}，注入器在此改写（改字体/文本/顺序/页边距等）。
    返回 handles（供调用方记录金标准定位用）。
    """
    doc = Document()

    # ================= 页面设置（第1章） =================
    section = doc.sections[0]
    section.left_margin = Cm(3)
    section.right_margin = Cm(3)
    section.top_margin = Cm(2)
    section.bottom_margin = Cm(2)
    section.header_distance = Cm(1.5)
    section.footer_distance = Cm(1.5)

    # ================= 中文题目与摘要（第2章） =================
    title = _add_para(doc, "基于深度学习的论文格式校验研究",
                      east_asia="黑体", size=SZ_XIAO_SAN, align=WD_ALIGN_PARAGRAPH.CENTER)
    abstract_title = _add_para(doc, "摘要", east_asia="黑体", size=SZ_SAN,
                               align=WD_ALIGN_PARAGRAPH.CENTER)
    abstract_body = _add_para(doc, "论文格式校验系统将 DOCX 文档解析为结构化契约，"
                                   "并依据学院规范逐项比对格式要求，用于高校毕业论文格式校验。",
                              east_asia="宋体", size=SZ_XIAO_SI,
                              line_spacing=1.5)
    keyword_title = _add_para(doc, "关键字：", east_asia="黑体", size=SZ_SI,
                              align=WD_ALIGN_PARAGRAPH.LEFT)
    keyword_body = _add_para(doc, "格式校验，文档解析，契约重建，DOCX",
                             east_asia="黑体", size=SZ_SI,
                             align=WD_ALIGN_PARAGRAPH.LEFT)

    # ================= 英文题目与英文摘要 =================
    en_title = _add_para(doc, "Thesis Format Checking Based on Deep Learning",
                         name="Times New Roman", east_asia="黑体", size=SZ_XIAO_SAN,
                         align=WD_ALIGN_PARAGRAPH.CENTER)
    en_abstract_title = _add_para(doc, "Abstract", name="Times New Roman",
                                  east_asia="黑体", size=SZ_XIAO_SAN,
                                  align=WD_ALIGN_PARAGRAPH.CENTER, bold=True)
    en_abstract_body = _add_para(doc, "This system parses DOCX documents into a "
                                      "structured contract and checks formatting "
                                      "against college regulations.",
                                 name="Times New Roman", east_asia="宋体",
                                 size=SZ_XIAO_SI, line_spacing=1.5)
    en_keyword_title = _add_para(doc, "Keywords：", name="Times New Roman",
                                 east_asia="黑体", size=SZ_SI,
                                 align=WD_ALIGN_PARAGRAPH.LEFT)
    en_keyword_body = _add_para(doc, "format checking, document parsing, "
                                     "contract rebuild, DOCX",
                                name="Times New Roman", east_asia="黑体",
                                size=SZ_SI, align=WD_ALIGN_PARAGRAPH.LEFT)

    # ================= 标题与正文（第3/4章） =================
    h1_1 = _add_para(doc, "第1章 绪论", east_asia="黑体", size=SZ_SAN,
                     align=WD_ALIGN_PARAGRAPH.CENTER)
    body_1 = _add_para(doc, "近年来，深度学习在自然语言处理任务上取得了显著进展，"
                            "成为学术研究的热点方向。文献［1］对该领域进行了系统综述。",
                       east_asia="宋体", size=SZ_XIAO_SI, line_spacing=1.5)
    h2_1 = _add_para(doc, "1.1 研究背景", east_asia="黑体", size=SZ_SI,
                     align=WD_ALIGN_PARAGRAPH.LEFT)
    body_2 = _add_para(doc, "研究背景主要围绕文档格式校验的自动化展开，"
                            "传统人工检查效率较低。",
                       east_asia="宋体", size=SZ_XIAO_SI, line_spacing=1.5)
    h3_1 = _add_para(doc, "1.1.1 深度学习方法", east_asia="黑体", size=SZ_XIAO_SI,
                     align=WD_ALIGN_PARAGRAPH.LEFT)
    body_3 = _add_para(doc, "深度学习方法通过端到端训练在多个任务上取得了良好效果。",
                       east_asia="宋体", size=SZ_XIAO_SI, line_spacing=1.5)
    h2_2 = _add_para(doc, "1.2 相关工作", east_asia="黑体", size=SZ_SI,
                     align=WD_ALIGN_PARAGRAPH.LEFT)
    body_4 = _add_para(doc, "相关工作包括规则引擎、机器学习与检索增强生成等技术路线。",
                       east_asia="宋体", size=SZ_XIAO_SI, line_spacing=1.5)

    h1_2 = _add_para(doc, "第2章 系统设计", east_asia="黑体", size=SZ_SAN,
                     align=WD_ALIGN_PARAGRAPH.CENTER)
    h2_3 = _add_para(doc, "2.1 系统架构", east_asia="黑体", size=SZ_SI,
                     align=WD_ALIGN_PARAGRAPH.LEFT)
    body_5 = _add_para(doc, "系统整体架构如图2-1所示，包含解析、检测与写回三层。"
                            "文献［2］详细介绍了相关方法。",
                       east_asia="宋体", size=SZ_XIAO_SI, line_spacing=1.5)

    # ================= 图 + 图题（第5章） =================
    image_para = doc.add_paragraph()
    image_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = image_para.add_run()
    run.add_picture(io.BytesIO(_png()), width=Cm(4))
    fig_caption = _add_para(doc, "图2-1 系统架构图", east_asia="黑体", size=SZ_WU,
                            align=WD_ALIGN_PARAGRAPH.CENTER)

    body_6 = _add_para(doc, "实验环境配置如表2-1所示。",
                       east_asia="宋体", size=SZ_XIAO_SI, line_spacing=1.5)

    # ================= 表题 + 表格 =================
    table_caption = _add_para(doc, "表2-1 实验环境配置", east_asia="黑体",
                              size=SZ_WU, align=WD_ALIGN_PARAGRAPH.CENTER)
    table = doc.add_table(rows=2, cols=2)
    table.style = "Table Grid"
    cells_text = (("指标", "数值"), ("准确率", "98.2%"))
    for r_idx, row in enumerate(cells_text):
        for c_idx, text in enumerate(row):
            cell = table.cell(r_idx, c_idx)
            cell.text = ""
            r = cell.paragraphs[0].add_run(text)
            set_font(r, name="Times New Roman", east_asia="宋体", size=SZ_WU)

    body_7 = _add_para(doc, "本文验证了该系统的有效性与可靠性。",
                       east_asia="宋体", size=SZ_XIAO_SI, line_spacing=1.5)

    # ================= 参考文献（第6章） =================
    reference_title = _add_para(doc, "参考文献", east_asia="黑体", size=SZ_SAN,
                                align=WD_ALIGN_PARAGRAPH.CENTER)
    ref_entry_1 = _add_para(doc, "［1］张三，李四．深度学习综述[J]．计算机学报，"
                                 "2024，47(1)：1-20．",
                            east_asia="宋体", size=SZ_WU)
    ref_entry_2 = _add_para(doc, "［2］Smith J．Neural Network Methods[M]．"
                                 "New York：Springer，2023：1-100．",
                            east_asia="宋体", size=SZ_WU)

    # ================= 致谢（第7章） =================
    ack_title = _add_para(doc, "致    谢", east_asia="黑体", size=SZ_SAN,
                          align=WD_ALIGN_PARAGRAPH.CENTER)
    ack_body = _add_para(doc, "感谢导师与实验室全体同学的帮助。",
                         east_asia="宋体", size=SZ_XIAO_SI, line_spacing=1.5)

    handles = {
        "section": section,
        "title": title, "abstract_title": abstract_title,
        "abstract_body": abstract_body, "keyword_title": keyword_title,
        "keyword_body": keyword_body,
        "en_title": en_title, "en_abstract_title": en_abstract_title,
        "en_abstract_body": en_abstract_body, "en_keyword_title": en_keyword_title,
        "en_keyword_body": en_keyword_body,
        "h1_1": h1_1, "body_1": body_1, "h2_1": h2_1, "body_2": body_2,
        "h3_1": h3_1, "body_3": body_3, "h2_2": h2_2, "body_4": body_4,
        "h1_2": h1_2, "h2_3": h2_3, "body_5": body_5,
        "image_para": image_para, "fig_caption": fig_caption,
        "body_6": body_6, "table_caption": table_caption, "table": table,
        "body_7": body_7,
        "reference_title": reference_title,
        "ref_entry_1": ref_entry_1, "ref_entry_2": ref_entry_2,
        "ack_title": ack_title, "ack_body": ack_body,
    }
    if mutate is not None:
        mutate(handles)
    doc.save(path)
    return handles


def build_scaled_paper(path, n_body_paragraphs, n_tables, n_figures):
    """构建用于性能规模扫描的长论文（不做合规保证，只求结构相似与可解析）。

    返回段落数，便于换算文档规模。
    """
    doc = Document()
    section = doc.sections[0]
    section.left_margin = Cm(3)
    section.right_margin = Cm(3)
    section.top_margin = Cm(2)
    section.bottom_margin = Cm(2)

    _add_para(doc, "第1章 绪论", east_asia="黑体", size=SZ_SAN,
              align=WD_ALIGN_PARAGRAPH.CENTER)
    para_count = 1
    for i in range(n_body_paragraphs):
        _add_para(doc, f"这是用于性能测试的第{i + 1}段正文，包含中文与 Times New "
                       f"Roman 西文内容，用于模拟真实论文的正文规模。",
                  east_asia="宋体", size=SZ_XIAO_SI, line_spacing=1.5)
        para_count += 1
        if n_tables and i % max(1, n_body_paragraphs // n_tables) == 0:
            _add_para(doc, f"表2-{i // max(1, n_body_paragraphs // n_tables) + 1} "
                           f"性能测试表", east_asia="黑体", size=SZ_WU,
                      align=WD_ALIGN_PARAGRAPH.CENTER)
            t = doc.add_table(rows=3, cols=3)
            t.style = "Table Grid"
            for r_idx in range(3):
                for c_idx in range(3):
                    cell = t.cell(r_idx, c_idx)
                    cell.text = ""
                    r = cell.paragraphs[0].add_run(f"数据{r_idx}{c_idx}")
                    set_font(r, name="Times New Roman", east_asia="宋体", size=SZ_WU)
            para_count += 1
        if n_figures and i % max(1, n_body_paragraphs // n_figures) == 1:
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = p.add_run()
            run.add_picture(io.BytesIO(_png()), width=Cm(3))
            _add_para(doc, f"图2-{i // max(1, n_body_paragraphs // n_figures) + 1} "
                           f"性能测试图", east_asia="黑体", size=SZ_WU,
                      align=WD_ALIGN_PARAGRAPH.CENTER)
            para_count += 1
    doc.save(path)
    return para_count
