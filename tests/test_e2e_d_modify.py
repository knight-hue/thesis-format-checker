# -*- coding: utf-8 -*-
"""整体测试——D 组：修改 JSON 后复现（用户实际使用路径）。

模拟用户对 document.json 的典型修改（字号/文本/页边距/单元格/页眉），
json_to_docx 复现后验证：修改生效、未改部分完整保持。
"""
import copy
import json
import os
import shutil
import sys

OUT = os.path.join(os.environ.get("TEMP", "/tmp"), "docx_parser_verify")
SRC_JSON = os.path.join(OUT, "e2e_parse", "document.json")
MOD_JSON = os.path.join(OUT, "e2e_modified", "document.json")
MOD_OUT = os.path.join(OUT, "e2e_modified", "rebuilt.docx")

checks = []


def check(name, cond, detail=""):
    checks.append((name, bool(cond), detail))


def main():
    from backend.write_back.json_to_docx import json_to_docx

    doc = json.load(open(SRC_JSON, encoding="utf-8"))
    modified = copy.deepcopy(doc)

    # ---- 模拟用户修改 ----
    edits = []
    # 1) 标题字号："摘 要" 16pt -> 18pt
    for b in modified["blocks"]:
        if b.get("type") == "paragraph" and b.get("text") == "摘 要":
            for r in b.get("runs", []):
                r["font_size_pt"] = 18.0
            edits.append("标题字号 16->18")
    # 2) 正文文本："近年来" 段改文本
    for b in modified["blocks"]:
        if b.get("type") == "paragraph" and b.get("text", "").startswith("近年来"):
            b["text"] = "近年来，深度学习与自然语言处理技术快速发展。"
            for r in b.get("runs", []):
                if r.get("text") == "近年来，":
                    r["text"] = "近年来，深度学习与自然语言处理技术快速发展。"
            edits.append("正文文本替换")
    # 3) 页边距：S1 left_cm 3.17 -> 2.5
    modified["sections"][0]["left_cm"] = 2.5
    edits.append("左页边距 3.17->2.5")
    # 4) 单元格文本："纵合" -> "纵向合并"
    for b in modified["blocks"]:
        if b.get("type") == "table":
            for c in b["cells"]:
                if c.get("text") == "纵合":
                    c["text"] = "纵向合并"
                    for r in c.get("runs", []):
                        if r.get("text") == "纵合":
                            r["text"] = "纵向合并"
            edits.append("单元格文本 纵合->纵向合并")
    # 5) 页眉文本：S1 odd 页眉追加"（修订）"
    for s in modified["sections"]:
        for h in s.get("headers", []):
            if h.get("type") == "default" and "吉林大学" in h.get("text", ""):
                h["text"] = h["text"].replace("吉林大学硕士学位论文", "吉林大学硕士学位论文（修订）")
                for pg in h.get("paragraphs", []):
                    for r in pg.get("runs", []):
                        if r.get("text") == "吉林大学硕士学位论文":
                            r["text"] = "吉林大学硕士学位论文（修订）"
                edits.append("页眉追加（修订）")
    # 6) 参考文献字号 10.5 -> 9
    for b in modified["blocks"]:
        if b.get("type") == "paragraph" and b.get("text", "").startswith("[1] "):
            for r in b.get("runs", []):
                r["font_size_pt"] = 9.0
            edits.append("参考文献字号 10.5->9")

    os.makedirs(os.path.dirname(MOD_JSON), exist_ok=True)
    # 复制 images/ 目录（用户实际使用时 images 与 document.json 一起携带）
    src_images = os.path.join(OUT, "e2e_parse", "images")
    dst_images = os.path.join(OUT, "e2e_modified", "images")
    if os.path.isdir(src_images):
        shutil.rmtree(dst_images, ignore_errors=True)
        shutil.copytree(src_images, dst_images)
    with open(MOD_JSON, "w", encoding="utf-8") as f:
        json.dump(modified, f, ensure_ascii=False, indent=2)
    json_to_docx(MOD_JSON, MOD_OUT)

    from docx import Document
    from docx.oxml.ns import qn as _qn
    rd = Document(MOD_OUT)

    # ---- 修改生效 ----
    # 1) 摘 要 字号 18pt
    zh_yao = next(p for p in rd.paragraphs if p.text == "摘 要")
    sizes = [r.font.size.pt for r in zh_yao.runs if r.font.size is not None]
    check("D1 标题字号修改生效 18pt", sizes and all(abs(s - 18) < 0.01 for s in sizes),
          str(sizes))

    # 2) 正文文本替换生效
    all_text = "\n".join(p.text for p in rd.paragraphs)
    check("D2 正文文本修改生效", "近年来，深度学习与自然语言处理技术快速发展。" in all_text,
          all_text[:150])
    check("D2 旧正文文本已替换", "近年来，deep learning方法在自然语言处" not in all_text,
          all_text[:150])

    # 3) 页边距生效
    from docx.shared import Cm
    check("D3 左页边距 2.5cm", abs(rd.sections[0].left_margin.cm - 2.5) < 0.05,
          str(rd.sections[0].left_margin.cm))

    # 4) 单元格文本生效（合并表）
    tbl = rd.tables[0]
    cell_texts = set()
    for t in rd.tables:
        for row in t.rows:
            for c in row.cells:
                cell_texts.add(c.text)
    check("D4 单元格文本修改生效", "纵向合并" in cell_texts, str(cell_texts))
    check("D4 旧单元格文本已替换", "纵合" not in cell_texts, str(cell_texts))

    # 5) 页眉生效
    check("D5 页眉修改生效", "（修订）" in rd.sections[0].header.paragraphs[0].text,
          rd.sections[0].header.paragraphs[0].text)

    # 6) 参考文献字号 9pt
    ref_para = next(p for p in rd.paragraphs if p.text.startswith("[1] "))
    ref_sizes = [r.font.size.pt for r in ref_para.runs if r.font.size is not None]
    check("D6 参考文献字号 9pt", ref_sizes and all(abs(s - 9) < 0.01 for s in ref_sizes),
          str(ref_sizes))

    # ---- 未改部分保持 ----
    for snippet in ("吉林大学硕士学位论文", "基于深度学习的文本格式校验研究",
                    "关键词：格式校验", "第1章 绪论", "1.1研究背景", "1.2.1 深度学习方法",
                    "表 2-1 实验环境配置", "合并单元格标题", "数量", "12", "样式字号",
                    "(2-1)", "[2] Smith J.", "致 谢", "图 2-1 系统架构图2",
                    "附录 A 数据说明", "98.2%", "参考文献链接"):
        check(f"D7 未改内容保持：{snippet[:12]}", snippet in all_text or snippet in str(cell_texts),
              all_text[:100] + str(cell_texts))

    # 结构保持：合并/公式/域/分节
    tc00 = tbl.rows[0]._tr.findall(_qn("w:tc"))[0]
    gs = tc00.find(_qn("w:tcPr") + "/" + _qn("w:gridSpan"))
    check("D8 合并结构保持", gs is not None and gs.get(_qn("w:val")) == "2",
          str(gs.attrib if gs is not None else None))
    pxml = rd.element.body.xml
    check("D8 公式对象保持", "OLEObject" in pxml and "imagedata" in pxml, "")
    check("D8 TOC 域保持", 'TOC \\o' in pxml, "")
    check("D8 updateFields 已关闭（不弹安全声明）", rd.settings.element.find(_qn("w:updateFields")) is None, "")
    check("D8 S2 横向保持", rd.sections[1].orientation == 1 and
          rd.sections[1].page_width > rd.sections[1].page_height,
          f"{rd.sections[1].page_width:.0f}x{rd.sections[1].page_height:.0f}")

    # 公式 blob 仍可解析
    found_obj = None
    for p in rd.paragraphs:
        for obj in p._p.iter(_qn("w:object")):
            found_obj = obj
            break
        if found_obj is not None:
            break
    VML = "{urn:schemas-microsoft-com:vml}"
    img_node = found_obj.find(".//" + VML + "imagedata") if found_obj is not None else None
    img_rid = img_node.get(_qn("r:id")) if img_node is not None else None
    check("D8 公式 r:id 可解析", img_rid is not None and img_rid in rd.part.related_parts, str(img_rid))

    # ---- 汇总 ----
    print("\n==== D 组：修改 JSON 后复现 ====")
    print("模拟修改:", "; ".join(edits))
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
