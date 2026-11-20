# -*- coding: utf-8 -*-
"""整体测试——B/C 组：复现链路（JSON → DOCX）+ 往返一致性。

B 组：对 comprehensive_thesis.docx 的解析 JSON 做无修改复现，核对复现稿
     的文本/表格合并/页眉页脚/大纲级别/公式重绑/域重建/图片关系。
C 组：复现稿再解析，与原 JSON 对比关键内容，测往返保真度。
"""
import base64
import json
import os
import shutil
import sys

OUT = os.path.join(os.environ.get("TEMP", "/tmp"), "docx_parser_verify")
DOCX = os.path.join(OUT, "comprehensive_thesis.docx")
PARSE_DIR = os.path.join(OUT, "e2e_parse")
REBUILD_DIR = os.path.join(OUT, "e2e_rebuild")
REP = os.path.join(OUT, "e2e_reparse")
REBUILT = os.path.join(REBUILD_DIR, "rebuilt.docx")
PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
OLE_BLOB = b"FAKEOLEBLOB-COMPREHENSIVE-0123456789"

checks = []


def check(name, cond, detail=""):
    checks.append((name, bool(cond), detail))


def main():
    from backend.parser.docx_to_json import docx_to_json
    from backend.write_back.json_to_docx import json_to_docx

    doc = json.load(open(os.path.join(PARSE_DIR, "document.json"), encoding="utf-8"))

    shutil.rmtree(REBUILD_DIR, ignore_errors=True)
    os.makedirs(REBUILD_DIR, exist_ok=True)
    json_to_docx(os.path.join(PARSE_DIR, "document.json"), REBUILT)

    # ================= B 组：复现正确性 =================
    from docx import Document
    from docx.oxml.ns import qn as _qn
    VML = "{urn:schemas-microsoft-com:vml}"
    OFFICE = "{urn:schemas-microsoft-com:office:office}"
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    rd = Document(REBUILT)

    # B1 文本序列保留（关键句，含表格文本）
    all_text = "\n".join(p.text for p in rd.paragraphs)
    for t in rd.tables:
        for row in t.rows:
            for c in row.cells:
                all_text += "\n" + c.text
    for snippet in ("吉林大学硕士学位论文", "摘 要", "关键词：格式校验",
                    "第1章 绪论", "1.1研究背景", "1.2 相关工作", "1.2.1 深度学习方法",
                    "deep learning", "研究背景", "表 2-1 实验环境配置",
                    "参考文献", "[1] 张三, 李四", "致 谢", "图 2-1 系统架构图2",
                    "附录 A 数据说明", "表 1-1 附录数据", "98.2%"):
        check(f"B 文本保留：{snippet[:14]}", snippet in all_text, all_text[:200] + "...")

    # B2 表格：合并 XML + 多 run 字体
    tbl = rd.tables[0]
    tc00 = tbl.rows[0]._tr.findall(_qn("w:tc"))[0]
    gs = tc00.find(_qn("w:tcPr") + "/" + _qn("w:gridSpan"))
    check("B 表格 gridSpan=2", gs is not None and gs.get(_qn("w:val")) == "2",
          str(gs.attrib if gs is not None else None))
    tc02 = tbl.rows[0]._tr.findall(_qn("w:tc"))[1]
    vm = tc02.find(_qn("w:tcPr") + "/" + _qn("w:vMerge"))
    check("B 表格 vMerge restart", vm is not None and vm.get(_qn("w:val")) == "restart",
          str(vm.attrib if vm is not None else None))
    tc12 = tbl.rows[1]._tr.findall(_qn("w:tc"))[2]
    vm12 = tc12.find(_qn("w:tcPr") + "/" + _qn("w:vMerge"))
    check("B 表格 vMerge continue", vm12 is not None and vm12.get(_qn("w:val")) is None,
          str(vm12.attrib if vm12 is not None else None))
    cell10 = tbl.cell(1, 0)
    cell10_fonts = []
    for r in cell10.paragraphs[0].runs:
        rPr = r._element.rPr
        rfonts = rPr.find(_qn("w:rFonts")) if rPr is not None else None
        cell10_fonts.append((r.text, rfonts.get(_qn("w:ascii")) if rfonts is not None else None))
    check("B 单元格多 run 分槽字体", cell10_fonts == [("数量", "Arial"), ("12", "Times New Roman")],
          str(cell10_fonts))

    # B3 页眉页脚
    s1 = rd.sections[0]
    check("B 页眉 odd 文字", "吉林大学硕士学位论文" in s1.header.paragraphs[0].text,
          s1.header.paragraphs[0].text)
    check("B 页眉 even 文字", "计算机科学与技术学院" in s1.even_page_header.paragraphs[0].text,
          s1.even_page_header.paragraphs[0].text)
    footer_xml = s1.footer._element.xml
    check("B 页脚 PAGE 域", "PAGE" in footer_xml and "fldChar" in footer_xml, footer_xml[:150])
    check("B S2 页眉不同", "附录部分" in rd.sections[1].header.paragraphs[0].text,
          rd.sections[1].header.paragraphs[0].text)

    # B4 大纲级别
    outline_vals = {}
    for p in rd.paragraphs:
        pPr = p._p.pPr
        if pPr is None:
            continue
        ol = pPr.find(_qn("w:outlineLvl"))
        if ol is not None:
            outline_vals[p.text] = ol.get(_qn("w:val"))
    check("B outlineLvl 第1章=0", outline_vals.get("第1章 绪论") == "0", str(outline_vals))
    check("B outlineLvl 二级 x3", sum(1 for t in ("1.1研究背景", "1.2 相关工作", "研究背景")
                                      if outline_vals.get(t) == "1") == 3, str(outline_vals))
    check("B outlineLvl 三级=2", outline_vals.get("1.2.1 深度学习方法") == "2", str(outline_vals))

    # B5 公式 r:id 重绑 + blob
    src_assets = next(b for b in doc["blocks"]
                      if b.get("type") == "paragraph" and b.get("formula_xml")).get("formula_assets") or []
    found_obj = None
    for p in rd.paragraphs:
        for obj in p._p.iter(_qn("w:object")):
            found_obj = obj
            break
        if found_obj is not None:
            break
    check("B 公式对象存在", found_obj is not None)
    if found_obj is not None:
        img_node = found_obj.find(".//" + VML + "imagedata")
        ole_node = found_obj.find(".//" + OFFICE + "OLEObject")
        img_rid = img_node.get(_qn("r:id")) if img_node is not None else None
        ole_rid = ole_node.get(_qn("r:id")) if ole_node is not None else None
        check("B imagedata r:id 重绑", img_rid is not None and img_rid in rd.part.related_parts,
              str(img_rid))
        check("B OLEObject r:id 重绑", ole_rid is not None and ole_rid in rd.part.related_parts,
              str(ole_rid))
        if img_rid in rd.part.related_parts:
            check("B 图片 blob 一致", rd.part.related_parts[img_rid].blob == base64.b64decode(PNG_B64),
                  str(len(rd.part.related_parts[img_rid].blob)))
        if ole_rid in rd.part.related_parts:
            check("B OLE blob 一致", rd.part.related_parts[ole_rid].blob == OLE_BLOB,
                  str(rd.part.related_parts[ole_rid].blob[:20]))
        src_img_asset = next(a for a in src_assets if a.get("content_type", "").startswith("image/"))
        asset_path = src_img_asset.get("path", "")
        # 解析层统一写 "images/<文件名>"，以 JSON 所在目录为基准（与 fix 层同口径）
        if not os.path.isabs(asset_path):
            asset_path = os.path.join(PARSE_DIR, asset_path)
        check("B 公式预览图与源一致", len(rd.part.related_parts[img_rid].blob) ==
              os.path.getsize(asset_path), str(img_rid))

    # B6 域重建 + updateFields（修复③：已关闭，避免 Word 打开弹安全声明）
    pxml = rd.element.body.xml
    check("B TOC 域 instrText", 'TOC \\o' in pxml, "")
    settings = rd.settings.element
    upd = settings.find(_qn("w:updateFields"))
    check("B updateFields 已关闭（不弹安全声明）", upd is None,
          str(upd.attrib if upd is not None else None))

    # B7 标题颜色往返（修复①：run 颜色四级回退写回）
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    heading1 = next(p for p in rd.paragraphs if p.text.strip() == "第1章 绪论")
    h1_color = next((r._element.rPr.find(W + "color").get(W + "val")
                     for r in heading1.runs
                     if r._element.rPr is not None
                     and r._element.rPr.find(W + "color") is not None), None)
    check("B 标题颜色往返 第1章=365F91", h1_color == "365F91", str(h1_color))
    heading2 = next(p for p in rd.paragraphs if p.text.strip() == "研究背景")
    h2_color = next((r._element.rPr.find(W + "color").get(W + "val")
                     for r in heading2.runs
                     if r._element.rPr is not None
                     and r._element.rPr.find(W + "color") is not None), None)
    check("B 标题颜色往返 研究背景=4F81BD", h2_color == "4F81BD", str(h2_color))

    # B7 图片关系
    img_blips = rd.element.body.iter(_qn("a:blip"))
    img_rids = [b.get(_qn("r:embed")) for b in img_blips if b.get(_qn("r:embed"))]
    check("B 图片关系可解析", len(img_rids) >= 1 and all(r in rd.part.related_parts for r in img_rids),
          str(img_rids))

    # B8 页面设置（S2 横向）
    s2 = rd.sections[1]
    check("B S2 横向", s2.orientation == 1, str(s2.orientation))
    check("B S2 宽>高", s2.page_width > s2.page_height,
          f"{s2.page_width:.1f}x{s2.page_height:.1f}")

    # ================= C 组：往返一致性 =================
    shutil.rmtree(REP, ignore_errors=True)
    re_doc = json.load(open(docx_to_json(REBUILT, REP), encoding="utf-8"))

    # C1 文本序列对比（忽略超链接文字等已知缺口，按原 JSON 的文本逐条查）
    orig_texts = [b.get("text") for b in doc["blocks"]
                  if b.get("type") == "paragraph" and b.get("text")]
    re_texts = [b.get("text") for b in re_doc["blocks"]
                if b.get("type") == "paragraph" and b.get("text")]
    lost = [t for t in orig_texts if t not in re_texts and "参考文献链接" not in t]
    check("C 文本往返无丢失", not lost, str(lost))
    check("C 往返文本条数", len(orig_texts) == len(re_texts),
          f"{len(orig_texts)} vs {len(re_texts)}")

    # C2 kind 序列对比
    orig_kinds = [(b.get("type"), b.get("kind"), b.get("text", "")[:10]) for b in doc["blocks"]]
    re_kinds = [(b.get("type"), b.get("kind"), b.get("text", "")[:10]) for b in re_doc["blocks"]]
    kind_mismatch = [(a, b) for a, b in zip(orig_kinds, re_kinds) if a != b]
    check("C kind 序列一致", not kind_mismatch, str(kind_mismatch[:5]))

    # C3 表格单元格对比
    o_tbl = next(b for b in doc["blocks"] if b.get("type") == "table")
    r_tbl = next(b for b in re_doc["blocks"] if b.get("type") == "table")
    o_cells = [(c.get("text"), c.get("grid_span"), c.get("vmerge")) for c in o_tbl["cells"]]
    r_cells = [(c.get("text"), c.get("grid_span"), c.get("vmerge")) for c in r_tbl["cells"]]
    check("C 表格单元格/合并一致", o_cells == r_cells,
          f"{o_cells} vs {r_cells}")
    check("C 表格 n_rows/n_cols", (o_tbl.get("n_rows"), o_tbl.get("n_cols")) ==
          (r_tbl.get("n_rows"), r_tbl.get("n_cols")),
          str((o_tbl.get("n_rows"), o_tbl.get("n_cols"))))

    # C4 公式块对比
    o_f = next(b for b in doc["blocks"] if b.get("formula_type") == "ole")
    r_f = next(b for b in re_doc["blocks"] if b.get("formula_type") == "ole")
    check("C 公式块保留", o_f.get("formula_type") == r_f.get("formula_type") and
          bool(r_f.get("formula_xml")) and
          o_f.get("formula_preview") == r_f.get("formula_preview"),
          str(r_f))
    check("C 公式资产重解析可读", len(r_f.get("formula_assets") or []) >= 2,
          json.dumps(r_f.get("formula_assets"), ensure_ascii=False)[:200])

    # C5 页眉页脚对比
    o_h = {h.get("type"): h.get("text") for h in doc["sections"][0].get("headers", [])}
    r_h = {h.get("type"): h.get("text") for h in re_doc["sections"][0].get("headers", [])}
    check("C 页眉往返一致", o_h == r_h, f"{o_h} vs {r_h}")
    o_s = [(s.get("paper_size"), s.get("orientation")) for s in doc["sections"]]
    r_s = [(s.get("paper_size"), s.get("orientation")) for s in re_doc["sections"]]
    check("C sections 往返一致", o_s == r_s, f"{o_s} vs {r_s}")

    # C6 域往返
    o_toc = next(b for b in doc["blocks"] if b.get("text", "").startswith("1.1 研究背景"))
    r_toc = next(b for b in re_doc["blocks"] if b.get("text", "").startswith("1.1 研究背景"))
    check("C TOC 域往返", "TOC" in (r_toc.get("fields") or []) and
          any(r.get("field", {}).get("type") == "TOC" for r in r_toc.get("runs", [])),
          str(r_toc.get("fields")))

    # ---- 汇总 ----
    print("\n==== B 组：复现链路（JSON -> DOCX）====")
    print("==== C 组：往返一致性 ====")
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
