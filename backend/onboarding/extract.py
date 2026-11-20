# -*- coding: utf-8 -*-
"""规范文档（docx）→ 结构化条目 → 上架材料草案。

产物（写入 --out 目录）：

- ``spec_items.json``：条目台账（章 / 节 / 原文 / 段落号 + 警告），是后续
  "判据草案生成"（WP2，走 AI）的输入；
- ``corpus.txt``：规范文本草稿（``【章 / 节】`` + 条目行），人审后进入 config。

v1 边界（如实声明）：

- 只吃 ``.docx``（``.doc`` 先用 Word 另存为 docx）；只读段落，表格 / 文本框
  等载体等真实材料到位后按需补；
- 标题启发 = 样式名（``Heading N`` / ``标题 N``）或文本形如 ``第X章 …`` /
  ``N.N …``；三级及以下标题按**条目**保留（原文不丢，人审时再归类）；
- 本模块不判断内容对错——它是抽取器，判据与人审在流水线后面。
"""
from __future__ import annotations

import json
import os
import re
from typing import List, Optional

_CHAPTER_RE = re.compile(r"^第\s*[一二三四五六七八九十百千0-9]+\s*章")
_SECTION3_RE = re.compile(r"^\d+\.\d+\.\d+")
_SECTION2_RE = re.compile(r"^\d+\.\d+(?!\.?\d)")
_STYLE_LEVEL_RE = re.compile(r"^(?:Heading|标题)\s*(\d+)", re.IGNORECASE)


def _style_level(paragraph) -> Optional[int]:
    try:
        name = (paragraph.style.name or "").strip()
    except Exception:  # noqa: BLE001 - 样式读取失败按"没有样式"处理
        return None
    match = _STYLE_LEVEL_RE.match(name)
    return int(match.group(1)) if match else None


def _text_level(text: str) -> Optional[int]:
    if _CHAPTER_RE.match(text):
        return 1
    if _SECTION3_RE.match(text):
        return 3
    if _SECTION2_RE.match(text):
        return 2
    return None


def _classify(paragraph) -> tuple:
    """返回 (level, text)：level ∈ {1, 2, 3, None}；None=条目。"""
    text = (paragraph.text or "").strip()
    if not text:
        return None, ""
    level = _style_level(paragraph)
    if level is None:
        level = _text_level(text)
    return level, text


def extract_docx(path: str) -> dict:
    """把规范文档抽成"章 / 节 / 条目"结构；纯读取，不改文档。"""
    from docx import Document

    document = Document(path)
    sections: List[dict] = []
    warnings: List[str] = []
    chapter = ""
    current: Optional[dict] = None

    def ensure_group() -> dict:
        nonlocal current
        if current is None:
            current = {"chapter": chapter, "section": "", "items": []}
            sections.append(current)
        return current

    for para_no, paragraph in enumerate(document.paragraphs, start=1):
        level, text = _classify(paragraph)
        if not text:
            continue
        if level == 1:
            chapter = text
            current = None
            continue
        if level == 2:
            current = None
            ensure_group()["section"] = text
            continue
        # 三级标题按条目保留（原文不丢）；普通段落是条目
        ensure_group()["items"].append({"text": text, "para_no": para_no})

    incomplete = sum(1 for group in sections
                     if not (group["chapter"] and group["section"]))
    if incomplete:
        warnings.append(f"有 {incomplete} 组条目章/节不完整"
                        f"（草稿里是单栏标题，人审时补齐）")
    empty_sections = [g for g in sections if not g["items"]]
    if empty_sections:
        warnings.append(f"有 {len(empty_sections)} 个章/节标题下没有抽到条目")
    return {"source": os.path.abspath(path), "warnings": warnings,
            "sections": sections}


def write_outputs(result: dict, out_dir: str) -> dict:
    """写出 spec_items.json 与 corpus.txt 草案，返回 {文件名: 路径}。"""
    from backend.onboarding.corpus import build_corpus_text

    os.makedirs(out_dir, exist_ok=True)
    items = [{"chapter": group["chapter"], "section": group["section"],
              "text": item["text"], "para_no": item["para_no"]}
             for group in result["sections"] for item in group["items"]]
    spec = {"source": result["source"], "warnings": result["warnings"],
            "items_total": len(items), "items": items}
    spec_path = os.path.join(out_dir, "spec_items.json")
    with open(spec_path, "w", encoding="utf-8") as handle:
        json.dump(spec, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    corpus_path = os.path.join(out_dir, "corpus.txt")
    with open(corpus_path, "w", encoding="utf-8") as handle:
        handle.write(build_corpus_text(result["sections"]))
    return {"spec_items": spec_path, "corpus": corpus_path}
