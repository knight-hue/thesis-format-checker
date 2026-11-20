# -*- coding: utf-8 -*-
"""新 JSON 规范 → docx。"""
import json
import os
import sys

from docx import Document
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

from backend.docx_common.assets import find_image_file, images_dir
from .op_section import apply_sections, DEFAULT_SECTION, PAPER_SIZES_CM
from .op_passage import add_paragraph
from .op_picture import add_image
from .op_table import add_table


def _resolve_formula_asset_paths(block, base_dir, images_root):
    """W3/P9：把 formula_assets 里的相对路径解析成绝对路径。

    解析层（P9 修复后）统一写成 "images/<文件名>"，即以 JSON 所在目录为基准，
    与 block.path 的约定一致；旧数据可能只写文件名（相对 images/），
    这里按“先 base_dir 后 images/”的顺序探测，两个都不存在时按 base_dir 归一化，
    由调用方（_import_formula_part）检查文件是否存在并安全跳过。
    """
    for asset in block.get("formula_assets") or []:
        p = asset.get("path")
        if not p or os.path.isabs(p):
            continue
        candidates = [
            os.path.normpath(os.path.join(base_dir, p)),
            os.path.normpath(os.path.join(images_root, p)),
        ]
        for candidate in candidates:
            if os.path.isfile(candidate):
                asset["path"] = candidate
                break
        else:
            asset["path"] = candidates[0]


def _usable_body_width_inch(doc_cfg, section_index=0):
    """正文可用宽度（英寸）= 页宽 − 左右页边距，取自 JSON 的 sections 配置。

    W4：表格列宽需要按真实可用宽度铺满，而不是硬编码 6.0 英寸。
    注意此时页面设置还没应用（apply_sections 在 blocks 渲染之后），
    所以只能从 sections 配置里算，不能读 doc.sections。
    """
    sections = doc_cfg.get("sections") or []
    config = sections[section_index] if len(sections) > section_index else {}
    config = config or {}
    paper = config.get("paper_size") or DEFAULT_SECTION["paper_size"]
    width_cm, height_cm = PAPER_SIZES_CM.get(paper, PAPER_SIZES_CM["A4"])
    if str(config.get("orientation") or DEFAULT_SECTION["orientation"]).lower() == "landscape":
        width_cm, height_cm = height_cm, width_cm
    left = config.get("left_cm")
    right = config.get("right_cm")
    left = DEFAULT_SECTION["left_cm"] if left is None else left
    right = DEFAULT_SECTION["right_cm"] if right is None else right
    usable_cm = width_cm - left - right
    if usable_cm <= 0:
        return None
    return usable_cm / 2.54


def _enable_update_fields(doc, doc_cfg):
    """W6：文档含域（TOC/PAGE/REF/SEQ...）时开启 w:updateFields，
    让 Word 打开时提示/自动更新域结果。"""
    has_field = False
    for block in doc_cfg.get("blocks", []):
        if block.get("type") != "paragraph":
            continue
        if block.get("fields"):
            has_field = True
            break
        for run in block.get("runs", []) or []:
            if run.get("field"):
                has_field = True
                break
        if has_field:
            break
    if not has_field:
        return
    settings = doc.settings.element
    if settings.find(qn("w:updateFields")) is not None:
        return
    update = OxmlElement("w:updateFields")
    update.set(qn("w:val"), "true")
    settings.insert_element_before(update, "w:compat", "w:rsids", "w:mathPr")


def json_to_docx(json_path: str, output_docx: str = "复现.docx") -> str:
    with open(json_path, "r", encoding="utf-8") as f:
        doc_cfg = json.load(f)

    base_dir     = os.path.dirname(os.path.abspath(json_path))
    images_root  = images_dir(base_dir)
    # W4：表格总宽度按 sections 配置算出的正文可用宽度铺满
    body_width_inch = _usable_body_width_inch(doc_cfg)

    doc = Document()

    # 旧实现（往返错位根因）：先 apply_sections（内部 add_section 插入分节符
    # 空段）再渲染 blocks，正文全部落在分节符之后，S1 内容被归入 S2 节；
    # 且 Document() 默认空段落问题。新实现先按 blocks 顺序渲染（遇
    # section_break 块重建分节符），渲染完成后再应用页面设置。

    # 1) 按 blocks 顺序渲染
    for block in doc_cfg.get("blocks", []):
        t = block.get("type")

        if t == "paragraph":
            if block.get("section_break"):
                # 分节符空段：重建分节符（后续内容进入新节），不渲染空段本身
                doc.add_section()
                continue
            # W3/P9：公式引用部件路径解析为绝对路径后再渲染
            _resolve_formula_asset_paths(block, base_dir, images_root)
            add_paragraph(doc, block)

        elif t == "image":
            # 优先用 JSON 里的 path；没有就按 index 反查 images/ 目录
            p = block.get("path", "") or ""
            if not p:
                p = find_image_file(images_root, block.get("index", 0))

            if not p:
                # ★ 找不到就报警，不要静默跳过
                print(f"[WARN] 图片未找到: block index={block.get('index')}, "
                      f"images_dir={images_root}")
                # 仍留一个空段落，避免 block 顺序错乱
                add_image(doc, {"type": "image"})
                continue

            if not os.path.isabs(p):
                p = os.path.normpath(os.path.join(base_dir, p))

            # 浅拷贝一份，避免改到原 block
            new_block = dict(block)
            new_block["path"] = p
            add_image(doc, new_block)

        elif t == "table":
            add_table(doc, block, body_width_inch=body_width_inch)

        else:
            raise ValueError(f"未知的 block type：{t}")

    # 2) 页面设置（渲染后 doc.sections 数量与分节符一致）
    apply_sections(doc, doc_cfg.get("sections", []))

    # W6：含域代码时开启 updateFields，让 Word 打开时提示更新目录/页码等
    # 修复③：用户反馈打开复现稿弹安全声明（updateFields=true 触发 Word 安全提示），
    # 暂时关掉——目录/页码需用户在 Word 里手动右键"更新域"，不再弹窗打扰。
    # _enable_update_fields(doc, doc_cfg)
    doc.save(output_docx)
    return output_docx


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit("用法：python -m backend.write_back.json_to_docx 输入.json 输出.docx")
    in_path, out_path = sys.argv[1], sys.argv[2]

    print(f"输入 JSON : {in_path}")
    print(f"输出 DOCX : {out_path}")
    out = json_to_docx(in_path, out_path)
    print(f"已生成: {out}")