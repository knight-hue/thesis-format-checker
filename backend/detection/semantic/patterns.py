# -*- coding: utf-8 -*-
"""语义检查的模式表、候选定位与送审前预筛。

统一收纳三类“正则/模式”知识，避免散落：
- 规则预筛启发式（prefilter_semantic_rule，供 text_rewrite_service 复用）
- 定位规则候选片段扫描（_locate_candidates / _obviously_compliant）
- 确定性检查用到的正则辅助（参考文献类型标识、目录必含项、时间段）
"""
import re

from .rules import CANDIDATE_CONTEXT, CANDIDATE_MERGE_GAP


def prefilter_semantic_rule(rule_id, content):
    """廉价启发式预筛：返回 True 表示值得调用 LLM，False 表示明显不涉及该条款。

    所有启发式都偏保守（宁可多送 LLM，不可漏报）。
    模块级函数，供改写（text_rewrite_service）复用。
    """
    if rule_id == "list.numbering":
        # 无任何列表序号特征才跳过
        return bool(re.search(r"(?:\d\s*[.．、]|（\s*\d+\s*）|\(\s*\d+\s*\))", content))
    if rule_id == "terminology":
        # 不含英文（外文缩写/外国人名都需要英文）才跳过
        return bool(re.search(r"[A-Za-z]{2,}", content))
    if rule_id == "units":
        # 不含数字或单位符号才跳过
        return bool(re.search(r"\d|[%℃]|\b(?:km|kg|mm|cm|m/s|MPa|kPa|Hz|V|W|A)\b",
                              content, re.IGNORECASE))
    if rule_id == "numerals":
        # 不含汉字数字才跳过
        return bool(re.search(r"[一二三四五六七八九十百千万亿零两]", content))
    if rule_id == "formula_axis":
        # 不含坐标/轴/公式编号特征才跳过
        return bool(re.search(r"坐标|轴|[（(]\d+-\d+[）)]", content))
    if rule_id == "acknowledgement.content":
        # 不含"数字年月 到/至 —"的时间段特征才跳过（与 _TIME_RANGE_RE 同口径：
        # 不限数字会把"感到恍惚，四年"这种跨词偶合当成时间段）
        return bool(re.search(r"\d{1,4}\s*[年月].{0,6}[到至—～~]|[到至—～~].{0,6}\d{1,4}\s*[年月]",
                              content))
    # 其余规则（如参考文献著录）不预筛，总是调用 LLM
    return True


def _locate_candidates(rule_id, text):
    """全文扫出候选片段（±上下文窗口），返回片段文本列表。

    正则只负责"找可疑点"，是否真违规交给 LLM 精判（宁可多送，不可漏送）。

    只处理**定位规则**（`LOCATED_SEMANTIC_RULES`）：列表序号/单位/数字用法三条已是
    确定性规则，走 `deterministic.py`，这里不再给它们留分支——留着就是"看起来能用、
    其实永远走不到"的假开关。
    """
    if rule_id == "terminology":
        pattern = r"[A-Za-z][A-Za-z0-9./\-]{1,}"
    elif rule_id == "formula_axis":
        pattern = r"坐标|轴|[（(]\d+-\d+[）)]"
    else:
        return []

    matches = list(re.finditer(pattern, text))
    if not matches:
        return []

    # 取上下文窗口
    windows = []
    for m in matches:
        s = max(0, m.start() - CANDIDATE_CONTEXT)
        e = min(len(text), m.end() + CANDIDATE_CONTEXT)
        windows.append([s, e])

    # 合并相邻片段
    merged = []
    for w in windows:
        if merged and w[0] - merged[-1][1] < CANDIDATE_MERGE_GAP:
            merged[-1][1] = max(merged[-1][1], w[1])
        else:
            merged.append(w)

    fragments = [text[s:e].strip() for s, e in merged]
    fragments = [f for f in fragments if f]

    # 保序去重
    seen = set()
    unique = []
    for f in fragments:
        if f not in seen:
            seen.add(f)
            unique.append(f)

    return unique


def _obviously_compliant(rule_id, frag):
    """保守的送审前预筛：丢弃 LLM 也无法可靠判定的噪声片段。

    只做"几乎必合规 / 必无法判定"的过滤，宁可不滤，不可误滤。
    """
    if rule_id == "formula_axis":
        # 去掉图号「（1-1）」「(1-1)」后，若不再含"坐标/轴"，说明片段只是
        # 顺带提到图（如"图中给出了…曲线"），并非坐标图本身，判合规不送审。
        stripped = re.sub(r"[（(]\s*\d+\s*[-—~～]\s*\d+\s*[）)]", "", frag)
        if not re.search(r"坐标|轴", stripped):
            return True
    return False


# ====================== 术语 token 常量 ======================
_TERM_RE = re.compile(r"[A-Za-z][A-Za-z0-9./\-]{1,}")

# 纯小写虚词/功能词：不可能是外文缩写或外国人名，直接跳过以减少噪声。
_TERM_SKIP_WORDS = {
    "the", "a", "an", "of", "and", "or", "in", "on", "at", "to", "for", "with",
    "by", "from", "as", "is", "are", "was", "were", "be", "been", "being", "it",
    "its", "this", "that", "these", "those", "we", "our", "they", "their", "he",
    "she", "his", "her", "do", "does", "did", "not", "no", "but", "if", "then",
    "than", "so", "can", "may", "will", "would", "should", "could", "shall",
    "must", "has", "have", "had", "which", "who", "whom", "when", "where", "why",
    "how", "what", "all", "each", "every", "some", "any", "such", "into", "over",
    "under", "between", "through", "during", "before", "after", "above", "below",
    "up", "down", "out", "off", "also", "too", "very", "just", "more", "most",
    "other", "new", "both", "same", "own", "per", "via", "vs", "et", "al",
}





# ====================== 确定性检查辅助 ======================
# 致谢里的“时间段”表述（“从2020年3月到2021年5月”一类）。
# 两端必须是**带数字的日期**：不限数字的话，“感到恍惚，四年”这种跨词的偶合会被当成时间段
# （实测 1 条），而模板这句的原意是别写出“从…年到…年”这种具体区间。
_TIME_RANGE_RE = re.compile(
    r"\d{1,4}\s*[年月].{0,6}[到至—～~]|[到至—～~].{0,6}\d{1,4}\s*[年月]")
