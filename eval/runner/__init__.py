# -*- coding: utf-8 -*-
"""评测执行器公共逻辑：解析、金标准定位解析。"""
import json
from typing import Optional

from backend.document_contract import DocumentContract
from backend.parser import docx_to_json
from backend.recognition.structure import load_structure


def parse_docx(docx_path: str, college_id: str, output_dir: str) -> dict:
    """解析 DOCX → 契约 document（含契约校验）。"""
    json_path = docx_to_json(
        docx_path, output_dir=output_dir, structure=load_structure(college_id)
    )
    with open(json_path, "r", encoding="utf-8") as stream:
        document = json.load(stream)
    return DocumentContract.model_validate(document).model_dump(exclude_unset=True)


def resolve_block_index(document: dict, text_find: str, kind: Optional[str] = None) -> Optional[int]:
    """在解析后的 document 里按文本片段找块 index（可限定 kind）。"""
    for block in document.get("blocks", []):
        text = block.get("text") or ""
        if text_find not in text:
            continue
        if kind is not None and block.get("kind") != kind:
            continue
        return block.get("index")
    # 表格单元格里找（table_cell 类定位）
    for block in document.get("blocks", []):
        if block.get("type") != "table":
            continue
        for cell in block.get("cells", []):
            if text_find in (cell.get("text") or ""):
                return block.get("index")
    return None


def resolve_case_locations(document: dict, golden_list: list) -> dict:
    """把金标准条目的 text_find 解析成 index，返回 {golden_index: resolved}。

    unresolved 的金标准条目（找不到注入点）标记为定位失败——说明结构识别
    没把该段认成注入器期望的 kind，检测层的对应检查没有触发。
    """
    resolved = {}
    for pos, golden in enumerate(golden_list):
        tf = golden.get("text_find")
        if not tf:
            resolved[pos] = True
            continue
        idx = resolve_block_index(document, tf, golden.get("kind"))
        if idx is None:
            resolved[pos] = False
            continue
        loc = golden.get("location") or {}
        loc["index"] = idx
        if loc.get("type") in (None, "paragraph") and golden.get("kind"):
            # 表格单元格定位时 type 由注入器给出（"table"），段落类统一为 block type
            block = next((b for b in document.get("blocks", [])
                          if b.get("index") == idx), {})
            loc["type"] = block.get("type", "paragraph")
        golden["location"] = loc
        resolved[pos] = True
    return resolved
