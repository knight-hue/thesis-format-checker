# -*- coding: utf-8 -*-
"""自动编号格式修复：改 numbering.xml 的 numFmt，保留 Word 自动编号。

判定层能从 numbering.xml 还原出“第一章”，但段落文本里没有编号；如果按普通文字
修复把“第1章”写进段落，Word 会渲染成“第1章 第一章 绪论”。正确做法是把该级编号的
numFmt 从 chineseCounting 改为 decimal，`第%1章` 就会显示为“第1章”。

只替换 ZIP 包里的 word/numbering.xml，其余部件逐字节保留（与 patch_docx 的承诺一致）。
"""
from __future__ import annotations

import os
import zipfile

from docx.oxml.ns import qn
from lxml import etree

_NUMBERING_PART = "word/numbering.xml"


def _targets_from_issues(blocks, issues):
    """{(numId, ilvl): (rule_id, block_index)}，只收自动编号的编号格式问题。"""
    targets = {}
    for issue in issues or []:
        if not isinstance(issue, dict) or not issue.get("auto_number"):
            continue
        rule_id = str(issue.get("rule_id") or "")
        if not rule_id.endswith(".number"):
            continue
        # 聚合问题（同规则多处）会带 locations：每一处都可能用不同的 numId/ilvl。
        locations = issue.get("locations") or [issue.get("location")]
        for location in locations:
            if not isinstance(location, dict):
                continue
            index = location.get("index")
            if not isinstance(index, int) or not 0 <= index < len(blocks):
                continue
            ref = (blocks[index] or {}).get("auto_number_ref")
            if not ref or len(ref) != 2:
                continue
            key = (int(ref[0]), int(ref[1]))
            targets.setdefault(key, (rule_id, index))
    return targets


def _patch_numbering_xml(data: bytes, targets):
    """把命中目标的 lvl/numFmt 改成 decimal；返回 (新字节, 命中的 key 列表)。"""
    root = etree.fromstring(data)
    applied = []
    for (num_id, ilvl), _info in targets.items():
        num = next(
            (item for item in root.findall(qn("w:num"))
             if item.get(qn("w:numId")) == str(num_id)),
            None,
        )
        if num is None:
            continue
        abstract_id = num.find(qn("w:abstractNumId"))
        if abstract_id is None:
            continue
        abstract = next(
            (item for item in root.findall(qn("w:abstractNum"))
             if item.get(qn("w:abstractNumId")) == abstract_id.get(qn("w:val"))),
            None,
        )
        if abstract is None:
            continue
        lvl = next(
            (item for item in abstract.findall(qn("w:lvl"))
             if (item.get(qn("w:ilvl")) or "0") == str(ilvl)),
            None,
        )
        if lvl is None:
            continue
        num_fmt = lvl.find(qn("w:numFmt"))
        if num_fmt is None:
            continue
        if num_fmt.get(qn("w:val")) != "decimal":
            num_fmt.set(qn("w:val"), "decimal")
            applied.append((num_id, ilvl))
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8",
                          standalone=True), applied


def patch_numbering_formats(docx_path: str, blocks, issues):
    """就地修复 docx 的自动编号格式；返回 applied / skipped 明细。"""
    targets = _targets_from_issues(blocks, issues)
    applied = []
    skipped = []
    if not targets:
        return {"applied": applied, "skipped": skipped}
    try:
        with zipfile.ZipFile(docx_path) as source:
            items = source.infolist()
            if not any(item.filename == _NUMBERING_PART for item in items):
                return {"applied": applied, "skipped": [
                    {"rule_id": info[0],
                     "reason": "文档没有 numbering.xml，无法修改自动编号格式"}
                    for info in targets.values()
                ]}
            payloads = {item.filename: source.read(item.filename) for item in items}
    except (OSError, zipfile.BadZipFile):
        return {"applied": applied, "skipped": [
            {"rule_id": info[0], "reason": "DOCX 包无法读取"} for info in targets.values()
        ]}

    new_xml, changed_keys = _patch_numbering_xml(
        payloads[_NUMBERING_PART], targets)
    changed = set(changed_keys)
    for key, (rule_id, index) in targets.items():
        if key in changed:
            applied.append({"rule_id": rule_id, "index": index,
                            "numbering": {"num_id": key[0], "ilvl": key[1]}})
        else:
            skipped.append({
                "rule_id": rule_id,
                "reason": f"该段落使用的自动编号（numId={key[0]}, ilvl={key[1]}）"
                          "找不到可修改的编号级别",
                "index": index,
            })
    if not changed:
        return {"applied": applied, "skipped": skipped}

    payloads[_NUMBERING_PART] = new_xml
    temp_path = docx_path + ".numbering.tmp"
    try:
        with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as target:
            for item in items:
                target.writestr(item, payloads[item.filename])
        os.replace(temp_path, docx_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    return {"applied": applied, "skipped": skipped}
