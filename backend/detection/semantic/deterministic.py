# -*- coding: utf-8 -*-
"""确定性语义检查：零 LLM，正则判定，全文覆盖。

只依赖 patterns 里的正则辅助与 config/rules 里的 make_issue，不触碰 RAG。
所有函数都是纯模块函数，便于 checker 编排与单测。
"""
import re

from .rules import CANDIDATE_CONTEXT, CANDIDATE_MERGE_GAP, make_issue
from .patterns import _TIME_RANGE_RE

# 文献类型标识：模板写“[J]”“[M]”，学生也常写全角“［J］”；[DB/OL] 这类
# “类型/载体”组合标识同样认（模板 §6.2 表里列了 [DB/OL]、[CP/DK]、[M/CD]）。
_REF_MARK_RE = re.compile(r"[\[［]\s*([A-Za-z]{1,3}(?:\s*/\s*[A-Za-z]{1,3})?)\s*[\]］]")

# 出版年份：模板 9 条著录样例每一条都有年份/日期。只认 4 位年份，范围放宽到
# 1500-2099：引更早的文献（如 1870 年）不该被报成“缺出版年份”。
_REF_YEAR_RE = re.compile(r"(?:1[5-9]\d{2}|20\d{2})")

# 起止页码：样例写作“卷号(期号)：起止页码”，页码紧跟冒号。
_REF_PAGES_RE = re.compile(r"[:：]\s*\d")

# 起止页码“痕迹”：页码可能写了、但没按样例接在“卷号(期号)：”后（实测樊静文
# “2016. 265–283.”、蒙博“学报,1-25.”）。这时报“未按样例接排”，不能报“缺页码”
# ——学生可见的话必须与条目事实相符。
_REF_PAGE_SPAN_RE = re.compile(r"\d+\s*[-–—]\s*\d+")

# 类型标识后面允许出现的分隔符：模板样例都写作“．”，会议论文集的分隔符模板写
# “∥”，GB/T 7714-2005 写“//”——这几种都算，其余（如“[J] 刊名”）算缺句点。
_REF_MARK_TAIL = "．.∥／/"

# 列表总项序号：模板写作“1．、2．”，学生常写成半角句点“1.”“3.接口设计：”。
# 只认段首 1–2 位阿拉伯数字 + 半角句点 + 紧接中文或字母标题；
# 句点后是数字的（3.14、2.0 版本、2020.10）不算；标题块不在 list_item 里也不会命中。
_TOTAL_ITEM_HALFWIDTH_RE = re.compile(
    r"(?:(?<=^)|(?<=[\n\r]))\s*\d{1,2}\s*\.\s*(?=[\u4e00-\u9fffA-Za-z])")


def run_deterministic(rule, blocks_by_kind):
    """① 确定性规则：不调用 LLM，覆盖全文。"""
    rule_id = rule["rule_id"]
    if rule_id == "acknowledgement.content":
        return check_acknowledgement(rule, blocks_by_kind)
    if rule_id == "reference.format":
        return check_reference(rule, blocks_by_kind)
    if rule_id == "list.numbering":
        return check_list_numbering(rule, blocks_by_kind)
    if rule_id == "units":
        return check_units(rule, blocks_by_kind)
    if rule_id == "numerals":
        return check_numerals(rule, blocks_by_kind)
    return []


def check_acknowledgement(rule, blocks_by_kind):
    issues = []
    for b in blocks_by_kind.get("acknowledgement_body", []):
        text = b.get("text") or ""
        if text and _TIME_RANGE_RE.search(text):
            issues.append(make_issue(
                rule, [{"index": b.get("index"), "text": text}],
                actual=text[:60],
                conclusion="致谢正文包含时间段表述",
                basis="确定性检查：时间段正则匹配",
                suggestion="删除致谢中“从某年某月到某年某月”之类的时间段文字",
            ))
    return issues


def check_reference(rule, blocks_by_kind):
    """§6：条目的类型标识与著录项（齐全、顺序），判据取自模板逐类给出的样例。

    条目在 Word 里常折行成另一段（kind=reference_continuation），所以先按文档
    顺序把条目与它的续行拼成"一条"再判：只看条目段本身，写在续行上的起止页码、
    出版年份会被当成"缺项"，报出与事实不符的错。
    """
    issues = []
    samples = rule.get("entry_samples") or {}
    features = rule.get("entry_features") or {}
    for entry, text in _reference_entries(blocks_by_kind):
        if not text:
            continue
        mark = _REF_MARK_RE.search(text)
        if mark is None:
            issues.append(make_issue(
                rule, [entry], actual=text[:60],
                conclusion="参考文献条目缺少文献类型标识",
                basis="确定性检查：未匹配到 [J]/[M]/[D] 等类型标识",
                suggestion="在条目中补充文献类型标识，如 [J]（期刊）、[M]（专著）、[D]（学位论文）",
            ))
            continue
        problems = _entry_problems(text, mark, features)
        if not problems:
            continue
        sample = _entry_sample(mark.group(1), samples)
        issues.append(make_issue(
            rule, [entry], actual=text[:60], expected=sample,
            conclusion="参考文献条目的著录项与模板样例不符：" + "；".join(problems),
            basis="确定性检查：按模板该类条目的样例核对著录项",
            suggestion="按模板该类条目的样例著录" + (f"：{sample}" if sample else ""),
        ))
    return issues


def _reference_entries(blocks_by_kind):
    """按文档顺序把每个条目与其续行拼成一条，返回 [(条目块, 整条文本)]。"""
    entries = blocks_by_kind.get("reference_entry", [])
    continuations = sorted(
        (b for b in blocks_by_kind.get("reference_continuation", [])
         if isinstance(b.get("index"), int)),
        key=lambda b: b["index"],
    )
    out = []
    for i, entry in enumerate(entries):
        index = entry.get("index")
        limit = entries[i + 1].get("index") if i + 1 < len(entries) else None
        text = entry.get("text") or ""
        if isinstance(index, int):
            for block in continuations:
                if block["index"] <= index:
                    continue
                if limit is not None and block["index"] >= limit:
                    break
                text += block.get("text") or ""
        out.append(({"index": index, "text": (entry.get("text") or "").strip()},
                    text.strip()))
    return out


def _entry_problems(text, mark, features):
    """条目缺了哪些部件（含年份顺序错），返回人话描述的列表。

    类型标识决定该按哪条样例判：有样例的按样例要求，混合标识（[DB/OL] 这类
    “类型/载体”）按电子文献样例，其余（如“其它[Z]”）只查各条样例共有的部件。
    """
    letter = mark.group(1).upper()
    if letter in features:
        wanted = features[letter]
    elif "/" in letter:
        wanted = features.get("电子文献", ())
    else:
        wanted = features.get("其它", ())
    after = text[mark.end():]
    problems = []
    tail = after.lstrip()
    if "mark_period" in wanted and tail and tail[0] not in _REF_MARK_TAIL:
        problems.append("文献类型标识后面缺句点")
    if "year" in wanted and not _REF_YEAR_RE.search(after):
        problems.append("出版年份排在文献类型标识之前"
                        if _REF_YEAR_RE.search(text[:mark.start()])
                        else "缺出版年份")
    if "colon" in wanted and "：" not in after and ":" not in after:
        problems.append("缺“：”分隔的出版地／保存地等项")
    if "pages" in wanted and not _REF_PAGES_RE.search(after):
        if _REF_PAGE_SPAN_RE.search(after):
            problems.append("起止页码未按“卷号(期号)：起止页码”的样例接排")
        else:
            problems.append("缺卷(期)号后的起止页码")
    return problems


def _entry_sample(letter, samples):
    """该类型标识对应的模板样例（报错时作为“应为”的对照）。"""
    if not samples:
        return None
    letter = letter.upper()
    if letter in samples:
        return samples[letter]
    return samples.get("电子文献") if "/" in letter else None


def scan_deterministic(rule, blocks_by_kind, patterns, suggestion):
    """确定性扫描通用骨架：对相关 kind 的每个段落跑若干正则，命中即生成 issue。"""
    issues = []
    for kind in rule["kinds"]:
        for b in blocks_by_kind.get(kind, []):
            text = b.get("text") or ""
            if not text:
                continue
            idx = b.get("index")
            # 收集该段落内所有正则命中区间
            spans = []
            for pat, reason in patterns:
                for m in pat.finditer(text):
                    spans.append((m.start(), m.end(), reason))
            if not spans:
                continue
            spans.sort(key=lambda x: x[0])
            # 合并相邻命中，避免同一句被多组正则重复上报
            merged = []
            for s, e, reason in spans:
                if merged and s - merged[-1][1] < CANDIDATE_MERGE_GAP:
                    ps, pe, _ = merged[-1]
                    merged[-1] = (ps, max(pe, e), reason)
                else:
                    merged.append((s, e, reason))
            for s, e, reason in merged:
                cs = max(0, s - CANDIDATE_CONTEXT)
                ce = min(len(text), e + CANDIDATE_CONTEXT)
                frag = text[cs:ce].strip()
                issues.append(make_issue(
                    rule, [{"index": idx, "text": frag}],
                    actual=frag[:60],
                    conclusion=reason,
                    basis="确定性检查：正则匹配",
                    suggestion=suggestion,
                ))
    return issues


def check_list_numbering(rule, blocks_by_kind):
    patterns = [
        # “（1）、”这类括号后加标点的写法。**必须**先确认括号里的数字处在列表位置上：
        # 否则“Distill, 1(10), e2.”（卷期号）、“34(1):277-311”（卷期:页码）、
        # “_.gt(90)。”（函数实参）都会被当成“分项序号”——实测 5 条里 3 条是这么来的。
        (re.compile(r"(?:(?<=^)|(?<=[\n\r。；;！!？?：:，,、]))"
                    r"\s*[（(]\s*\d{1,2}\s*[)）]\s*[、，。；：,.?;:!]"),
         "分项序号括号后不应再加标点"),
        # 模板给的总项序号是"1．、2．"，没有 0 开头的序号：`0、1、Accuracy、Macro avg…`
        # 是图例/类别标注，不是列表序号（实测 2 条里 1 条这么来的），故排掉 0 开头。
        (re.compile(r"(?:(?<=^)|(?<=[\n\r。；;！!？?]))\s*(?!0\s*[、，,])\d{1,2}\s*[、，,]"),
         "列表总项序号应采用 1．、2．形式，不用顿号或逗号"),
        (_TOTAL_ITEM_HALFWIDTH_RE,
         "列表总项序号应采用全角“．”，不用半角句点"),
    ]
    return scan_deterministic(
        rule, blocks_by_kind, patterns,
        "总项用 1．、2．；分项用（1）（2）；括号后不加标点",
    )


def check_units(rule, blocks_by_kind):
    patterns = [
        # “公里/公斤”不是非法定计量单位的名称：它们指代的 km/kg 本身是法定计量单位，
        # 只是标准名称写作“千米/千克”。话术必须与事实相符，否则学生会照着规范原文
        # （只说“采用 GB3100”）来质疑这条报错。市斤/市尺/市亩等市制单位才是不合规的。
        (re.compile(r"公里|公斤|公分|公尺|公亩|公升|市斤|市尺|市寸|市亩"),
         "计量单位应使用国家标准名称与符号"),
        (re.compile(r"(?<=\d)(?:KM|Kg|KG|KW|kw|KPa|KPA|kpa|Mpa|MPA|HZ|hz|MHZ|Mhz|mhz|GHZ|Ghz|ghz|DB|db)\b"),
         "计量单位符号大小写不符合规范"),
    ]
    return scan_deterministic(
        rule, blocks_by_kind, patterns,
        "改用国家标准单位及其规范符号（如 千米/km、千克/kg、厘米/cm、米/m、升/L）",
    )


def check_numerals(rule, blocks_by_kind):
    patterns = [
        (re.compile(r"百分之[零一二三四五六七八九十百千万亿点]+"),
         "百分比应使用阿拉伯数字（如 25%）"),
        # 只认“汉字数字 + **计量单位**”。模板原句是“测量统计数据一律用阿拉伯数字”，
        # 所以单位表里不能放“元/亿/兆”这类**既能当单位又能当词素**的字：
        # “二元分类”“三元组”“千亿稀疏特征”“万亿参数”都会被误判成钱数（实测 39 处
        # 匹配里 25 处是这类术语）。货币/量级只在“万/亿 + 元”成词时才算（万元、亿元）。
        # 代价：“五元”这种单字货币量不再报（论文里极少，且不是“测量统计数据”）。
        # 量级字不许"半路起头"：`(?<![\d万千亿])` 挡住“4**万亿元**”这种——
        # 数字是阿拉伯数字，被当汉字数字的其实是单位里的“亿”（实测 1 条）。
        (re.compile(r"(?<![\d万千亿])[零一二三四五六七八九十百千万亿两]+"
                    r"(?:千米|公里|米|厘米|毫米|千克|公斤|克|毫克|吨|升|毫升"
                    r"|摄氏度|℃|%|kg|km|mm|cm|m|g|Pa|kPa|MPa|Hz|V|W|A"
                    r"|万元|亿元|秒|分钟|小时)(?![内前后]|之内|以内)"),
         "测量统计数据应使用阿拉伯数字"),
    ]
    return scan_deterministic(
        rule, blocks_by_kind, patterns,
        "测量、统计数据改用阿拉伯数字表示",
    )
