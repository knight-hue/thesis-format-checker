"""段落语义类型分类。

"这一段是什么"的判据分两处：
- **词表**（章节锚点、承诺书/声明页、封面字段、题注前缀、条目序号括号）在
  `recognition/structure.py` 的结构识别表里，按学院可配，默认值 = 改造前的行为；
- **形态类判据**（编号数字形态、编号分隔符、目录点线、"整句话不是标题"的句子
  护栏）仍在下面，因为它们是解析逻辑而不是措辞，外置成字符串反而更难查。
"""
import re

# 段末排版说明括注的剥离口径与 detection 共用，唯一定义在 docx_common/notes.py。
from backend.docx_common.notes import strip_trailing_note

from .structure import DEFAULT_STRUCTURE, StructureSpec

# 默认结构识别表（值 = 改造前的模块常量）。调用方传 structure 时按学院的那份走。
_DEFAULT_STRUCTURE = StructureSpec(DEFAULT_STRUCTURE)
# 旧实现（P4）：
#     _NUM_HEADING = re.compile(r"^(\d+(?:\.\d+)*)[\s\.、]")
# 要求编号后必须有空格/点/顿号，导致“1.1研究背景”（数字后无分隔）判不成标题。
# 放宽为前瞻断言：编号后可跟空白/顿号，也可直接跟中文（“1.1研究背景”）
# 或直接到行尾（Word 自动编号时文本里可能只留编号）。
# .／． 只在其后不是数字时算分隔符（兼容“1. 研究背景”，
# 避免“5.1%…”回溯成“5”+“.”被误判成一级标题）。
_NUM_HEADING = re.compile(r"^(\d+(?:\.\d+)*)(?=[\s、]|[\u4e00-\u9fff]|[.．](?!\d)|$)")
# Word 样式名标题与“第X章”体的形态都取自结构识别表（见 recognition/structure.py）：
# “标题 1”/“Heading 2”这类样式名、以及章节号后面的量词（章/节/部分/篇）按学院可配。
# 题注长度上限：正文里“图2-1给出了……的实验结果”这类句子开头的“图X-Y”与
# 题注前缀完全同形，仅靠前缀无法区分，用长度 + 句末标点兜底。
_CAPTION_MAX_LEN = 60
# 一级编号后用这些分隔符（而非空格）的是列表序号，不是“第N章”式标题：
# §3.4 列表序号：总项“1．/1、/1.”，分项“（1）(1) 1)”。
# 模板未规定带圈序号（①…），故不把 ① 开头的段落归为列表项，按正文检查。
# 编号后紧跟数字的不算（3.14、5.1% 等正文数字），故对“.”加数字前瞻。
_LIST_ITEM_RE = re.compile(
    r"^(?:[（(]\d+[）)]|\d+[）)]|\d+[．.、](?!\d))"
)
# 一级编号后用这些分隔符（而非空格）的是列表序号，不是“第N章”式标题：
# “1．研究背景”“1、研究背景”“1. 研究背景”都属于列表项。
_LIST_SEPARATORS = ".．、"

# 数字后直接跟中文（无分隔符）时，标题文本长度上限，避免“1.1亿元…”式正文误判。
_NUM_HEADING_MAX_LEN = 40
# “第X章”式标题的文本长度上限。标题一般只有十几个字（“第1章 绪论”），而正文里
# 引述章节的长句远超此值。
_CN_HEADING_MAX_LEN = 30
_SENTENCE_END_RE = re.compile(r"[。！？；]$")
# “数字+正文”起首信号：紧跟编号后的这些字符基本说明是正文句而非标题
# （量词“1.1亿元”、系词/动词“3.14是…”、百分比“5.1%…”等）。
_BODY_START_SIGNALS = "亿万千百十两元角分%％是为占有达比倍约共"

# §2 承诺书标题与封面信息行：写法（“承诺书”结尾、“指导教师/Author”这类字段名）
# 都在结构识别表里，按学院可配（异校叫“学术诚信声明”时改那一份，不动代码）。
# 信息行都很短；长度上限在代码里，避免“作者简介：……”这类正文句被当成封面信息行
# （那会让它绕过正文检查，反而漏检）。
_AUTHOR_INFO_MAX_LEN = 40

# 章节锚点标题：比对前去掉全部空白（含全角空格）并忽略大小写。
# 模板里“摘  要”“目  录”“致 谢”的空格数并不统一，逐字比对会让整块锚点
# 失效——锚点失效不只是这一行判错，后面的区域状态机也会跟着走偏
# （摘要正文、目录条目、致谢正文会被当成正文或参考文献续行）。
_WS_RE = re.compile(r"\s+")

# 标题末尾可能出现、但不是标题文字的标点（“参考文献：”“参考文献。”）
_TITLE_TAIL_PUNCT = "：:。．.、,，;；!！?？ \t"

# w:outlineLvl=9 是 OOXML 的"正文级别"（Word 给正文段落写这个值）。
_OUTLINE_BODY_LEVEL = 9

# "注明'续表'"的几种写法：模板只写"续表"，学生常加括号。
_CONTINUED_NOTE_WORDS = ("续表", "（续表）", "(续表)")


# 封面/扉页信息行：“Author: …”“Supervisor: …”“作者：…”“指导教师：…”。
# 它们排在英文题目之后，若不单独成一种 kind 会被当成正文或关键词内容查字号。
# 注意用“作者”时要能区分承诺书末尾的“…作者签名：”（该行不以“作者”开头）。
# 字段名本身取自结构识别表（front_matter.cover_fields）。


def _style_base_name(style_name):
    """取 WPS 派生样式名的基础样式；不是派生名时返回 None。

    WPS 把"基于某样式再改几项"的派生样式命名成
    `样式 标题 2 + 宋体 小四 行距: 多倍行距 1.3 字行`（基础样式 + " + " + 覆盖项）。
    只按这一种形态剥离，不做模糊匹配。
    """
    name = str(style_name or "").strip()
    if not name.startswith("样式 ") or " + " not in name:
        return None
    return name[len("样式 "):].split(" + ", 1)[0].strip()


def _heading_level_from_derived_style(style_name, spec=None):
    """WPS 派生样式名里的基础样式能定出的标题级别；定不出返回 None。

    这条判据**弱**：派生名只说明"它是照着 标题 2 改的"，实测有文档把三级标题
    （"4.2.6 …"）排在这种样式上，光看名字会抬成二级并报出与事实不符的字号错。
    所以只在正文里没有任何编号可用时才拿它兜底（见 classify_kind 末尾）。
    """
    spec = spec or _DEFAULT_STRUCTURE
    base = _style_base_name(style_name)
    if not base:
        return None
    match = spec.style_heading_re.match(base)
    if not match or not match.group(1):
        return None
    level = int(match.group(1))
    return level if 1 <= level <= 3 else None


def _heading_level_from_style(style_name, outline_lvl, spec=None):
    """由 Word 样式名 / 大纲级别推导标题级别（1~3）；不是标题返回 None。

    - 样式名命中"标题 N"/"Heading N"直接取级别；
    - 样式名是"标题"/"Heading"（无编号）时靠大纲级别兜底，无级别按一级；
    - 仅有大纲级别（w:outlineLvl，0 起）时按 级别+1 推导；`w:outlineLvl=9`
      在 OOXML 里表示"正文级别"，此时一律不是标题。
    这样 Word 自动编号、用样式排版的标题不再依赖正文里的编号文本。
    能认的样式名取自结构识别表（`heading.style_names`），换模板写"章标题"时改表。
    """
    spec = spec or _DEFAULT_STRUCTURE
    if outline_lvl == _OUTLINE_BODY_LEVEL:
        # Word 给正文段落写 w:outlineLvl=9（正文级别）。有样张把成片正文套了
        # "Heading 3"样式且带这个值，若只看样式名会把正文当三级标题漏检。
        return None
    if style_name:
        match = spec.style_heading_re.match(style_name.strip())
        if match:
            if match.group(1):
                level = int(match.group(1))
                return level if 1 <= level <= 3 else None
            if outline_lvl is not None and 0 <= outline_lvl <= 2:
                return outline_lvl + 1
            return 1
    if outline_lvl is not None and 0 <= outline_lvl <= 2:
        return outline_lvl + 1
    return None


def _style_gives_heading_level(style_name, spec):
    """样式名本身是不是标题样式（“标题 1”“Heading 2”…）。"""
    name = str(style_name or "").strip()
    return bool(name) and bool(spec.style_heading_re.match(name))


def _looks_like_prose(value):
    """像一整句话/一整条著录的段落（只有大纲级别一条线索时用它兜底）。"""
    return bool(_SENTENCE_END_RE.search(value)) or len(value) > _CN_HEADING_MAX_LEN


# Word 自动目录的条目样式名固定是 "toc 1"…"toc 9"（中文版 Word 也是这个名字），
# 有的模板/工具写成"目录 1"。这是目录条目的现成结构线索，比"靠点线与页码猜"硬。
_TOC_ENTRY_STYLE_RE = re.compile(r"^(?:toc|目录)\s*\d*$", re.IGNORECASE)


def _toc_entry_style(style_name):
    """样式名是不是目录条目样式（toc 1 / TOC 2 / 目录 1）。"""
    return bool(style_name) and bool(_TOC_ENTRY_STYLE_RE.match(style_name.strip()))


def _is_caption_like_body(value):
    """判定“图X-Y…”开头但其实是正文句（题注只在短句、不带句末标点时成立）。"""
    if len(value) > _CAPTION_MAX_LEN:
        return True
    return bool(_SENTENCE_END_RE.search(value)) and len(value) > _CAPTION_MAX_LEN // 2


def anchor_kind_of(text, spec=None):
    """这段文字是表里配的哪个章节锚点；不是锚点则返回 None。

    归一化口径只此一份：剥掉段末排版说明括注 → 去掉**全部**空白（"目    录"
    "摘  要"这类模板空格写法、以及带换行的写法都要认）→ 查表，忽略大小写；
    带尾标点的写法（"目录："、"参考文献、"）去掉尾标点再查一次，且只在确实命中
    已知锚点时才算，不猜内容。判别"有没有认出锚点"的告警也用这一份，写两套口径
    必然自相矛盾（例如中文"目    录"识别层认得出、告警却说不认识）。
    """
    spec = spec or _DEFAULT_STRUCTURE
    compact = _WS_RE.sub("", strip_trailing_note((text or "").strip()))
    if not compact:
        return None
    anchor = spec.anchor_kinds.get(compact) or spec.anchor_kinds.get(compact.lower())
    if anchor is None:
        trimmed = compact.rstrip(_TITLE_TAIL_PUNCT)
        if trimmed != compact:
            anchor = (spec.anchor_kinds.get(trimmed)
                      or spec.anchor_kinds.get(trimmed.lower()))
    return anchor


def classify_kind(text, style_name=None, outline_lvl=None, structure=None):
    """段落语义类型分类。

    - style_name / outline_lvl（P4）：调用方把 Word 样式名与大纲级别传进来，
      标题识别不再只看正文编号文本；
    - structure：学院的结构识别表（`recognition/structure.py`）；不传时用默认表，
      也就是改造前的行为。
    """
    spec = structure or _DEFAULT_STRUCTURE
    value = strip_trailing_note((text or "").strip())
    if not value:
        return "unknown"
    compact = _WS_RE.sub("", value)
    anchor = anchor_kind_of(value, spec)
    if anchor:
        return anchor
    if len(compact) <= spec.pledge_title_max_len and spec.pledge_title_re.search(compact):
        return "pledge_title"
    if re.match(r"^(?:关键词|关键字)\s*[:：]", value):
        return "keyword_title"
    if re.match(r"^(Key\s*words?|Keywords)\s*[:：]", value, re.IGNORECASE):
        return "en_keyword_title"
    if len(value) <= _AUTHOR_INFO_MAX_LEN and spec.author_info_re.match(value):
        return "author_info"
    if value.startswith(("【注】", "【注意】", "注：")):
        return "note"
    if value in _CONTINUED_NOTE_WORDS or (
        spec.continued_note_re.match(value) and not _is_caption_like_body(value)
    ):
        return "table_continued_note"
    if spec.figure_caption_re.match(value) and not _is_caption_like_body(value):
        return "figure_caption"
    if spec.table_caption_re.match(value) and not _is_caption_like_body(value):
        return "table_caption"
    # 目录条目：Word 自动目录的条目带 "toc 1/2/3" 样式（模板里就有）。
    # 这是现成的结构线索，比"靠点线与页码猜"硬；放在文本编号形态之前判，
    # 否则目录里的"第1章  绪论"会被先认成一级标题。
    if _toc_entry_style(style_name):
        return "toc"
    # 样式/大纲级别判标题（放在精确锚点与图表题之后，避免“摘要”等
    # 用“标题 1”样式排版时被降级成 heading 而破坏 context 区域状态机）
    heading_level = _heading_level_from_style(style_name, outline_lvl, spec)
    if heading_level is not None:
        # 样式名本身不是标题样式、级别只来自 w:outlineLvl 时，要防"残留大纲级别"：
        # 实测学生给参考文献条目用了 List Paragraph + outlineLvl=0，整片条目被当
        # 一级标题查编号/字体/字号（17 段 58 条）。标题都短、不以句末标点结尾，
        # 按这条挡；样式名自己是标题样式时不受影响（真标题仍走样式判据）。
        if not (not _style_gives_heading_level(style_name, spec)
                and _looks_like_prose(value)):
            return {1: "heading_1", 2: "heading_2", 3: "heading_3"}[heading_level]
    if _LIST_ITEM_RE.match(value):
        return "list_item"
    if spec.heading_cn_re.match(value):
        # “第X章”开头的**整句话**不是标题：论文组织结构里常写“第1章为绪论。本章
        # 主要介绍…”，只按前缀判会把整段正文当成一级标题去量字体字号加粗对齐，
        # 并连锁出“目录未收录…（一整句话）”。与 _NUM_HEADING 同口径：以句末标点
        # 结尾、或长到不像标题，就不当标题判。
        if not _SENTENCE_END_RE.search(value) and len(value) <= _CN_HEADING_MAX_LEN:
            return "heading_1"
    match = _NUM_HEADING.match(value)
    if match:
        depth = len(match.group(1).split("."))
        tail = value[match.end():]
        # P4：编号后接“．”“、”“.”（非“第N章”体）的是一级列表序号，如
        # “1．研究背景”。判成 heading_1 会撞上 heading_1 的 “^第..章” 编号
        # 正则，把列表项报成标题编号错误；判成 body 又会被当正文查字号行距。
        if depth == 1 and tail[:1] in _LIST_SEPARATORS:
            return "list_item"
        # 数字后直接跟中文（如“1.1研究背景”）时，用编号形态/量词/长度/
        # 句末标点兜底，避免“3.1415926是圆周率”“1.1亿元的投资…”等
        # 正文开头被误判成标题。
        # 数字后是空格也**不豁免**：“2017 Goole[12]推出了…”“50 Hz工频干扰是…”
        # 这类正文句开头（年号、数值+单位）就是这么被认成章标题的（实测 2 段 35 条）。
        # “1 引言”这类真标题靠下面的长度/句末标点判据照样保住。
        if tail and tail[0] not in ".．、":
            number = match.group(1)
            segments = number.split(".")
            looks_like_heading_number = (
                len(segments) <= 3
                and all(len(seg) <= 2 for seg in segments)
            )
            if (
                not looks_like_heading_number
                or tail[0] in _BODY_START_SIGNALS
                or len(value) > _NUM_HEADING_MAX_LEN
                or _SENTENCE_END_RE.search(value)
            ):
                return "body"
        # P4：论文规则只定义到三级标题（1/1.1/1.1.1）。四级及以上（1.1.1.1）
        # 既不在任何标题规则内，按 body 查会拿正文的宋体/12pt/1.5 倍去报错，
        # 归入 list_item（不参与正文与标题的样式检查）更安全。
        if depth > 3:
            return "list_item"
        return {1: "heading_1", 2: "heading_2", 3: "heading_3"}.get(
            depth, "list_item"
        )
    # 正文里没有任何编号可用时，才拿 WPS 派生样式名的基础样式兜底
    # （"样式 标题 2 + 宋体 小四…"）。有编号就以上面的编号为准：模板是按编号形态
    # 定义各级标题的，派生名只说明"照哪个样式改的"，实测会把"4.2.6 x"这类三级标题
    # 抬成二级（孙天羽 32 条、孙晟 4 条与事实不符的字号错就是这么来的）。
    derived = _heading_level_from_derived_style(style_name, spec)
    if derived is not None:
        return {1: "heading_1", 2: "heading_2", 3: "heading_3"}[derived]
    return "body"
