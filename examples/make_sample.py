# -*- coding: utf-8 -*-
"""生成一份「故意带格式错误」的合成论文，用于演示与自测。

**不含任何真实论文内容**：所有文字都是占位句，人名统一用「张三」，
机构统一用「示例大学」。生成的文件只落在本地 `examples/sample.docx`。

用法：

    python examples/make_sample.py

生成后把 `examples/sample.docx` 上传到页面，即可看到若干条格式问题。
故意留下的问题：
  1. 标题字号偏小（14pt，规范要求三号 16pt）且未居中
  2. 缺「摘要」标题
  3. 正文行距 1.0（规范要求 1.5 倍）
  4. 正文字号 10.5pt（规范要求小四 12pt）
  5. 正文西文字体 Arial（规范要求 Times New Roman）
  6. 表格缺表题
  7. 图题位于图片上方（规范要求下方）
  8. 参考文献条目末尾无句点
"""
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt

OUT_DIR = Path(__file__).resolve().parent
OUT_FILE = OUT_DIR / "sample.docx"


def _set_font(paragraph, name="宋体", size_pt=12, ascii_font="Times New Roman"):
    """给段落内所有 run 设置中西文字体与字号。"""
    for run in paragraph.runs:
        run.font.size = Pt(size_pt)
        run.font.name = ascii_font
        run._element.rPr.rFonts.set(qn("w:eastAsia"), name)


def main() -> None:
    doc = Document()

    # ---- 1. 标题：字号偏小且未居中 ----
    title = doc.add_paragraph()
    title.add_run("基于示例数据的格式校验演示")
    _set_font(title, name="黑体", size_pt=14, ascii_font="Arial")
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT

    # ---- 2. 缺「摘要」标题，直接写摘要正文 ----
    abstract = doc.add_paragraph()
    abstract.add_run(
        "本文仅用于演示格式校验功能，全部文字均为合成的占位内容，"
        "不含任何真实论文数据。"
    )
    _set_font(abstract)

    # ---- 3~5. 正文：行距 1.0、字号 10.5、西文 Arial ----
    for text in (
        "第一章 引言",
        "本节用来说明正文判定项。示例段落故意使用 1.0 倍行距、10.5 磅字号，"
        "并把西文字体设为 Arial，以便观察系统给出的问题条目。",
        "本节中的英文缩写 CNN 首次出现时未注明中文含义，用于演示术语类检查。",
    ):
        para = doc.add_paragraph()
        para.add_run(text)
        _set_font(para, size_pt=10.5, ascii_font="Arial")
        para.paragraph_format.line_spacing = 1.0

    # ---- 6. 表格缺表题 ----
    table = doc.add_table(rows=2, cols=2)
    table.style = "Table Grid"
    table.cell(0, 0).text = "示例项"
    table.cell(0, 1).text = "取值"
    table.cell(1, 0).text = "占位"
    table.cell(1, 1).text = "占位"

    # ---- 7. 图题位于图片上方（规范要求下方） ----
    caption = doc.add_paragraph()
    caption.add_run("图 1-1 示例结构图")
    _set_font(caption, size_pt=10.5)
    caption.alignment = WD_ALIGN_PARAGRAPH.CENTER

    body = doc.add_paragraph()
    body.add_run("（此处为示意图占位，实际演示时可自行插入任意图片。）")
    _set_font(body, size_pt=10.5, ascii_font="Arial")

    # ---- 8. 参考文献：条目末尾无句点 ----
    refs_title = doc.add_paragraph()
    refs_title.add_run("参考文献")
    _set_font(refs_title, name="黑体", size_pt=14, ascii_font="Arial")

    ref = doc.add_paragraph()
    ref.add_run("[1] 张三. 示例研究[J]. 示例学报, 2020")
    _set_font(ref, size_pt=10.5)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    doc.save(OUT_FILE)
    print(f"已生成：{OUT_FILE}")


if __name__ == "__main__":
    main()
