# -*- coding: utf-8 -*-
"""基于文档结构的 kind 上下文校正。

classify_kind 是无状态的单段分类器，只能识别“目录/摘要标题/图题”这类
精确锚点，无法识别“跟在某个标题之后”的段落类型（摘要正文、关键词内容、
英文摘要正文、参考文献条目、致谢正文、中文题目、作者信息等）。

本模块在 parse_blocks 产出完整块序列之后，按文档顺序做一次区域状态机扫描，
把落在各“区域”内的 body 段落改标为对应 kind，并把目录条目归入 toc，
从而让 rule_engine 与 semantic 层真正拿到它们期望的 kind。
另有两处非区域的特例：
- 承诺书自身的文字（标题、承诺正文、署名行、日期行）不参与检查，单独成 pledge_title /
  pledge_body 两种 kind。免检只覆盖这几行文字本身，承诺书页上别的块（图片、表格、公式）
  与其后的内容照常检查；
- 封面上的字段行（学校名、论文类型、学院、专业、学号、姓名、日期…）单独成 cover_info：
  规范里只对“中文题目”有要求，对它们没有任何条目，留成 body 会被正文规则逐条量（字号、
  对齐、缩进、行距）并报出与事实不符的错；
- 前置区里的图片/表格打 front_matter 标记：校徽、整页扫描的承诺书、封面信息表都是版式
  元素，不是论文插图/表格，不该按图表规则要求题注。
"""
import re

from .structure import DEFAULT_STRUCTURE, StructureSpec

# 默认结构识别表（词表部分按学院可配，见 recognition/structure.py）；调用方传 structure
# 时按学院的那份走，不传就是改造前的行为。
_DEFAULT_STRUCTURE = StructureSpec(DEFAULT_STRUCTURE)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# 封面日期行：只由年份/月/日组成（"2022年04月""2026年4月26日"）。封面上的日期
# 居中排、字号常落在题目区间，但模板里对"封面日期"没有任何要求条目，它也不是题目。
_COVER_DATE_RE = re.compile(
    r"^\d{4}\s*年\s*(?:\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?)?[\s\u3000]*$")

# 段落末尾粘着的章节锚点：学生少打一次回车，把"参考文献"标题接在上一段末尾
# （实测"…能够被更广泛的使用。                    参考文献"）。两个以上空白说明它是
# 独立写成的一段文字，只是落在同一段里；认出来的话，后面整片参考文献才不会被
# 当成正文查字号（实测 1 份样张因此错报 131 条）。
_TAIL_ANCHOR_SPLIT_RE = re.compile(r"[\s\u3000]{2,}")
_TAIL_ANCHOR_MAX_LEN = 12

# 触发区域切换的锚点标题（classify_kind 已识别出的这些 kind）
_REGION_ANCHORS = {
    "abstract_title": "ABSTRACT",
    "keyword_title": "KEYWORDS",
    "en_abstract_title": "EN_ABSTRACT",
    "en_keyword_title": "EN_KEYWORDS",
    "reference_title": "REFERENCES",
    "acknowledgement_title": "ACK",
    "toc": "TOC",
}

# 承诺书自身的文字：承诺正文（含“承诺”）、署名行（含“签名”）、日期行（“2020年5月20日”）。
# 只有这几行免检；承诺书页上别的内容（学生另加的文字等）照常按它自己的 kind 检查。
# 承诺/签名的词表与日期形态由结构识别表给出（spec.pledge_content_re）。

# 关键词列表的特征：用关键词分隔符（，,；;、）隔开的短行。
# 用于把“紧跟在关键词标签后面的非中文行”分成两类：英文题目还是英文关键词。
_KEYWORD_LIST_RE = re.compile(r"[，,；;、]")

# 题注与正文句的区分：正文里“表1-1给出了本文使用的数据集规模。”这类句子开头与题注
# 完全同形，分类层按“长度 + 句末标点”只能挡住长句（>30 字），短句仍会被当成题注，
# 继而被题注规则量字体/字号/对齐/编号空格。这里补一刀：以句末标点结尾、且上下都不挨着
# 图/表的“题注”，实际是正文句，改回 body。
_CAPTION_KINDS = ("table_caption", "figure_caption")
_SENTENCE_END_RE = re.compile(r"[。！？；]$")
_CAPTION_NEIGHBOR_TYPES = ("table", "image")

_HEADING_KINDS = ("heading_1", "heading_2", "heading_3")

# 正文之前的前置部分区域：封面、摘要、关键词、英文题名/摘要/关键词。
# 这些区域里的"章节标题"是按标题样式排的封面行（中文/英文题名等），不是章标题。
_PRE_BODY_REGIONS = frozenset({
    "FRONT", "ABSTRACT", "KEYWORDS", "EN_TITLE", "EN_ABSTRACT", "EN_KEYWORDS",
})

# 章号形态（正文里写的，或自动编号渲染出来的）：第1章 / 第 1 章 / 1 / 1.1 / 1.1.1。
# 只用于判断“封面区里这个带标题样式的段落到底是不是章标题”。
_HEADING_NUMBER_RE = re.compile(
    r"^第\s*[\d一二三四五六七八九十百]+\s*[章节部分篇]"
    r"|^\d{1,2}(?:\.\d{1,2}){0,2}(?=[\s、.．]|$)")


def _has_heading_number(text, block, spec):
    """这一段有没有章号形态的编号（含自动编号还原出的文字）。"""
    for value in (block.get("auto_number_text"), text):
        value = str(value or "").strip()
        if value and (spec.heading_cn_re.match(value)
                      or _HEADING_NUMBER_RE.match(value)):
            return True
    return False

# 目录区域内，这些“标题类”行实际是目录条目（摘要/ABSTRACT/参考文献/致谢）
_TOC_SECTION_KINDS = {
    "abstract_title",
    "en_abstract_title",
    "reference_title",
    "acknowledgement_title",
}

# 目录条目的点线引导符（…… / 省略号 / 连续中点），正文标题不会出现
_TOC_LEADER_RE = re.compile(r"(?:\.{2,}|\u2026+|\u00b7{2,})")
# 规范化（去掉点线/空白）后结尾的页码：阿拉伯数字或罗马数字
_TOC_PAGE_END_RE = re.compile(r"(?:[0-9]+|[IVX]+)\s*$", re.IGNORECASE)

# 目录条目的开头：章/节编号、必含组成部分（摘要、ABSTRACT、参考文献、致谢、
# 附录…）。用于“没有‘目录’二字标题时”靠条目自身把目录区认出来——只看
# “带点线/行尾页码”会把正文里以数字结尾的句子也算进来（“样本量为 1200”），
# 加上开头形态这道闸才只认真正的章条行。
_TOC_ENTRY_HEAD_RE = re.compile(
    r"^(?:第\s*[一二三四五六七八九十百零\d]+\s*[章节部分篇]"
    r"|\d+(?:\.\d+)*[\s、]"
    r"|摘要|abstract|参考文献|references|致\s*谢|acknowledge?ments?"
    r"|附录|附件|结论|结束语)",
    re.IGNORECASE,
)

# 参考文献条目前缀：［1］ / [1]（允许空格）；序号括号的写法由结构识别表给出
# （spec.ref_entry_number_re），异校用别的序号形态时改那份表。
# 文献类型标识（[J]/[M]/[D]/[DB/OL]/[CP/DK]…）：出现在著录条目里的“是什么文献”的标记，
# 正文句子不会带它。用它把“没写序号但确实是条文献”的段落认出来——认不出来就会被
# 当成正文查宋体小四（参考文献是五号），报出“正文字号不符合要求”这种与事实不符的错。
_REF_TYPE_MARK_RE = re.compile(r"[\[［][A-Z]{1,2}(?:/[A-Z]{1,2})?[\]］]", re.IGNORECASE)
# 参考文献区里“说明/举例”行：［序号］是占位符（不是真序号），“例如：”后面跟的是格式举例。
# 它们本身就是文字说明，不该按“文献条目”查（模板里每种文献类型都配了这两行）。
_REF_NOTE_PREFIX_RE = re.compile(r"^\s*(?:[\[［]\s*序号\s*[\]］]|例如|例：|注\s*[:：]|说明)")
# 参考文献条目的字号：§7.1「正文字体字号：宋体五号字」，模板里的条目与示例条目都是五号。
# 不是五号的段落（如参考文献区末尾 12pt 的说明文字）不当条目判。
_REF_ENTRY_SIZE_PT = 10.5
# 正文之后出现的新章节（附录/附件），用于结束参考文献/致谢区域
_NEW_SECTION_RE = re.compile(r"^(附录|附件|Appendix|附表)")

# 目录条目按“目录内容”排版（宋体小四 12pt、居左）；正文各级标题则是黑体、
# 居中或居左、三号~小四。手工排版的目录既无点线也无页码，只能靠这套格式证据
# 区分“目录条目”与“正文标题”，故字号阈值取小四（12pt）+ 容差。
_TOC_CONTENT_SIZE_PT = 12.0
_SIZE_TOLERANCE_PT = 0.5


# 允许“靠条目自身进入目录区域”的前置区域。目录排在前置部分（封面/摘要之后、
# 正文之前），所以只有这几类区域里才认；正文/参考文献/致谢区里的章条式行
# （附录里的编号清单等）不会被误当成目录。
_TOC_ENTRY_REGIONS = frozenset({
    "FRONT", "ABSTRACT", "KEYWORDS", "EN_TITLE", "EN_ABSTRACT", "EN_KEYWORDS",
})


def _contains_cjk(text):
    return bool(_CJK_RE.search(text))


def _looks_like_toc_entry(text):
    """判断一段 heading 文本是否像目录条目（带点线引导符或结尾页码）。"""
    if _TOC_LEADER_RE.search(text):
        return True
    normalized = re.sub(r"[.\u2026\u3002\u00b7·\s]+", "", text)
    return bool(_TOC_PAGE_END_RE.search(normalized))


def _is_toc_entry_evidence(block, text):
    """这一段是否像目录条目（位置无关的判据）。

    TOC 域的域结果 run 带域标记是最硬的证据；其次是“行尾页码/点线 + 章条开头形态”。
    """
    if _has_field_type(block, "TOC"):
        return True
    return bool(_TOC_ENTRY_HEAD_RE.match(text)) and _looks_like_toc_entry(text)


def _toc_evidence_starts(blocks):
    """目录区“没有标题、但有条目”时的区域起点位置集合。

    单个像目录条目的段落不算证据——正文里以数字结尾的句子（“样本量为 1200”）
    也满足，故要求**连续两段**都像目录条目。这样“目录”二字标题被学生删掉、
    或写成识别不了的写法时，目录条目仍按目录判，不会被当成正文/标题逐条报格式错。
    """
    starts = set()
    run_start = None
    run_length = 0
    for position, block in enumerate(blocks):
        text = (block.get("text") or "").strip()
        is_entry = (block.get("type") == "paragraph" and text
                    and _is_toc_entry_evidence(block, text))
        if is_entry:
            if run_length == 0:
                run_start = position
            run_length += 1
            if run_length == 2:
                starts.add(run_start)
        else:
            run_start = None
            run_length = 0
    return starts


def _has_field_type(block, ftype):
    """段落内是否出现指定域类型（P7）。

    TOC 域生成的首个条目段落（含 begin/instrText/separate）的结果 run
    会被解析层打上 field:{"type": "TOC"}，这里作为目录条目判定的强信号，
    避免只靠点线/页码启发式时把无点线的 TOC 条目误当成正文标题。
    """
    for run in block.get("runs", []):
        field = run.get("field")
        if field and field.get("type") == ftype:
            return True
    return False


def _run_font_size(block):
    """取段落首 run 的字号（pt），无 run 信息时回退到段落级字段。"""
    for run in block.get("runs", []):
        size = run.get("font_size_pt")
        if size is not None:
            return size
    return block.get("font_size_pt")


def _run_cjk_font(block):
    """取段落首 run 的中文字体名，无 run 信息时回退到段落级字段。"""
    for run in block.get("runs", []):
        font = run.get("east_asia_font")
        if font:
            return font
    return block.get("east_asia_font")


def _looks_like_title(block):
    """中文题目启发式：居中，且字号落在标题区间（四号14~三号16.5）或中文字体为黑体。

    仅用于封面（FRONT）区域识别题目，避免把封面上的学校名/论文类型
    （通常字号更大、宋体）误判成题目。
    """
    if block.get("alignment") != "center":
        return False
    if _COVER_DATE_RE.match((block.get("text") or "").strip()):
        # 封面日期行（"2022年04月""2026年4月26日"）：居中、字号常落在题目区间，
        # 但它不是题目。不排除的话它会被认成"中文题目"，整篇按题目要求报字号
        # （实测 24 份样张里 22 份中招），而真正的"中文题目"那一行反倒从不检查。
        return False
    size = _run_font_size(block)
    try:
        if size is not None and 14 <= float(size) <= 16.5:
            return True
    except (TypeError, ValueError):
        pass
    font = _run_cjk_font(block)
    return bool(font and "黑" in str(font))


def _looks_like_reference_item(text, block):
    """参考文献区里的这一段是不是一条著录条目（用来判“漏写序号”这类问题）。

    判据：带文献类型标识（[J]/[M]…）+ 用条目字号（五号）+ 不是说明/举例行。
    说明/举例行由 _REF_NOTE_PREFIX_RE 认（“［序号］主要责任者．…”“例如：[3] …”）。
    """
    if not _REF_TYPE_MARK_RE.search(text) or _REF_NOTE_PREFIX_RE.match(text):
        return False
    size = next((run.get("font_size_pt") for run in (block.get("runs") or [])
                 if (run.get("text") or "").strip()), None)
    return size is None or abs(size - _REF_ENTRY_SIZE_PT) <= 0.5


def _auto_entry_number(block, spec):
    """Word 自动编号渲染出的序号（“[47]”）也算条目的序号。

    实测学生论文的参考文献列表整列用自动编号：序号不在段落文字里，
    只按文本匹配会把每一条都漏掉（条目继而掉回正文被查宋体小四）。
    渲染不出编号（auto_number_text 缺席）时保持原判定。
    """
    value = str(block.get("auto_number_text") or "").strip()
    return bool(value) and bool(spec.ref_entry_number_re.match(value))


def _looks_like_new_section(text):
    """正文之后出现的新章节（附录/附件），应结束参考文献/致谢区域。"""
    return bool(_NEW_SECTION_RE.match((text or "").strip()))


def _trailing_anchor_kind(text, spec):
    """段落末尾（两个以上空白之后）若是一个章节锚点写法，返回它的 kind。

    只在末尾那一小段**正好等于**锚点写法时命中，不猜内容。
    """
    parts = _TAIL_ANCHOR_SPLIT_RE.split((text or "").strip())
    if len(parts) < 2:
        return None
    tail = re.sub(r"[\s\u3000]", "", parts[-1])
    if not tail or len(tail) > _TAIL_ANCHOR_MAX_LEN:
        return None
    return spec.anchor_kinds.get(tail) or spec.anchor_kinds.get(tail.lower())


def _looks_like_heading_format(block):
    """该行是否带正文标题格式（黑体 / 居中 / 字号大于目录内容的小四）。

    用于在目录区域里把“没有点线也没有页码的目录条目”与“正文标题”分开，
    以及在参考文献区域里识别没被 classify_kind 认出的章节标题
    （如“致  谢”“结束语”）：文献续行是宋体五号 10.5pt 左顶格，不会命中。
    """
    font = _run_cjk_font(block)
    if font and "黑" in str(font):
        return True
    if block.get("alignment") == "center":
        return True
    size = _run_font_size(block)
    if size is None:
        return False
    try:
        return float(size) > _TOC_CONTENT_SIZE_PT + _SIZE_TOLERANCE_PT
    except (TypeError, ValueError):
        return False


def _demote_sentence_like_captions(blocks):
    """把“看起来像题注、其实是正文句”的段落改回 body（原地修改）。

    判据两条同时成立：以句末标点结尾（题注是名词短语，不加句号），且紧邻的前后块都不是
    图/表（真题注旁边一定有它的图或表）。只看这两条，不猜内容，避免把真题注误改。
    """
    for index, block in enumerate(blocks):
        if block.get("kind") not in _CAPTION_KINDS:
            continue
        if not _SENTENCE_END_RE.search((block.get("text") or "").strip()):
            continue
        neighbors = [blocks[at] for at in (index - 1, index + 1)
                     if 0 <= at < len(blocks)]
        if any(n.get("type") in _CAPTION_NEIGHBOR_TYPES for n in neighbors):
            continue
        block["kind"] = "body"


def _mark_front_matter_blocks(blocks):
    """给前置区（承诺书页、封面、目录之前）的图片/表格打标记（原地修改）。

    这些是版式元素（校徽、学生整页扫描的承诺书、封面信息表），不是论文插图/表格，
    按图表规则要求题注属于“报错内容与事实不符”。标记只在确实存在前置区边界
    （见 apply_contextual_kinds 的 has_cover_boundary）时才打。

    这里按块在序列中的位置判断，不看 block["index"]：解析层是先校正 kind、后统一
    编号的，本函数运行时段落块上还没有 index。
    """
    boundary_at = None
    for position, block in enumerate(blocks):
        if block.get("type") != "paragraph":
            continue
        if block.get("kind") in _REGION_ANCHORS or block.get("kind") in _HEADING_KINDS:
            boundary_at = position
            break
    if boundary_at is None:
        return
    for block in blocks[:boundary_at]:
        if block.get("type") in ("image", "table"):
            block["front_matter"] = True


def apply_contextual_kinds(blocks, structure=None):
    """按文档顺序校正 paragraph 块的 kind（原地修改，返回 blocks）。

    结构识别词表（承诺书文字、参考文献条目序号形态…）取自 `structure`；
    不传时用默认表，也就是改造前的行为。

    状态机以“区域”为单位：
      FRONT       —— 文档开头，摘要标题之前
      ABSTRACT    —— 摘要标题之后
      KEYWORDS    —— 关键词标题之后
      EN_TITLE    —— 中文关键词之后、Abstract 之前的英文题目区
      EN_ABSTRACT —— Abstract 标题之后
      EN_KEYWORDS —— Keywords 标题之后
      REFERENCES  —— 参考文献标题之后
      ACK         —— 致谢标题之后
      TOC         —— 目录标题之后
      BODY        —— 正文章节标题之后

    另有 pledge_open 标志（不属于区域）：承诺书标题出现后，紧随的承诺书文字
    （承诺正文、署名行、日期行）改标为 pledge_body，不参与检查；一旦出现别的内容
    就立即结束免检，后面的段落照常检查。
    """
    # 封面区的边界：出现这些锚点或章节标题，才说明前面那一段确实是封面（前置部分）。
    # 没有边界就不能把免检权交出去——例如只给了一段正文的片段，它整篇都落在 FRONT 区，
    # 若整篇按封面处理就会一条检查都不做。
    has_cover_boundary = bool(
        {b.get("kind") for b in blocks if b.get("type") == "paragraph"}
        & (set(_REGION_ANCHORS) | set(_HEADING_KINDS))
    )
    # 有无正文锚点（摘要/关键词/目录/参考文献/致谢）：封面区里的"章标题"要靠它
    # 才能判定为封面题目而不是真章标题（见循环里的 ③）。
    has_region_anchor = bool(
        {b.get("kind") for b in blocks if b.get("type") == "paragraph"}
        & set(_REGION_ANCHORS)
    )
    _demote_sentence_like_captions(blocks)
    _mark_front_matter_blocks(blocks)
    toc_evidence_starts = _toc_evidence_starts(blocks)
    spec = structure or _DEFAULT_STRUCTURE
    region = "FRONT"
    title_seen = False
    pledge_open = False
    keyword_body_seen = False
    entry_seen = False
    # 摘要区里“正文是否已开始”：开始之前的居中大字号行是重复的中文题目（§2.1）。
    abstract_body_seen = False
    # 英文摘要区同理：正文之前的居中行是重复的英文题目（§2.3）。
    en_abstract_body_seen = False

    for position, b in enumerate(blocks):
        if b.get("type") != "paragraph":
            continue
        text = (b.get("text") or "").strip()
        if not text:
            continue
        kind = b.get("kind")

        # ①' “目录”二字标题被删掉或写成识别不了的写法时，靠条目自身进入目录区域：
        #     不进入的话目录条目会被当正文/章节标题逐条报格式错（实测多出 11 条误报）。
        if region != "TOC" and position in toc_evidence_starts \
                and region in _TOC_ENTRY_REGIONS:
            region = "TOC"

        # ① 目录区域：任何带点线/页码的行都是目录条目，归一为 toc；
        #    遇不带页码的章节标题则说明正文开始，离开目录区域。
        if region == "TOC":
            # P7：TOC 域结果 run 带域标记，优先按目录条目处理；
            # 否则回退到旧的点线/页码启发式。
            if _has_field_type(b, "TOC") or _looks_like_toc_entry(text):
                b["kind"] = "toc"
                continue
            if kind in _TOC_SECTION_KINDS:
                b["kind"] = "toc"  # 目录里的“摘要/ABSTRACT/参考文献/致谢”行
                continue
            if kind in _HEADING_KINDS:
                # 手工排版的目录可以既无点线也无页码（“第1章  绪论”后面直接
                # 换行），此时用格式证据区分：目录条目按“目录内容”排版
                # （宋体小四 居左），带标题格式的行说明目录结束、正文开始。
                if _looks_like_heading_format(b):
                    region = "BODY"
                else:
                    b["kind"] = "toc"
                continue
            continue  # 目录区域内的其他 kind 保持不变

        # ② 锚点标题：切换区域，标题本身 kind 不变
        if kind in _REGION_ANCHORS:
            region = _REGION_ANCHORS[kind]
            if kind == "abstract_title":
                abstract_body_seen = False
            elif kind == "keyword_title":
                keyword_body_seen = False
            elif kind == "en_abstract_title":
                en_abstract_body_seen = False
            continue

        # ③ 正文章节标题：进入正文区域
        if kind in _HEADING_KINDS:
            # 封面题目被学生套了"标题 1"样式时，分类层只能按样式认成一级标题。
            # 封面题目不是章标题：真进正文区就会被按一级标题查编号/字体/字号
            # （实测 6 段 20 条）。凡在正文之前的前置区域（封面/摘要/关键词/英文题名
            # 各区）、这段又没有任何章号形态，就改标成 body 交给下面的区域分支按它
            # 自己的类型处理（题目行 → title/en_title，其余 → cover_info 等）。
            # 真章标题都带章号（"第1章"，或自动编号还原出的"第1章"），不会中这一刀；
            # 文档里没有正文锚点时也不动，免得把片段文档的章标题整片降级。
            if has_region_anchor and region in _PRE_BODY_REGIONS \
                    and not _has_heading_number(text, b, spec):
                kind = b["kind"] = "body"
            else:
                region = "BODY"
                continue

        # ③' 承诺书标题：打开“承诺书文字免检”。免检只覆盖紧随其后的承诺书文字，
        #     遇到别的内容（学生另加的文字、封面行等）立即关闭，见 FRONT 分支。
        if kind == "pledge_title":
            pledge_open = True
            continue

        # ④ 区域内 body 段落重标注
        if kind != "body":
            continue

        # ④' 段落末尾粘着一个章节锚点（"…以上。      参考文献"）：这一段本身是正文，
        #     但从下一段起按锚点切换区域。学生少打一次回车就把标题接在了段尾，
        #     不切换的话后面整片参考文献会被当正文逐条查字号（实测 1 份样张错报 131 条）。
        tail_kind = _trailing_anchor_kind(text, spec)
        if tail_kind in _REGION_ANCHORS:
            region = _REGION_ANCHORS[tail_kind]
            if tail_kind == "keyword_title":
                keyword_body_seen = False
            continue

        if region == "FRONT":
            if pledge_open:
                if spec.pledge_content_re.search(text):
                    b["kind"] = "pledge_body"
                    continue
                # 承诺书页上出现的其他内容不是承诺书的一部分，免检到此为止
                pledge_open = False
            if not title_seen and _looks_like_title(b):
                b["kind"] = "title"
                title_seen = True
            elif title_seen and b.get("alignment") == "right":
                b["kind"] = "author_info"
            elif has_cover_boundary:
                # 封面上的字段行（学校名、论文类型、学院、专业、学号、姓名、日期…）：
                # 规范里只规定了“中文题目”，对它们没有任何条目，留成 body 会被正文规则
                # 逐条量（字号/对齐/缩进/行距）并报出与事实不符的错，故单列 cover_info。
                b["kind"] = "cover_info"
            # 没有封面区边界时保持 body，维持原有行为
        elif region == "ABSTRACT":
            if not abstract_body_seen and _looks_like_title(b):
                # §2.1：摘要页开头重复的“中文题目”行（黑体小三、居中）是题目，
                # 不是摘要正文；按正文判会报“摘要正文字体应为宋体”，与事实不符
                # （实测 6 份样张 14 条）。正文一旦开始，居中行不再当题目。
                b["kind"] = "title"
            else:
                b["kind"] = "abstract_body"
                abstract_body_seen = True
        elif region == "KEYWORDS":
            if not keyword_body_seen:
                # 计软模板的关键词排在标签行的同一行（§2.4/§2.7），标签行之后紧接着
                # 的是居中的英文题目（§2.5）——它绝不是关键词内容。若照旧一律当成
                # keyword_body，英文题目会被按关键词内容查：报“关键词数量不足 1 个”
                # 与“关键词内容字号不符合要求”，而学生一个字都没写错。
                if not _contains_cjk(text) and not _KEYWORD_LIST_RE.search(text) \
                        and _looks_like_title(b):
                    b["kind"] = "en_title"
                    region = "EN_TITLE"
                else:
                    b["kind"] = "keyword_body"
                    keyword_body_seen = True
            elif _contains_cjk(text):
                b["kind"] = "keyword_body"  # 多段关键词兜底
            else:
                # 中文关键词之后、Abstract 之前的非中文段落 → 英文题目
                b["kind"] = "en_title"
                region = "EN_TITLE"
        elif region == "EN_TITLE":
            b["kind"] = "en_title"  # 多行英文题目兜底
        elif region == "EN_ABSTRACT":
            if not en_abstract_body_seen and not _contains_cjk(text) \
                    and _looks_like_title(b):
                # §2.3：英文摘要页上重复的“英文题目”行（Times New Roman 小三、
                # 居中）。有的排版把它排在 “Abstract” 标题之后，它照样是题目，
                # 不是英文摘要正文（实测 7 份样张各 1 条被当正文报 12pt）。
                b["kind"] = "en_title"
            else:
                b["kind"] = "en_abstract_body"
                en_abstract_body_seen = True
        elif region == "EN_KEYWORDS":
            b["kind"] = "en_keyword_body"
        elif region == "REFERENCES":
            if spec.ref_entry_number_re.match(text) or _auto_entry_number(b, spec):
                # 序号也可能由 Word 自动编号生成、不在段落文字里（实测学生的
                # 参考文献整列自动编号，形如 [47]）。只看文本会把这类条目整条
                # 漏掉，继而掉回正文被查宋体小四（实测张权辉 2 条）。
                b["kind"] = "reference_entry"
                entry_seen = True
            elif _looks_like_new_section(text) or _looks_like_heading_format(b):
                # 附录/附件，或带标题格式却没被认出的章节标题（“致  谢”
                # “结束语”等）：文献列表到此为止。否则从这里到文末的所有段落
                # 都会被当成文献续行，按宋体 10.5pt 逐条报错。
                region = "BODY"
            elif _looks_like_reference_item(text, b):
                # §7.1（模板原文）：「参考文献的序号左顶格，并用数字加方括号表示」。
                # 没写序号的著录条目要按“条目”判，才能报出“应以方括号序号开头”；
                # 归成正文会报“正文字号不符合要求”（条目五号、正文小四），与事实不符。
                b["kind"] = "reference_entry"
                # 认出一条真条目后要置位，否则后面没有类型标识的条目会掉回
                # 正文（实测黄书成 79 条、张权辉 4 条 body.* 误报）。
                entry_seen = True
            elif entry_seen or _REF_TYPE_MARK_RE.search(text):
                # P5：一条文献因过长换行时会拆成多个段落（或段落内换行后的
                # 独立段），这些续行不满足“［n］”前缀，旧实现保持 body，
                # 于是被当正文查宋体/12pt/两端对齐/首行缩进 24pt 并自动改写，
                # 把参考文献改坏（违反 §7.1 左顶格、宋体 10.5）。单列一种 kind，
                # 只做字体字号检查，不再套正文格式。
                # 没写序号的著录条目（类型标识 [J]/[M] 在、前面的［n］漏了）同样是
                # 文献列表里的一行：按正文查会报“正文字号不符合要求”（五号 vs 小四）。
                b["kind"] = "reference_continuation"
            # 参考文献标题后、首条文献前，既没有［n］序号也没有类型标识的行
            # （空行、整段说明文字）保持 body
        elif region == "ACK":
            if _looks_like_new_section(text):
                region = "BODY"
            else:
                b["kind"] = "acknowledgement_body"
        # region == "BODY"：body 保持 body

    return blocks
