# -*- coding: utf-8 -*-
"""整体测试——A 组：解析链路（DOCX → JSON）。

对 comprehensive_thesis.docx 做完整解析，核对 JSON 结构/字段/语义分类，
覆盖 P1-P7 全部修复点在新文档上的整体表现。
"""
import json
import os
import sys

OUT = os.path.join(os.environ.get("TEMP", "/tmp"), "docx_parser_verify")
DOCX = os.path.join(OUT, "comprehensive_thesis.docx")
PARSE_DIR = os.path.join(OUT, "e2e_parse")

checks = []


def check(name, cond, detail=""):
    checks.append((name, bool(cond), detail))


def main():
    import shutil
    shutil.rmtree(PARSE_DIR, ignore_errors=True)

    from backend.parser.docx_to_json import docx_to_json
    json_path = docx_to_json(DOCX, PARSE_DIR)
    doc = json.load(open(json_path, encoding="utf-8"))

    # ---- 顶层结构 ----
    check("顶层 schema/file_name/sections/blocks",
          doc.get("schema_version") and doc.get("file_name") and "sections" in doc and "blocks" in doc,
          str(list(doc.keys())))
    blocks = doc["blocks"]
    check("blocks 非空且有序", len(blocks) >= 20, str(len(blocks)))

    # ---- sections：S1 纵向 A4 / S2 横向 ----
    sections = doc["sections"]
    check("sections == 2", len(sections) == 2, str(len(sections)))
    s1, s2 = sections[0], sections[1]
    check("S1 paper A4", s1.get("paper_size") == "A4", str(s1.get("paper_size")))
    check("S1 纵向", s1.get("orientation") == "portrait", str(s1.get("orientation")))
    check("S2 横向", s2.get("orientation") == "landscape", str(s2.get("orientation")))
    check("S2 纸张不同", s2.get("paper_size") == "A4", str(s2.get("paper_size")))

    # ---- S1 页眉页脚三类 ----
    h1 = s1.get("headers") or []
    f1 = s1.get("footers") or []
    h1_types = {h.get("type"): h for h in h1}
    f1_types = {f.get("type"): f for f in f1}
    check("S1 headers 三类(default/even/first_page)",
          set(h1_types) == {"default", "even_page", "first_page"}, str(h1_types))
    check("S1 odd 页眉文字", "吉林大学硕士学位论文" in (h1_types.get("default") or {}).get("text", ""),
          json.dumps(h1, ensure_ascii=False)[:200])
    check("S1 even 页眉文字", "计算机科学与技术学院" in (h1_types.get("even_page") or {}).get("text", ""),
          json.dumps(h1, ensure_ascii=False)[:200])
    check("S1 first_page 页眉为空", (h1_types.get("first_page") or {}).get("text", "") == "",
          json.dumps(h1, ensure_ascii=False)[:200])
    # S1 页脚 PAGE 域
    footer_has_page = any(
        r.get("field", {}).get("type") == "PAGE"
        for f in f1 for pg in f.get("paragraphs", []) for r in pg.get("runs", [])
    )
    check("S1 页脚 PAGE 域", footer_has_page, json.dumps(f1, ensure_ascii=False)[:300])
    # S2 页眉不同
    h2 = s2.get("headers") or []
    check("S2 页眉独立且不同", any("附录部分" in h.get("text", "") for h in h2),
          json.dumps(h2, ensure_ascii=False)[:200])

    # ---- 标题分类（P4） ----
    text_kind = {}
    for b in blocks:
        if b.get("type") == "paragraph" and b.get("text"):
            text_kind[b["text"]] = b.get("kind")
    check("封面标题 title（context 重标注）", text_kind.get("吉林大学硕士学位论文") == "title", str(text_kind.get("吉林大学硕士学位论文")))
    check("摘要 abstract_title", text_kind.get("摘 要") == "abstract_title", str(text_kind.get("摘 要")))
    check("关键词 keyword_title", text_kind.get("关键词：格式校验；文档解析；契约重建；DOCX") == "keyword_title",
          str(text_kind.get("关键词：格式校验；文档解析；契约重建；DOCX")))
    check("目录 toc", text_kind.get("目 录") == "toc", str(text_kind.get("目 录")))
    check("第1章 绪论 heading_1（样式）", text_kind.get("第1章 绪论") == "heading_1",
          str(text_kind.get("第1章 绪论")))
    check("1.1研究背景 heading_2（无空格编号）", text_kind.get("1.1研究背景") == "heading_2",
          str(text_kind.get("1.1研究背景")))
    check("1.2 相关工作 heading_2（样式标题2）", text_kind.get("1.2 相关工作") == "heading_2",
          str(text_kind.get("1.2 相关工作")))
    check("1.2.1 深度学习方法 heading_3（outlineLvl）", text_kind.get("1.2.1 深度学习方法") == "heading_3",
          str(text_kind.get("1.2.1 深度学习方法")))
    check("自动编号标题 heading_2（样式+numPr，文本无编号）", text_kind.get("研究背景") == "heading_2",
          str(text_kind.get("研究背景")))
    check("参考文献 reference_title", text_kind.get("参考文献") == "reference_title",
          str(text_kind.get("参考文献")))
    check("致 谢 acknowledgement_title", text_kind.get("致 谢") == "acknowledgement_title",
          str(text_kind.get("致 谢")))
    fig_cap = next((k for k in text_kind if k.startswith("图 2-1")), None)
    check("图题 figure_caption", text_kind.get(fig_cap) == "figure_caption",
          str({k: text_kind[k] for k in list(text_kind)[:24]}))
    check("表题 table_caption", text_kind.get("表 2-1 实验环境配置") == "table_caption",
          str(text_kind.get("表 2-1 实验环境配置")))
    check("S2 表题 table_caption", text_kind.get("表 1-1 附录数据") == "table_caption",
          str(text_kind.get("表 1-1 附录数据")))

    # ---- 域（P7） ----
    toc_block = next(b for b in blocks
                     if b.get("type") == "paragraph" and b.get("text", "").startswith("1.1 研究背景"))
    toc_fields = [r.get("field", {}).get("type") for r in toc_block.get("runs", []) if r.get("field")]
    check("TOC 块 field=TOC", "TOC" in toc_fields, str(toc_fields))
    check("TOC 块 fields 列表", "TOC" in toc_block.get("fields", []), str(toc_block.get("fields")))
    seq_block = next(b for b in blocks if b.get("type") == "paragraph" and "SEQ" in (b.get("fields") or []))
    check("SEQ 域识别", seq_block is not None, str([b.get("fields") for b in blocks if b.get("fields")]))

    # ---- 公式（P6/W3） ----
    formula_blocks = [b for b in blocks if b.get("type") == "paragraph" and b.get("formula_type") == "ole"]
    check("OLE 公式块", len(formula_blocks) == 1, str(len(formula_blocks)))
    if formula_blocks:
        fb = formula_blocks[0]
        check("公式块 formula_xml", bool(fb.get("formula_xml")), str(fb.get("formula_xml"))[:80])
        assets = fb.get("formula_assets") or []
        check("公式块 formula_assets>=2", len(assets) >= 2, json.dumps(assets, ensure_ascii=False)[:300])
        check("公式块 formula_preview", fb.get("formula_preview", {}).get("width_inch") == 0.5,
              str(fb.get("formula_preview")))
        check("公式编号 run 保留", any("(2-1)" in (r.get("text") or "") for r in fb.get("runs", [])),
              str(fb.get("runs")))

    # ---- 图片 ----
    img_blocks = [b for b in blocks if b.get("type") == "image"]
    check("图片块存在", len(img_blocks) == 1, str(len(img_blocks)))
    if img_blocks:
        check("图片块尺寸", img_blocks[0].get("width_inch") == 1.5, str(img_blocks[0]))

    # ---- 表格（P1/P2/P3） ----
    tbl_blocks = [b for b in blocks if b.get("type") == "table"]
    check("表格块 x2（正文+附录）", len(tbl_blocks) == 2, str(len(tbl_blocks)))
    t1 = tbl_blocks[0]
    check("表格 n_rows/n_cols", t1.get("n_rows") == 3 and t1.get("n_cols") == 3,
          str((t1.get("n_rows"), t1.get("n_cols"))))
    cells = t1["cells"]
    check("gridSpan 去重（合并标题只 1 条）", sum(1 for c in cells if c["text"] == "合并单元格标题") == 1,
          str([c["text"] for c in cells]))
    span_cell = next(c for c in cells if c["text"] == "合并单元格标题")
    check("grid_span=2", span_cell.get("grid_span") == 2, str(span_cell))
    check("vmerge restart", any(c.get("vmerge") == "restart" for c in cells), str(cells))
    check("vmerge continue x2", sum(1 for c in cells if c.get("vmerge") == "continue") == 2,
          str([c.get("vmerge") for c in cells]))
    cell10 = next(c for c in cells if c["text"] == "数量12")
    check("单元格分槽字体（font_name=西文槽）", cell10.get("font_name") == "Arial", str(cell10))
    check("单元格 east_asia_font=宋体", cell10.get("east_asia_font") == "宋体", str(cell10))
    check("单元格 runs 明细（2 个 run 不同西文字体）",
          [(r.get("text"), r.get("font_name")) for r in cell10.get("runs", [])] ==
          [("数量", "Arial"), ("12", "Times New Roman")],
          str(cell10.get("runs")))
    style_cell = next(c for c in cells if c["text"] == "样式字号")
    check("样式字号回退 9pt（P2 四级回退）", style_cell.get("font_size_pt") == 9.0,
          str(style_cell.get("font_size_pt")))
    check("列宽比例长度", len(t1.get("col_width_ratio", [])) == 3, str(t1.get("col_width_ratio")))

    # ---- 超链接正文段：runs 与 text 必须同口径（P3） ----
    link_para = next(b for b in blocks
                     if b.get("type") == "paragraph" and "参考文献链接" in (b.get("text") or ""))
    check("超链接段 text 含链接文字", "参考文献链接" in link_para["text"], str(link_para["text"]))
    link_runs = link_para.get("runs", [])
    link_runs_text = "".join(r.get("text", "") for r in link_runs)
    check("超链接段 runs 含链接文字（P3：runs 与 text 同口径）",
          "参考文献链接" in link_runs_text, str(link_runs_text))
    check("超链接段 runs 拼起来等于 text", link_runs_text == link_para["text"],
          f"{link_runs_text!r} != {link_para['text']!r}")
    tagged = [r for r in link_runs if r.get("hyperlink") is not None]
    check("超链接内 run 带 hyperlink 下标（0 起编号）",
          len(tagged) == 1 and tagged[0]["hyperlink"] == 0,
          str([(r.get("text"), r.get("hyperlink")) for r in link_runs]))
    link_meta = link_para.get("hyperlinks", [])
    check("hyperlinks 元数据与 runs 下标一致",
          len(link_meta) == 1 and link_meta[0]["run_index"] == link_runs.index(tagged[0])
          and link_meta[0]["run_count"] == 1,
          str(link_meta))

    # ---- 契约校验 ----
    from backend.document_contract import DocumentContract
    try:
        DocumentContract.model_validate(doc)
        check("DocumentContract 校验通过", True, "")
    except Exception as e:
        check("DocumentContract 校验通过", False, str(e))

    # ---- 汇总 ----
    print("\n==== A 组：解析链路（DOCX -> JSON）====")
    failed = 0
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if not ok else ""))
        if not ok:
            failed += 1
    print(f"\n{len(checks) - failed}/{len(checks)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(2)
