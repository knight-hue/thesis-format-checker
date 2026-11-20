# -*- coding: utf-8 -*-
"""结构识别表：把"文档里的这一段是什么"从代码里搬到每学院一份配置。

分工要说清楚：

- `config/rules/<学校>/<学院>/<college_id>_rules.json` 管**要求是什么**（字号该多大、行距该多少）；
- 本模块的**结构识别表**管**这一段是什么**（这是摘要标题？参考文献条目？图题？）。

改造前，后者全是模块级常量：两院共用一份、改一所学校会碰到另一所、异校写法认不出
时系统一声不响。现在拆成"默认表（= 改造前的行为，也就是吉林大学两版模板的写法）+
每学院一份结构识别表覆盖"，只写要改的键，未写的用默认值。

**外置的是词表**（章节锚点、承诺书/声明页、封面字段、题注前缀、条目序号括号）；
**形态类判据仍在代码里**：编号数字形态（`\\d+(?:\\.\\d+)*`）、编号分隔符集合、
目录点线与页码形态、以及"整句话不是标题"这类句子护栏——它们是解析逻辑而不是措辞，
外置成字符串只会让写错时更难查。

配置校验是**严格**的：出现未知分组/未知键、锚点指向不存在的 kind、正则编译不过，
一律在加载时抛错并列出可用键——避免"配置写了却没生效"这种最难查的静默失效。

合并语义：**按分组覆盖，分组内的列表/字符集是整组替换**（不是追加）。所以只想加一个
写法时，要在自己的表里把原有取值一起写上（默认表见 `DEFAULT_STRUCTURE`）。
`_` 开头的键当作注释忽略。
"""
import json
import os
import re

from backend.college_registry import COLLEGES_CONFIG_PATH, RULES_ROOT, path_of

# 锚点表能指向的 kind：写错（如 "abstract" 少了 _title）要在加载时就被拦住，
# 否则那一段会静默地不被识别成锚点，区域状态机跟着错位。
ANCHOR_KINDS = frozenset({
    "title", "en_title", "toc", "abstract_title", "en_abstract_title",
    "keyword_title", "en_keyword_title", "reference_title", "acknowledgement_title",
})

# 默认结构识别表：值 = 改造前代码里的常量（吉林大学两版模板的写法）。
# 两院的表目前就是它的副本；改学校只改那一份。
DEFAULT_STRUCTURE = {
    "schema_version": "1.0",
    # 章节锚点：把写法归一化（去空白、忽略大小写）后查这张表。规范里"关键词"与
    # 个别模板的"关键字"都要认——锚点判错不只是这一行的问题，区域状态机会跟着走偏。
    "section_anchors": {
        "中文题目": "title",
        "论文题目": "title",
        "英文题目": "en_title",
        "目录": "toc",
        "contents": "toc",
        "tableofcontents": "toc",
        "摘要": "abstract_title",
        # 学生常把"摘要"写成"中文摘要"（与"关键字/关键词"同类）。不认它这一行就落进
        # 正文，按正文字号逐条报错；认出来才按"摘要标题"的正确要求判。
        "中文摘要": "abstract_title",
        "abstract": "en_abstract_title",
        "关键词": "keyword_title",
        "关键字": "keyword_title",
        "keyword": "en_keyword_title",
        "keywords": "en_keyword_title",
        "参考文献": "reference_title",
        "references": "reference_title",
        "致谢": "acknowledgement_title",
        "acknowledgement": "acknowledgement_title",
        "acknowledgements": "acknowledgement_title",
    },
    # 标题：Word 样式名（"标题 1"/"Heading 2"）与"第X章"体（章号形态本身仍在代码里）
    "heading": {
        "style_names": ["标题", "heading"],
        "chapter_prefix": "第",
        "chapter_units": "章节部分篇",
    },
    # 前置部分：免检的承诺书/声明页 + 封面字段行
    "front_matter": {
        "pledge_title_suffixes": ["承诺书"],
        "pledge_body_keywords": ["承诺", "签名"],
        "cover_fields": ["author", "supervisor", "指导教师", "导师", "作者"],
    },
    # 题注：图/表/续表前缀（数字与编号分隔符的形态仍在代码里）
    "captions": {
        "figure_prefixes": ["图"],
        # "表格 3.1…""图表 2-1…"也是学生写题注的常见写法。认出来才能报出真正的问题
        # （编号应为"表3-1"），否则整行按正文查字号，报的错与事实不符。
        "table_prefixes": ["表", "表格", "图表"],
        "continued_prefixes": ["续表"],
        "number_separators": "-－",
    },
    # 参考文献条目的序号括号（"[1]" / "［1］"）
    "references": {
        "entry_number_open": "[［",
        "entry_number_close": "]］",
    },
}

# 各分组允许的键：多写一个键会被拦下（防"配置写了却没生效"）。
_GROUP_KEYS = {
    "section_anchors": None,          # 自由映射：写法 → kind
    "heading": ("style_names", "chapter_prefix", "chapter_units"),
    "front_matter": ("pledge_title_suffixes", "pledge_body_keywords", "cover_fields"),
    "captions": ("figure_prefixes", "table_prefixes", "continued_prefixes",
                 "number_separators"),
    "references": ("entry_number_open", "entry_number_close"),
}

# 题目/摘要这类固定标题的长度上限：属于解析逻辑，不外置。
_PLEDGE_TITLE_MAX_LEN = 30


def _word_alternation(words):
    """把词表拼成正则分支（转义后拼接，空表报错——空表会让整条判据静默失效）。"""
    cleaned = [re.escape(str(word)) for word in words if str(word).strip()]
    if not cleaned:
        raise ValueError("结构识别表里出现空词表（会让对应判据静默失效）")
    return "(?:" + "|".join(cleaned) + ")"


def _char_class(chars):
    """把"字符集合"（如 "[［"、"-－"）拼成正则字符类的内容。

    逐字符转义：不转义的话 "[［" 会拼出嵌套字符集 `[[［]`，正则能跑但语义可疑
    （Python 会抛 FutureWarning）。
    """
    if not str(chars).strip():
        raise ValueError("结构识别表里出现空字符集（会让对应判据静默失效）")
    return "".join("\\" + ch if ch in "[]\\^-" else ch for ch in str(chars))


class StructureSpec:
    """编译好的结构识别规则：正则与词表，供解析层直接使用。"""

    def __init__(self, config):
        self.config = config
        anchors = config["section_anchors"]
        bad = sorted({kind for kind in anchors.values() if kind not in ANCHOR_KINDS})
        if bad:
            raise ValueError(
                f"section_anchors 指向了未知 kind：{'、'.join(bad)}；"
                f"可用值：{'、'.join(sorted(ANCHOR_KINDS))}")
        self.anchor_kinds = dict(anchors)

        heading = config["heading"]
        self.style_heading_re = re.compile(
            rf"^(?:{_word_alternation(heading['style_names'])})\s*(\d+)?$", re.IGNORECASE)
        self.heading_cn_re = re.compile(
            rf"^{re.escape(heading['chapter_prefix'])}"
            rf"[一二三四五六七八九十百零\d]+[{heading['chapter_units']}]")

        front = config["front_matter"]
        self.pledge_title_re = re.compile(
            _word_alternation(front["pledge_title_suffixes"]) + "$")
        # 承诺正文/署名行/日期行：日期形态（"2020年5月20日"）是通用写法，留在代码里。
        self.pledge_content_re = re.compile(
            _word_alternation(front["pledge_body_keywords"]) + r"|年\s*\d{1,2}\s*月")
        self.author_info_re = re.compile(
            rf"^{_word_alternation(front['cover_fields'])}\s*[:：]", re.IGNORECASE)

        captions = config["captions"]
        numerals = r"[\d一二三四五六七八九十]"
        self.figure_caption_re = re.compile(
            rf"^{_word_alternation(captions['figure_prefixes'])}\s*{numerals}")
        self.table_caption_re = re.compile(
            rf"^{_word_alternation(captions['table_prefixes'])}\s*{numerals}")
        self.continued_note_re = re.compile(
            rf"^{_word_alternation(captions['continued_prefixes'])}\s*[（(]?\s*\d+\s*"
            rf"[{_char_class(captions['number_separators'])}]\s*\d+")

        references = config["references"]
        self.ref_entry_number_re = re.compile(
            rf"^[{_char_class(references['entry_number_open'])}]\s*\d+\s*"
            rf"[{_char_class(references['entry_number_close'])}]")

    # 解析层用的小工具：这些判据在多处用到，集中在这里，避免各自拼正则。
    @property
    def pledge_title_max_len(self):
        return _PLEDGE_TITLE_MAX_LEN


def _merge(base, overrides, where=""):
    """按分组浅合并：只覆盖写了的分组里的写了 的键，未写的保持默认。"""
    merged = {key: (dict(value) if isinstance(value, dict) else value)
              for key, value in base.items()}
    for group, values in overrides.items():
        if group == "schema_version" or group == "college_id" or group.startswith("_"):
            continue  # "_note" 这类下划线键是给人看的注释，不参与合并
        allowed = _GROUP_KEYS.get(group)
        if allowed is None and group != "section_anchors":
            raise ValueError(
                f"{where}结构识别表出现未知分组：“{group}”；"
                f"可用分组：{'、'.join(sorted(k for k in _GROUP_KEYS))}")
        if group == "section_anchors":
            if not isinstance(values, dict):
                raise ValueError(f"{where}section_anchors 应是“写法: kind”的对象")
            merged[group] = {**merged[group], **values}
            continue
        if not isinstance(values, dict):
            raise ValueError(f"{where}{group} 应是对象")
        unknown = sorted(set(values) - set(allowed))
        if unknown:
            raise ValueError(
                f"{where}{group} 出现未知键：{'、'.join(unknown)}；"
                f"可用键：{'、'.join(allowed)}")
        merged[group] = {**merged[group], **values}
    return merged


def load_structure(college_id=None, rules_dir=RULES_ROOT,
                   config_path=COLLEGES_CONFIG_PATH):
    """加载某学院的结构识别表；表文件不存在就用默认表。

    校验是严格的：未知分组/未知键、锚点指向未知 kind、正则编译不过都会立刻抛错，
    并指出是哪个文件、哪个键——配置写错要马上看得见，不能静默按默认表跑。
    规则目录本身不存在时也报错（多半是工作目录不对）：否则会静默退回默认表，
    与服务器实际用的表不一致，这种"配了却没生效"最难查。

    文件位置由 `college_registry` 一处决定（`config/rules/<学校>/<学院>/`）。
    """
    if not college_id:
        return StructureSpec(DEFAULT_STRUCTURE)
    if not os.path.isdir(rules_dir):
        raise ValueError(f"规则目录不存在：{rules_dir}（请在项目根目录运行，或显式传入 rules_dir）")
    try:
        path = path_of(college_id, "structure", rules_dir, config_path)
    except ValueError:
        # 未登记的学院没有可用的表，与"表文件不存在"同一种兜底；
        # 真正拦住未知学院的是 RuleEngine.for_college。
        return StructureSpec(DEFAULT_STRUCTURE)
    if not os.path.isfile(path):
        return StructureSpec(DEFAULT_STRUCTURE)
    try:
        # utf-8-sig：Windows 记事本/Excel 另存常带 BOM，配置文件要能读得进（带不带都行）。
        with open(path, "r", encoding="utf-8-sig") as handle:
            overrides = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"结构识别表不是合法 JSON：{path}（{exc}）") from exc
    if not isinstance(overrides, dict):
        raise ValueError(f"结构识别表应是对象：{path}")
    merged = _merge(DEFAULT_STRUCTURE, overrides, where=f"{path}：")
    return StructureSpec(merged)
