# -*- coding: utf-8 -*-
"""文字改写服务：生成用户确认的文字内容类修复。

覆盖两类：
1. 语义检查 issue（source="rag"）：逐段调用 RAGService.rewrite_passage，
   LLM 只改违反规范的那几处、其余原文保留，返回整段改写文本后写回。
2. 规则引擎文字类 issue（source="rule_engine"）：白名单内的确定性正则修复，
   如参考文献句点与序号、图表编号空格、注释前缀、正文半角标点、关键词末尾标点、
   一级标题编号。

结构化与只能人工补的语义规则（图表位置、目录完整性、参考文献著录项）不属于
文本替换，统一跳过并给原因。

本模块只处理文本和模型，不打开或修改 DOCX。生成的 changes 交给
fix.fix_blocks 的文本层应用，再由 write_back.json_to_docx 重建落盘。
"""
import re
from backend.detection.rules.base import TERMINAL_PUNCT
from backend.detection.semantic_checker import (
    MANUAL_ONLY_SEMANTIC_RULES,
    REWRITABLE_SEMANTIC_RULES,
    prefilter_semantic_rule,
)


# ====================== 规则引擎文字类的确定性修复 ======================
def _fix_reference_period(text):
    """参考文献条目以全角“．”结尾：缺就补，写成半角“.”就换成全角。"""
    if not text:
        return None
    stripped = text.rstrip()
    if not stripped:
        return None
    tail = text[len(stripped):]
    if stripped.endswith("．"):
        return None
    if stripped.endswith("."):
        return stripped[:-1] + "．" + tail
    return stripped + "．" + tail


def _fix_caption_number_space(text):
    """图表编号与题名之间补一个汉字宽：用全角空格补齐（一个半角空格不够宽）。"""
    m = re.match(r"^([图表]\s*\d+\s*[-－]\s*\d+)", text)
    if not m:
        return None
    rest = text[m.end():]
    gap = re.match(r"[ \u3000]*", rest).group(0)
    half, full = gap.count(" "), gap.count("\u3000")
    if full + 0.5 * half >= 1:
        return None
    tail = rest[len(gap):]
    if not tail:
        return None
    return m.group(1) + "\u3000" + tail


def _fix_note_prefix(text):
    """注释前补【注】前缀。"""
    if text and not text.startswith(("【注】", "【注意】")):
        return "【注】" + text
    return None


# §3.4 列表序号：总项用“1．”，分项用“（1）”；与 config/rules/<学校>/<学院>/*_rules.json 的
# list_item.required_total_marker / forbid_punct_after_marker 保持一致。
_TOTAL_MARKER = "．"
_LIST_ITEM_PUNCT = "、，。；：,.?;:!"
_SUB_MARKER_RE = re.compile(r"^(\s*)(?:[（(]\d{1,2}[）)]|\d{1,2}[）)])")
_TOTAL_MARKER_RE = re.compile(r"^(\s*)(\d{1,2})\s*([．.、，,])(?!\d)")


def _fix_list_item_marker_punct(text):
    """§3.4：去掉分项序号括注后多余的标点（“（1）、数据” -> “（1）数据”）。"""
    m = _SUB_MARKER_RE.match(text)
    if not m:
        return None
    rest = text[m.end():]
    offset = len(rest) - len(rest.lstrip())
    if offset >= len(rest) or rest[offset] not in _LIST_ITEM_PUNCT:
        return None
    return text[:m.end()] + rest[:offset] + rest[offset + 1:]


def _fix_list_item_number_format(text):
    """§3.4：把总项序号后的分隔符规范为“．”（“1、内容” -> “1．内容”）。"""
    m = _TOTAL_MARKER_RE.match(text)
    if not m or m.group(3) == _TOTAL_MARKER:
        return None
    return m.group(1) + m.group(2) + _TOTAL_MARKER + text[m.end():].lstrip()


_CJK = "\u4e00-\u9fff"
# 紧邻中文的半角标点。用零宽断言定位，只替换标点本身、不吃掉中文，
# 这样“……中，”这类首尾紧邻的标点也能一次全改掉。
_HALFWIDTH_PUNCT_RE = re.compile(rf"(?<=[{_CJK}])[,;:!?]|[,;:!?](?=[{_CJK}])")
_HALFWIDTH_PUNCT_MAP = {",": "，", ";": "；", ":": "：", "!": "！", "?": "？"}


def _fix_body_fullwidth_punct(text):
    """正文里的半角标点，紧邻中文时改成全角（"文,字" -> "文，字"）。"""
    if not text or not _HALFWIDTH_PUNCT_RE.search(text):
        return None
    return _HALFWIDTH_PUNCT_RE.sub(
        lambda m: _HALFWIDTH_PUNCT_MAP[m.group()], text,
    )


def _fix_keyword_terminal_punct(text):
    """关键词内容末尾不应有标点（"关键词一，关键词二。" -> "关键词一，关键词二"）。"""
    stripped = (text or "").rstrip()
    if not stripped or stripped[-1] not in TERMINAL_PUNCT:
        return None
    return stripped[:-1].rstrip() or None


# 条目开头的序号：方括号式 [1]／［1］，或“1.”“1、”“1 ”式。只认 1~3 位数字，
# 且数字后必须紧跟分隔符或空格（否则 "2020. 张三" 这样的年份会被当成序号）；
# 分隔符后也不能还有数字，免得把 "1.2" 这类多级编号切开。
_REFERENCE_NUMBER_RE = re.compile(
    r"^\s*(?:[\[［(（]\s*(\d{1,3})\s*[\]］)）]"
    r"|(\d{1,3})(?:\s*[.．、,，:：)）]|\s|$))(?!\d)\s*"
)


def _fix_reference_entry_number(text):
    """参考文献条目开头的序号改用方括号（"[1] 张三" -> "［1］ 张三"）。

    只重排已有序号，不新增编号：没有序号时返回 None，交给写回层记为跳过。
    """
    match = _REFERENCE_NUMBER_RE.match(text or "")
    if not match:
        return None
    number = match.group(1) or match.group(2)
    rest = (text or "")[match.end():].lstrip()
    if not number or not rest:
        return None
    new_text = f"［{number}］ {rest}"
    return new_text if new_text != text else None


# 一级标题编号："1 绪论"／"1. 绪论"／"1、绪论"。点号后紧跟数字说明是多级编号
# （"1.2 研究现状"其实属于二级标题，交给编号连续性检查，这里不动）。
_HEADING1_NUMBER_RE = re.compile(
    r"^\s*(\d{1,2})\s*(?:[、,，:：]\s*|\.(?!\d)\s*|．(?!\d)\s*)?"
    r"\s*(?![\d.,．])(\S.*)$"
)
# 学生常见写法：“第二章 相关技术介绍”。章号是中文数字时也要能改成模板的“第2章”。
_HEADING1_CN_RE = re.compile(r"^\s*第([一二三四五六七八九十百零〇]+)章\s*(\S.*)$")
_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_number_to_arabic(value):
    """1~99 的中文数字转阿拉伯数字；不认识的写法返回 None。"""
    if not value:
        return None
    if value in _CN_DIGITS:
        return _CN_DIGITS[value]
    if "十" in value:
        left, _, right = value.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        if left and left not in _CN_DIGITS:
            return None
        if right and right not in _CN_DIGITS:
            return None
        return tens * 10 + ones
    return None


def _fix_heading_1_number(text):
    """"1 绪论" / "第二章 绪论" -> "第1章 绪论" / "第2章 绪论"。"""
    text = text or ""
    match = _HEADING1_NUMBER_RE.match(text)
    if match:
        return f"第{match.group(1)}章 {match.group(2).strip()}"
    match = _HEADING1_CN_RE.match(text)
    if not match:
        return None
    number = _cn_number_to_arabic(match.group(1))
    if number is None:
        return None
    return f"第{number}章 {match.group(2).strip()}"


# 规则 ID -> 确定性修复函数。键集合必须与 fix/_core.py 的
# _TEXT_LAYER_RULE_IDS 完全一致（后者决定前端能不能勾选修复），
# 由 tests/test_rules_wiring.py 守住。
_DETERMINISTIC_FIXERS = {
    "reference.entry_period": _fix_reference_period,
    "reference.entry_number": _fix_reference_entry_number,
    "caption.number_space": _fix_caption_number_space,
    "note.prefix": _fix_note_prefix,
    "heading.heading_1.number": _fix_heading_1_number,
    "body.fullwidth_punct": _fix_body_fullwidth_punct,
    "keyword.terminal_punct": _fix_keyword_terminal_punct,
    "en_keyword.terminal_punct": _fix_keyword_terminal_punct,
    "list_item.marker_punct": _fix_list_item_marker_punct,
    "list_item.number_format": _fix_list_item_number_format,
}


def generate_text_fixes(paragraph_texts, issues, rag_service, college_id):
    """根据段落文本生成文字修改，不读写 DOCX。

    - paragraph_texts: {block_index: text}，由 write_back 层提供
    - issues:      检测报告中的 issue 列表（或用户确认的子集）
    - rag_service: RAGService 实例；含语义 issue 时必填
    - college_id:  学院 ID，如 jlu_cs
    返回 changes、skipped 和 llm_calls，不包含 DOCX 对象。
    """
    semantic_issues = [i for i in issues if i.get("source") == "rag"]
    rule_issues = [i for i in issues if i.get("source") == "rule_engine"]

    if semantic_issues and rag_service is None:
        raise ValueError("issues 含语义类问题，必须提供 rag_service")

    changes = []
    skipped = []
    llm_calls = 0
    # 1) 语义类：逐段调用 LLM 改写
    for issue in semantic_issues:
        rule_id = issue.get("rule_id", "")
        if rule_id not in REWRITABLE_SEMANTIC_RULES:
            skipped.append({
                "rule_id": rule_id,
                "reason": MANUAL_ONLY_SEMANTIC_RULES.get(
                    rule_id, "该语义规则不提供自动文本改写"),
            })
            continue
        requirement = issue.get("expected", "")
        category = issue.get("category", "")
        for blk in issue.get("blocks", []):
            idx = blk.get("index")
            try:
                idx = int(idx)
            except (TypeError, ValueError):
                idx = None
            if idx is None or idx not in paragraph_texts:
                skipped.append({"rule_id": rule_id,
                                "reason": f"block index {idx} 不存在或不是 paragraph"})
                continue
            old_text = paragraph_texts.get(idx, "").strip()
            if not old_text or not prefilter_semantic_rule(rule_id, old_text):
                continue  # 该段不涉及条款，不改，也不计入 skip

            llm_calls += 1
            result = rag_service.rewrite_passage(college_id, {
                "rule_id": rule_id,
                "category": category,
                "requirement": requirement,
                "content": old_text,
            })
            new_text = result.get("rewritten", "")
            if not result.get("changed") or not new_text or new_text == old_text:
                continue

            changes.append({"rule_id": rule_id, "index": idx,
                            "old_text": old_text, "new_text": new_text,
                            "semantic": True})

    # 2) 规则引擎文字类：确定性修复（白名单）
    for issue in rule_issues:
        rule_id = issue.get("rule_id", "")
        fixer = _DETERMINISTIC_FIXERS.get(rule_id)
        if fixer is None:
            continue  # 格式类或暂无映射，交由 writeback_service 处理，此处不重复记录
        loc = issue.get("location") or {}
        if loc.get("type") != "paragraph":
            skipped.append({"rule_id": rule_id,
                            "reason": f"非段落级（type={loc.get('type')}），不自动修复"})
            continue
        idx = loc.get("index")
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            idx = None
        if idx is None or idx not in paragraph_texts:
            skipped.append({"rule_id": rule_id,
                            "reason": f"block index {idx} 不存在或不是 paragraph"})
            continue
        old_text = paragraph_texts.get(idx, "")
        new_text = fixer(old_text)
        if new_text is None or new_text == old_text:
            continue
        changes.append({"rule_id": rule_id, "index": idx,
                        "old_text": old_text, "new_text": new_text})

    return {
        "changes": changes,
        "skipped": skipped,
        "applied_count": len(changes),
        "skipped_count": len(skipped),
        "llm_calls": llm_calls,
    }
