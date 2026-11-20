"""正文与散块检查：正文、关键词、列表序号、注释、作者信息。"""
import re

from backend.docx_common.notes import strip_trailing_note

from .base import (CJK_RE, HEADING_KINDS, LATIN_RE, TERMINAL_PUNCT, _align_eq)

# 关键词分隔符：规范要求逗号（中文“，”／英文“,”），文档里常写成分号、顿号。
# 计数时按常见分隔符集合切分——只认规范逗号的话，整行会被当成“1 个关键词”，
# 报出“1 个/至少 3 个”这种与事实不符的数量错；用了非规范分隔符时另报一条
# 分隔符问题，两条错各自说清。
_KEYWORD_SEPARATOR_RE = re.compile(r"\s*[，,；;、]\s*")
_KEYWORD_SEPARATOR_CHARS = "，,；;、"

# 关键词标签前缀：中文“关键词/关键字”，英文“Keywords/Key words”。
# 计软模板把关键词内容排在标签之后同一行（不另起一行），这一行既是标签行也是
# 关键词内容行；判定数量与分隔符前必须先剥掉标签，标签本身不是一个关键词。
_KEYWORD_LABEL_RE = re.compile(r"^\s*(?:关键词|关键字|key\s*words?|keywords)\s*[:：]",
                               re.IGNORECASE)

# 标签行之后承载关键词的段落 kind：命中说明关键词在下一行，本行不判数量。
# 英文题目同样紧跟中文关键词之后，但解析层按“居中且字号在题目区间”把它单独标成
# en_title（见 recognition/context.py 的关键词区域分流），所以这里认 kind 就够。
_KEYWORD_CONTENT_KINDS = {"keyword": "keyword_body", "en_keyword": "en_keyword_body"}

# 段落中间出现的“关键词/关键字/Keywords：”。关键词标题本该单独一行，出现在别的
# 内容之后（摘要正文末尾）就是没另起一行——只认“以关键词开头”的段落会整条放过。
_EMBEDDED_KEYWORD_LABEL_RE = re.compile(
    r"(?:关键词|关键字|key\s*words?|keywords)\s*[:：]", re.IGNORECASE)

# 句末标点：冒号之后还有句子的，是正文里提到“关键词”三个字，不是关键词清单。
_SENTENCE_END_CHARS = "。；！？;!?"

# 正文里的半角标点（中文语境）。只收能零误报的类别：逗号分号冒号叹号问号、
# 两侧都是中文的句号（避开小数 “3.14” 与条目编号 “1.商品”）、省略号、双引号、
# 圆括号。方括号（正文里多是引用序号 [1]）、破折号、书名号的宽度不判，
# 在 checker_service 的“未检”清单里说明。
_CJK_RANGE = "\u4e00-\u9fff"
_HALFWIDTH_PUNCT_PATTERNS = (
    rf"[{_CJK_RANGE}][,;:!?]|[,;:!?][{_CJK_RANGE}]",
    rf"[{_CJK_RANGE}]\.[{_CJK_RANGE}]",
    rf"[{_CJK_RANGE}]\.{{3}}|\.{{3}}[{_CJK_RANGE}]",
    rf"[{_CJK_RANGE}]\x22|\x22[{_CJK_RANGE}]",
    rf"[{_CJK_RANGE}]\(|\)[{_CJK_RANGE}]",
)

# 关键词标签前面应当是个“断句处”：句末标点、逗号顿号，或者段内换行。
# 正文里正常提到“关键词”三个字时，它前面通常是词（“使用关键词”“用关键词”），
# 这一条能把这类句子挡掉——模板要求的是关键词标题另起一行，不是文中不许出现
# 这三个字。（“摘要正文，关键词：…”“摘要正文。关键词：…”两种粘法都能报出来。）
_KEYWORD_LABEL_BOUNDARY = "。！？!?.，,；;、"

# 单个关键词都很短；出现这种长度的“词”说明冒号后接的是正文句子。
_KEYWORD_TERM_MAX_LEN = 24


def _keyword_content_follows(ctx, english):
    """标签行之后是否还有承载关键词的段落（只看紧接着的那个非空段落）。"""
    if ctx is None:
        return True  # 没有上下文时保持旧行为：不拿标签行判数量
    expected = _KEYWORD_CONTENT_KINDS["en_keyword" if english else "keyword"]
    for block in ctx.blocks[ctx.index + 1:]:
        if block.get("type") != "paragraph" or not (block.get("text") or "").strip():
            continue
        return block.get("kind") == expected
    return False

# 冒号形态：模板写哪种就要求哪种（U+FF1A 全角 / U+003A 半角）。
_COLON_NAMES = {"\uff1a": "全角", ":": "半角"}

# 注释“同与之对应的标题对齐”时的对齐基准块。基准只取标题：模板说明（“5．注释”
# 第 1 条）写的是“同与之对应的标题对齐”，图表题注不在其列——把题注当基准，会把
# “表题居中 + 其下顶格注释”这种合规写法判成不合规。
FOLLOW_KINDS = HEADING_KINDS


def colon_name(char):
    """把冒号说成人话，用于报错原因。"""
    return _COLON_NAMES.get(char, "未知")


class ParagraphsMixin:
    """正文 / 关键词 / 列表 / 注释 / 作者。"""

    # ====================== 正文 ======================
    def _check_body(self, b, ctx):
        rule = self.rules.get("body", {})
        loc = self._loc(b)
        size_tol = rule.get("font_size_tolerance_pt", 0.5)
        indent_tol = rule.get("indent_tolerance_pt", 1.0)
        spacing_tol = rule.get("spacing_tolerance", 0.05)

        for i, run in enumerate(b.get("runs", [])):
            rloc = {**loc, "run": i}
            # 纯空白 run（Word 删字后残留、或用来占位的空格/制表符）不渲染任何可见
            # 字符，它的字号字体不影响排版。base.py 的 _check_run_fonts 一直是这么
            # 处理的，这里原先漏了：实测 79 条“正文字号不符合要求”打在只有一个
            # 空格的 run 上，学生看不到、也无从修改。
            if not (run.get("text") or "").strip():
                continue
            size = run.get("font_size_pt")
            expected_size = rule.get("font_size_pt")
            if not self._close(size, expected_size, size_tol):
                self._issue("body.font_size", "正文格式", "error", rloc,
                            f"{size} pt", f"{expected_size} pt", "正文字号不符合要求",
                            f"请将字号修改为 {expected_size} 磅")

            text = run.get("text") or ""
            if CJK_RE.search(text):
                actual = run.get("east_asia_font")
                expected = rule.get("east_asia_font")
                if not self._font_eq(actual, expected):
                    self._issue("body.east_asia_font", "正文格式", "error", rloc,
                                actual, expected, "正文中文字体不符合要求",
                                f"请将中文字体修改为 {expected}")
            if LATIN_RE.search(text):
                actual = run.get("font_name")
                expected = rule.get("latin_font")
                if not self._font_eq(actual, expected):
                    self._issue("body.latin_font", "正文格式", "error", rloc,
                                actual, expected, "正文西文/数字字体不符合要求",
                                f"请将西文字体修改为 {expected}")

            spacing = run.get("char_spacing_pt")
            expected_spacing = rule.get("char_spacing_pt", 0)
            if spacing is not None and not self._close(spacing, expected_spacing, 0.5):
                self._issue("body.char_spacing", "正文格式", "warning", rloc,
                            f"{spacing} pt", f"{expected_spacing} pt", "字符间距不符合要求（应为标准间距）")

        align = b.get("alignment")
        expected_align = rule.get("alignment")
        if not _align_eq(align, expected_align):
            self._issue("body.alignment", "正文格式", "error", loc,
                        align, expected_align, "正文对齐方式不符合要求")

        indent = b.get("first_line_indent_pt")
        expected_indent = rule.get("first_line_indent_pt")
        if not self._close(indent, expected_indent, indent_tol):
            self._issue("body.first_line_indent", "正文格式", "error", loc,
                        f"{indent} pt", f"{expected_indent} pt", "正文首行缩进不符合要求")

        fixed = b.get("line_spacing_pt")
        multiple = b.get("line_spacing_multiple")
        expected_multiple = rule.get("line_spacing_multiple")
        if fixed:
            self._issue("body.line_spacing", "正文格式", "error", loc,
                        f"固定行距 {fixed} pt", f"{expected_multiple} 倍",
                        "正文行距不符合要求（应为倍数行距）")
        elif not self._close(multiple, expected_multiple, spacing_tol):
            self._issue("body.line_spacing", "正文格式", "error", loc,
                        f"{multiple} 倍", f"{expected_multiple} 倍", "正文行距不符合要求")

        # 中文语境下的半角标点提示（全角标点规则 4.2）
        if rule.get("check_fullwidth_punctuation"):
            text = b.get("text") or ""
            for pattern in _HALFWIDTH_PUNCT_PATTERNS:
                match = re.search(pattern, text)
                if match:
                    self._issue("body.fullwidth_punct", "正文格式", "warning", loc,
                                match.group(), "全角标点", "中文语境下应使用全角标点")
                    break

    # ====================== 关键词 ======================
    def _extra_keyword_count(self, b, ctx):
        self._check_keyword_count(b, english=False, ctx=ctx)

    def _extra_keyword_title(self, b, ctx):
        """§2.3/2.4：关键词标签行。

        计软模板把关键词内容排在标签之后同一行（不另起一行），这一行同时承担
        “标签行”与“关键词内容行”两个角色：标签用词、冒号照查，同一行里的关键词
        数量、分隔符、末尾标点也要照查。只看标签的话，官方模板的排版会让关键词
        检查整条失效——只写 1 个关键词、用顿号分隔、末尾加句号都报不出来。
        """
        self._extra_keyword_label(b, ctx)
        self._check_keyword_count(b, english=False, ctx=ctx)
        self._check_keyword_layout(b, ctx, english=False)

    def _extra_en_keyword_title(self, b, ctx):
        """§2.7：Keywords 标签行（计软的英文关键词内容同样与标签同行）。"""
        self._extra_en_keyword_label(b, ctx)
        self._check_keyword_count(b, english=True, ctx=ctx)
        self._check_keyword_layout(b, ctx, english=True)

    def _check_keyword_layout(self, b, ctx, english=False):
        """§2.3/2.4、§2.7：关键词标题行与摘要正文之间是否空一行。

        只有软院模板写了“与摘要正文相隔一行”（rules 的 ``<标题规则>.blank_line_before``）；
        计算机模板没有任何关于关键词排布的说明，因此计软这一侧不查。
        “空一行”在 Word 里可能是段前间距（w:before），也可能就是一个空段落，所以判据
        取宽松的一侧：只要上方有间隔（段前间距 > 0 磅，或上一段是空段落）就算满足，
        紧贴上一段才报——两种做法都不会被冤枉。
        """
        if english:
            title_rule = (self.rules.get("english_abstract") or {}).get(
                "keyword_title") or {}
            prefix, label = "en_keyword", "Keywords标题"
        else:
            title_rule = (self.rules.get("keywords") or {}).get("title") or {}
            prefix, label = "keyword", "关键词标题"

        if title_rule.get("blank_line_before"):
            if (b.get("space_before_pt") or 0) > 0:
                return
            prev = ctx.prev if ctx is not None else None
            # 上方本来就空着（文档开头）或上一段就是个空段落，都算“留出了空行”。
            if prev is None or not (prev.get("text") or "").strip():
                return
            self._issue(f"{prefix}.space_before", "关键词", "warning", self._loc(b),
                        "紧贴上一段", "上方空一行", f"{label}上方应空一行",
                        suggestion=f"在{label}上方留出一个空行（或给该段设置段前间距）")

    def _extra_embedded_keyword(self, b, ctx):
        """§2.2：中文关键词标题须单独一行（模板：“黑体4号字，居左，单独一行”）。"""
        self._check_embedded_keyword(b, english=False)

    def _extra_embedded_keyword_en(self, b, ctx):
        """§2.4：英文关键词标题须单独一行（模板同款说明）。"""
        self._check_embedded_keyword(b, english=True)

    def _check_embedded_keyword(self, b, english=False):
        """§2.2/§2.4：关键词标题与摘要正文挤在同一段时报出来。

        模板写的是“单独一行”。识别关键词只认“这一段以关键词标签开头”，于是
        “……实验证明该方法有效。关键词：机器学习，癌症检测”这种写法整段被判成摘要
        正文——关键词的标签、数量、分隔符、字体一条都不会查。这里反向补一条：
        摘要正文里又出现关键词标签，就是关键词标题没单独一行。

        四条护栏避免冤枉正文里正常提到“关键词”的句子：标签要出现在断句处之后
        （前面不能压着“使用”“根据”这类词，段内软换行算已经另起一行）、冒号后
        不能有句末标点、冒号后要真的是关键词清单（够模板要求的关键词个数，且每个
        词都很短）。
        """
        rule = (self.rules.get("english_abstract") or {}) if english \
            else (self.rules.get("keywords") or {})
        if english:
            prefix, label, category = "en_keyword", "英文关键词标题", "英文关键词"
            title_rule = rule.get("keyword_title") or {}
            minimum = rule.get("keyword_min_count")
        else:
            prefix, label, category = "keyword", "关键词标题", "关键词"
            title_rule = rule.get("title") or {}
            minimum = rule.get("min_count")
        if not title_rule.get("own_line"):
            return  # 本院模板没写“单独一行”就不查
        text = (b.get("text") or "").strip()
        match = _EMBEDDED_KEYWORD_LABEL_RE.search(text)
        if match is None or match.start() == 0:
            return
        if not text[:match.start()].rsplit("\n", 1)[-1].strip():
            return  # 段落里软换行之后另起的一行，视觉上已经单独一行
        if text[:match.start()].rstrip()[-1:] not in _KEYWORD_LABEL_BOUNDARY:
            return  # 标签前还压着别的字（“使用关键词：…”），是正文在说关键词
        tail = strip_trailing_note(text[match.end():]).strip()
        # 末尾的句号不影响判断（那是另一条“末尾无标点”的事），句中出现句末标点
        # 说明冒号后接的是句子，不是关键词清单。
        if any(punct in tail[:-1] for punct in _SENTENCE_END_CHARS):
            return
        terms = [term.strip() for term in _KEYWORD_SEPARATOR_RE.split(tail)
                 if term.strip()]
        if len(terms) < (minimum or 3):
            return
        if any(len(term) > _KEYWORD_TERM_MAX_LEN for term in terms):
            return
        self._issue(f"{prefix}.title.own_line", category, "error", self._loc(b),
                    (match.group() + tail)[:40], "单独一行",
                    f"{label}未单独一行（与摘要正文排在同一段）",
                    suggestion=f"把“{match.group()}…”另起一段，让{label}单独占一行")

    def _extra_keyword_label(self, b, ctx):
        """§2.3：中文关键词标签行（标签词 + 冒号）须与本院模板逐字符一致。

        两院官方模板用词不同：计算机模板写“关键字：”，软件模板写“关键词：”。
        取值以 rules 的 keywords.label / label_colon 为准，写成另一种学院的写法、
        冒号换成半角，都是与本院模板不一致，报出来（不牵连字体字号）。
        """
        rule = self.rules.get("keywords") or {}
        self._report_keyword_label(
            b, rule.get("label"), rule.get("label_colon"),
            "keyword.title_label", "关键词标签", "关键词")

    def _extra_en_keyword_label(self, b, ctx):
        """§2.7：英文关键词标签行须与模板一致。

        模板原文：计算机模板是“Keywords：”（全角冒号），软件模板是“Keywords:”
        （半角冒号）；写成“Keyword”“Key words”或换错冒号形态都与模板不一致。
        """
        rule = self.rules.get("english_abstract") or {}
        self._report_keyword_label(
            b, rule.get("keyword_label"), rule.get("keyword_colon"),
            "en_keyword.title_label", "英文关键词标签", "英文关键词")

    def _report_keyword_label(self, b, expected, colon, rule_id, label, category):
        """标签行＝标签词 + 冒号，两段都要与模板逐字符一致；没配 expected 就不查。"""
        match = re.match(r"^([^：:]{1,30}?)(\s*)([:：])", (b.get("text") or "").strip())
        if not expected or not match:
            return
        word, gap, actual_colon = match.groups()
        if word == expected and not gap and (not colon or actual_colon == colon):
            return
        if word != expected:
            detail = f"{label}应写作“{expected}”"
        elif gap:
            detail = f"{label}与{colon_name(actual_colon)}冒号之间多了空白，模板是紧挨着的"
        else:
            detail = (f"{label}的冒号应为{colon_name(colon)}冒号"
                      f"（模板原文“{expected}{colon}”），当前用的是{colon_name(actual_colon)}冒号")
        self._issue(rule_id, category, "error", self._loc(b),
                    f"{word}{gap}{actual_colon}", f"{expected}{colon or actual_colon}",
                    detail,
                    suggestion=f"模板该处的标签写作“{expected}{colon or actual_colon}”，"
                               "请逐字符照着改")

    def _extra_en_keyword_count(self, b, ctx):
        self._check_keyword_count(b, english=True, ctx=ctx)

    def _check_keyword_count(self, b, english=False, ctx=None):
        if english:
            rule = self.rules.get("english_abstract", {})
            label, prefix = "英文关键词", "en_keyword"
            sep = rule.get("keyword_separator", ",")
            # 模板原句只写“逗号间隔”时（计算机学院 §2.4），全角“，”与半角“,”都是
            # 逗号，不判宽窄；软件学院写的是“英文逗号”（§2.5），只认半角。
            ascii_only = rule.get("keyword_separator_ascii_only", False)
            min_c = rule.get("keyword_min_count")
            max_c = rule.get("keyword_max_count")
            forbid = rule.get("keyword_forbid_terminal_punctuation")
        else:
            rule = self.rules.get("keywords", {})
            label, prefix = "关键词", "keyword"
            sep = rule.get("separator", "，")
            # 两院模板都写“关键词分隔：逗号间隔”，没有“英文/全角”限定：
            # 全角“，”与半角“,”都是逗号，不判宽窄。
            ascii_only = False
            min_c = rule.get("min_count")
            max_c = rule.get("max_count")
            forbid = rule.get("forbid_terminal_punctuation")
        loc = self._loc(b)
        # 先剥掉段末排版说明括注：模板样例行“关键词1，关键词2，关键词3（宋体小四
        # 号字，1.5倍行距，3-5个关键词，逗号间隔，末尾无标点）”里的说明文字本身
        # 就带逗号，不剥掉会把关键词数拆成 7 个，报“数量超出”这种与事实不符的错。
        text = strip_trailing_note(b.get("text") or "")
        # 标签与关键词同排一行时（计软模板的排版，见 _extra_keyword_title）先剥掉
        # 标签：标签本身不是关键词。剥掉后只剩分隔符与空白（模板样例行的空槽
        # “关键字： ，  ，  ”）或空无一物，就说明这一行没写关键词：
        #   · 下一段是承载关键词的段落（另起一行的排版）→ 本行不判数量，交给它判；
        #   · 下一段是别的（直接跳到 Abstract/正文）→ 这里就是空着没写，按 0 个报。
        without_label = _KEYWORD_LABEL_RE.sub("", text, count=1)
        if without_label != text:
            if _KEYWORD_SEPARATOR_RE.sub("", without_label).strip():
                text = without_label.strip()
            elif _keyword_content_follows(ctx, english):
                return
            else:
                text = ""
        parts = [p.strip() for p in _KEYWORD_SEPARATOR_RE.split(text) if p.strip()]
        has_separator = any(ch in text for ch in _KEYWORD_SEPARATOR_CHARS)
        tokens = [p for p in re.split(r"\s+", text.strip()) if p]
        if english and not has_separator and len(tokens) >= 2:
            # 一个分隔符都没有、只有空格：模板要求“逗号间隔”，没逗号是文件里可
            # 直接看见的事实，照报；但“几个关键词”数不准（machine learning 是
            # 一个还是两个？），数量不能猜，否则会报出“1 个”这种假数。
            expected_sep = f"英文逗号“{sep}”间隔" if ascii_only else f"逗号“{sep}”间隔"
            self._issue(f"{prefix}.separator", label, "error", loc,
                        "未使用逗号分隔", expected_sep,
                        f"{label}未用逗号分隔",
                        f"按模板「{label}分隔：{expected_sep}」，用逗号分隔各关键词")
            min_c = max_c = None
        allowed = {sep}
        if not ascii_only:
            allowed.update(("，", ","))
        wrong = [ch for ch in dict.fromkeys(text)
                 if ch in _KEYWORD_SEPARATOR_CHARS and ch not in allowed]
        if wrong:
            if ascii_only:
                expected, suggestion = sep, f"请改用“{sep}”分隔{label}"
            else:
                expected = "逗号"
                suggestion = f"请用逗号分隔{label}（全角“，”或半角“,”均可）"
            self._issue(f"{prefix}.separator", label, "error", loc,
                        "、".join(wrong), expected,
                        f"{label}分隔符不符合要求", suggestion)
        if min_c is not None and len(parts) < min_c:
            self._issue(f"{prefix}.count", label, "error", loc,
                        f"{len(parts)} 个", f"至少 {min_c} 个", f"{label}数量不足")
        if max_c is not None and len(parts) > max_c:
            self._issue(f"{prefix}.count", label, "error", loc,
                        f"{len(parts)} 个", f"最多 {max_c} 个", f"{label}数量超出")
        if forbid and text and text.rstrip()[-1] in TERMINAL_PUNCT:
            self._issue(f"{prefix}.terminal_punct", label, "error", loc,
                        text.rstrip()[-1], "无末尾标点", f"{label}末尾不应有标点")

    # ====================== 列表序号 ======================
    def _check_list_item(self, b, ctx):
        """§3.4：列表序号格式。

        总项用“1．2．”，分项用“（1）（2）”或数字加括号，序号括注后不加其他标点。
        与语义层 list.numbering 保持同一套判定口径：这里的段落因
        P4 分类为 list_item 已不再进入语义层的 body 扫描，故在此补齐。
        """
        rule = self.rules.get("list_item")
        if not rule:
            return
        loc = self._loc(b)
        text = (b.get("text") or "").strip()
        if not text:
            return
        sub_pattern = rule.get("sub_pattern")
        sub = re.match(sub_pattern, text) if sub_pattern else None
        if sub:
            rest = text[sub.end():].lstrip()
            punct = rule.get("forbid_punct_after_marker") or ""
            if rest and rest[0] in punct:
                self._issue("list_item.marker_punct", "正文", "warning", loc,
                            text[:20], "序号括注后直接接内容",
                            "列表序号括注之后不应再加其他标点")
            return
        # 数字加顿号/逗号/半角点等：分隔符不是“．”
        total_pattern = rule.get("total_marker_pattern")
        total = re.match(total_pattern, text) if total_pattern else None
        if not total:
            return
        required = rule.get("required_total_marker") or "．"
        marker = (total.group(1) if total.groups() else "").strip()
        if not marker or marker == required:
            return
        # 半角分隔符只在中文语境下判错，避免英文摘要里“1. Introduction”
        # 这类英文编号被误报。
        if marker in ".," and not CJK_RE.search(text):
            return
        head = re.match(r"^\d{1,2}", text)
        prefix = head.group(0) if head else ""
        self._issue("list_item.number_format", "正文", "warning", loc,
                    text[:20], f"{prefix}{required}",
                    f"列表总项序号后应使用“{required}”，不用“{marker}”")

    # ====================== 作者信息 ======================
    def _check_author_info(self, b, ctx):
        """作者、导师信息：只有规范原文写了这条的学院才检查（见 rules 的 author_info）。

        规则里声明了对齐、字号、西文字体中的哪几项就查哪几项（如软件学院
        要求“Times New Roman 小三号，居右”，则三者都查）。
        """
        rule = self.rules.get("author_info") or {}
        expected = rule.get("alignment")
        if not expected:
            return
        loc = self._loc(b)
        if b.get("alignment") and b["alignment"] != expected:
            self._issue("author_info.alignment", "英文摘要", "error", loc,
                        b["alignment"], expected, "作者、导师信息应居右")
        # 字号与西文字体：只有 rules 里显式写了才检查，避免给未规范的学院引入误报。
        self._check_run_fonts(b, "英文摘要",
                              latin_font=rule.get("latin_font"),
                              size_pt=rule.get("font_size_pt"),
                              size_tol=0.5, loc=loc,
                              font_rule_id="author_info.latin_font",
                              size_rule_id="author_info.font_size")

    # ====================== 注释 ======================
    def _check_note(self, b, ctx):
        rule = self.rules.get("notes", {})
        loc = self._loc(b)
        text = (b.get("text") or "").strip()
        prefixes = rule.get("prefix", [])
        if prefixes and text and not any(text.startswith(p) for p in prefixes):
            self._issue("note.prefix", "注释", "warning", loc,
                        text[:20], "/".join(prefixes), "注释应以【注】或【注意】开头")
        # 注释块由“注意标志”与“内容”组成，字号可能不同（CS：标志黑体小四、内容
        # 小四；SE：标志黑体小四、内容四号）。模板只对**注意标志**提了字体要求
        # （“注意标志采用黑体小四号汉字”），内容字体没有规定，所以字体只查标志那
        # 几个字符的范围；字号按两者允许值查。
        marker = next((prefix for prefix in prefixes if text.startswith(prefix)), None)
        self._check_run_fonts(b, "注释", cjk_font=rule.get("font_name"),
                              size_pt=None, size_tol=0.5, loc=loc,
                              font_rule_id="note.font",
                              until=len(marker) if marker else None)
        allowed_sizes = [size for size in (rule.get("font_size_pt"),
                                           rule.get("content_font_size_pt"))
                         if size is not None]
        if allowed_sizes:
            for index, run in enumerate(b.get("runs", [])):
                size = run.get("font_size_pt")
                if size is None or any(self._close(size, want, 0.5)
                                       for want in allowed_sizes):
                    continue
                self._issue("note.font_size", "注释", "warning", {**loc, "run": index},
                            f"{size} pt",
                            " / ".join(f"{want} pt" for want in allowed_sizes),
                            "注释字号不符合要求")
        # 模板「注意：小4号字，1.5倍行间距」（软院为四号字）：注释行距。
        expected_multiple = rule.get("line_spacing_multiple")
        if expected_multiple is not None and b.get("line_spacing_pt") is None \
                and not self._close(b.get("line_spacing_multiple"),
                                    expected_multiple, 0.05):
            self._issue("note.line_spacing", "注释", "warning", loc,
                        f'{b.get("line_spacing_multiple")} 倍',
                        f"{expected_multiple} 倍", "注释行距不符合要求")
        if rule.get("space_before_pt") is not None and b.get("space_before_pt") is not None \
                and not self._close(b["space_before_pt"], rule["space_before_pt"], 1.0):
            self._issue("note.space_before", "注释", "warning", loc,
                        f'{b["space_before_pt"]} pt', f'{rule["space_before_pt"]} pt',
                        "注释段前间距不符合要求")
        # 模板说明“5．注释”第 1 条：注意标志同与之对应的标题对齐。基准只取标题
        # （见 FOLLOW_KINDS）；左对齐与两端对齐由 _align_eq 视为同一种，单行注释
        # 在这两种对齐下视觉相同，只报明确不同的情形。
        if rule.get("alignment") == "follow_heading":
            expected_align = self._follow_alignment(ctx.blocks, ctx.index)
            actual_align = b.get("alignment")
            if not _align_eq(actual_align, expected_align):
                self._issue("note.alignment", "注释", "warning", loc,
                            actual_align, expected_align, "注释应与对应标题对齐")
        # §4.6 原文只有一句「注释起排：另起一行，用【注】开头」——它说的是"另起一行"，
        # **没有**说"首行不缩进"（模板全文没有"顶格"二字，只有参考文献序号那处的
        # "左顶格"）。所以"首行缩进"这条没有判据，config/rules 的 notes 里不放
        # first_line_indent_pt，本段判定因此不生效（见 test_defaults_consistency 里
        # 的 test_rules_do_not_enable_checks_without_a_template_sentence）。
        indent = b.get("first_line_indent_pt")
        if rule.get("first_line_indent_pt") is not None and indent \
                and not self._close(indent, rule["first_line_indent_pt"],
                                    rule.get("indent_tolerance_pt", 1.0)):
            self._issue("note.first_line_indent", "注释", "warning", loc,
                        f"{indent} pt", f'{rule["first_line_indent_pt"]} pt',
                        "注释首行缩进不符合要求")
        # §4.6 第 4 条：只有一条注意事项时紧跟在注意标志之后书写，多条才换行用
        # 编号序列。仅在“标志后没有内容、紧随一段正文、其后不再有注释段”时提示，
        # 以免把多条注释的编号序列误判。
        if rule.get("single_item_inline"):
            marker = text.rstrip("：:") or text
            if any(marker == p.rstrip("：:") for p in prefixes) \
                    and (ctx.nxt or {}).get("kind") == "body" \
                    and not any((blk or {}).get("kind") == "note"
                                for blk in ctx.blocks[ctx.index + 1:]):
                self._issue("note.single_item_inline", "注释", "warning", loc,
                            text, f"{text}注意事项内容",
                            "只有一条注意事项时应紧跟在注意标志之后书写")

    def _follow_alignment(self, blocks, index):
        """往前找到最近的标题，取其对齐方式作为注释的对齐基准。"""
        for pos in range(index - 1, -1, -1):
            block = blocks[pos] or {}
            if block.get("kind") in FOLLOW_KINDS and block.get("alignment"):
                return block["alignment"]
        return None
