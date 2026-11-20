# -*- coding: utf-8 -*-
"""Word 域代码识别（P7）。

Word 域在 OOXML 里有两种形态：
1. 复杂域：<w:fldChar w:fldCharType="begin"/> + <w:instrText> 指令 +
   <w:fldChar w:fldCharType="separate"/> + 结果 run + <w:fldChar w:fldCharType="end"/>
2. 简单域：<w:fldSimple w:instr="...">，子 run 直接是结果

旧解析只读 run 文本与字体，域只剩“域结果”被当普通正文：
- TOC 域生成的目录条目和普通文字无法区分；
- PAGE 页码、REF 交叉引用、SEQ 图/表/公式编号全部丢失类型；
- 后续检查无法验证编号连续性与更新状态。

本模块把段落 XML 里的域边界解析出来，给“域结果 run”打上
{"type": "PAGE", "code": " PAGE "} 标记，供解析层写入 run 与区块级 fields。
"""
import re

from docx.oxml.ns import qn

# 域指令首词即域类型，如 ' PAGE \\* MERGEFORMAT ' -> 'PAGE'
_FIELD_TOKEN_RE = re.compile(r"^[A-Za-z]+")


def normalize_field_type(code):
    """从域指令取首词作为域类型，规范化大写；无指令返回 None。"""
    if not code:
        return None
    match = _FIELD_TOKEN_RE.match(code.strip())
    return match.group(0).upper() if match else None


def field_run_map(paragraph_element):
    """扫描段落 XML，返回 {run_element: {"type": str, "code": str}}。

    只标记处于“域结果”区段（separate 之后、end 之前）的 w:r；
    fldChar 本身与指令 run 不打标。w:fldSimple 的子 run 全部视为结果。
    注意：跨段落的域（如 TOC 域跨多个条目段落）只在本段落内追踪，
    起始段的结果 run 会被打标，其余段仍靠正文启发式。
    """
    result = {}
    stack = []  # 复杂域栈：{"type", "code", "in_result"}
    for child in paragraph_element.iter():
        tag = child.tag
        if tag == qn("w:fldChar"):
            ftype = child.get(qn("w:fldCharType"))
            if ftype == "begin":
                stack.append({"type": None, "code": "", "in_result": False})
            elif ftype == "separate" and stack:
                stack[-1]["in_result"] = True
            elif ftype == "end" and stack:
                stack.pop()
        elif tag == qn("w:instrText"):
            if stack:
                stack[-1]["code"] += child.text or ""
                stack[-1]["type"] = normalize_field_type(stack[-1]["code"])
        elif tag == qn("w:fldSimple"):
            code = child.get(qn("w:instr")) or ""
            ftype = normalize_field_type(code)
            for run_el in child.iter(qn("w:r")):
                result[run_el] = {"type": ftype, "code": code}
        elif tag == qn("w:r"):
            if stack and stack[-1]["in_result"]:
                info = {"type": stack[-1]["type"], "code": stack[-1]["code"]}
                if info["type"]:
                    result[child] = info
    return result


def paragraph_field_types(paragraph_element, field_map=None):
    """段落内出现的域类型（按出现顺序去重），供区块级 fields 打标。

    除了“域结果所在的段落”，也把只在段内出现域指令（复杂域的 w:instrText、简单域
    的 w:fldSimple/@w:instr）的域类型算进来：跨段落的域（TOC 最典型）指令落在首段、
    结果散布在后续段落，只看结果 run 会把“这一段里有没有 TOC 域”判成没有。
    """
    types = []
    for type_name in _field_types(paragraph_element, field_map):
        if type_name and type_name not in types:
            types.append(type_name)
    return types


def _field_types(paragraph_element, field_map=None):
    """段落里的域类型：域结果的类型 + 域指令的类型。"""
    for info in (field_run_map(paragraph_element) if field_map is None
                 else field_map).values():
        yield info.get("type")
    codes = _instruction_codes(paragraph_element)
    for code in codes:
        yield normalize_field_type(code)
    # 指令被拆到多个 w:instrText 时（Word 会按格式切换拆 run），拼接后再认一次。
    if len(codes) > 1:
        yield normalize_field_type("".join(codes))


def _instruction_codes(paragraph_element):
    """段落里出现的域指令原文（复杂域的 w:instrText、简单域的 w:fldSimple 的 instr）。"""
    codes = []
    for child in paragraph_element.iter():
        if child.tag == qn("w:instrText"):
            codes.append(child.text or "")
        elif child.tag == qn("w:fldSimple"):
            codes.append(child.get(qn("w:instr")) or "")
    return [code for code in codes if code.strip()]
