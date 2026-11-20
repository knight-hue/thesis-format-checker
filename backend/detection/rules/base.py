"""规则引擎骨架：常量、块上下文、规则装载、分派表与通用断言。

设计原则：本模块只做确定性比较，不做语义判断。
“为什么错、怎么改”的自然语言解释交给 RAG 层。
"""
import json
import os
import re
from typing import NamedTuple, Optional

from backend.college_registry import RULES_ROOT, path_of
from backend.config import COLLEGE_ID_PATTERN, COLLEGES_CONFIG_PATH
from backend.docx_common.notes import strip_trailing_note, style_note_start

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z0-9]")
# 学院 ID 规范：见 config.COLLEGE_ID_PATTERN（小写字母/数字/下划线/连字符/@版本后缀）。
# 这里既是"未知学院"的判据，也是把 id 拼进文件路径前的安全闸——不许斜杠与「.」。
COLLEGE_ID_RE = re.compile(COLLEGE_ID_PATTERN)

# 标题类 kind（rules["headings"] 的键与 kind 同名）
HEADING_KINDS = frozenset({"heading_1", "heading_2", "heading_3"})

TERMINAL_PUNCT = "。；;.,，、"

# 纸型 -> 高度 cm（用于估算表格是否跨页）。
_PAPER_HEIGHT_CM = {"A4": 29.7, "A3": 42.0, "B5": 25.7, "LETTER": 27.9}

# 磅 ↔ 厘米（1 磅 = 2.54/72 厘米）。
CM_PER_PT = 2.54 / 72.0


def section_text_width_pt(sections):
    """版心宽度（磅）= 页面宽度 − 左右页边距；取不到返回 None。

    captions（续表对齐）与 formulas（OLE 对象是否铺满行宽）共用。
    """
    if not sections:
        return None
    section = sections[0]
    width_cm = section.get("width_cm")
    left_cm, right_cm = section.get("left_cm"), section.get("right_cm")
    if width_cm is None or left_cm is None or right_cm is None:
        return None
    usable_cm = width_cm - left_cm - right_cm
    return usable_cm / CM_PER_PT if usable_cm > 0 else None

# 字体别名：Word 里同一种字体有中文名/英文名两种写法，模板本身就写成
# w:ascii="SimSun"（附件Z-3 模板全文 1398 处），而规范写的是“宋体”。
# 直接字符串比较会把“就是宋体”的正文报成“字体不符合要求”，属于典型误报。
_FONT_ALIASES = {
    "simsun": "宋体", "nsimsun": "新宋体", "simhei": "黑体", "simkai": "楷体",
    # 楷体_GB2312 / 仿宋_GB2312 是同一字体的 GB2312 子集版本，模板页眉用的就是
    # KaiTi_GB2312；若与规范的“楷体”判成两种字体，官方模板自己都会被报格式错。
    "kaiti": "楷体", "kaitigb2312": "楷体",
    "simfang": "仿宋", "fangsong": "仿宋", "fangsonggb2312": "仿宋",
    "lisu": "隶书",
    "youyuan": "幼圆", "dengxian": "等线", "timesnewroman": "times new roman",
    "arial": "arial", "calibri": "calibri", "cambria": "cambria",
}


def _font_key(name):
    """字体名归一化：忽略大小写、空格与分隔符，并把中英文别名折叠成同一个键。"""
    if not name:
        return ""
    key = re.sub(r"[\s_\-]", "", str(name)).lower()
    return _FONT_ALIASES.get(key, key)


def _font_eq(actual, expected):
    """判断文档里的字体名与规范要求的字体名是否指同一种字体。"""
    if not actual or not expected:
        return True
    return _font_key(actual) == _font_key(expected)


def _align_eq(actual, expected):
    """对齐方式比较：规范写“居左”时，“两端对齐”视为等价。

    Word 中文文档的默认对齐就是“两端对齐”，而中文两端对齐只在行末遇到标点/换行
    时才扩字距——单行短段（标题、关键词标签行）里与左对齐没有任何视觉差别。
    模板实测即如此：规范写“居左”，模板自己写成 justify。
    故只做单向放宽（expected=left 接受 actual=justify）：规范要求“两端对齐”的正文
    若写成左对齐，行末仍会参差不齐，必须照报。
    """
    if not actual or not expected:
        return True
    if actual == expected:
        return True
    return expected == "left" and actual == "justify"


# 文字里的连续空白（半角/全角空格、制表符、不换行空格）。
_SPACE_RUN_RE = re.compile(r"[ \u3000\t\u00a0]+")

# 空白字符的称法，用来把“空了几格”讲清楚。
_SPACE_CHARS = {" ": "半角空格", "\u3000": "全角空格", "\t": "制表符", "\u00a0": "不换行空格"}


def _space_runs(text):
    """文本里每一段连续空白的说法，如“致    谢”-> [“4 个半角空格”]。"""
    runs = []
    for m in _SPACE_RUN_RE.finditer(text or ""):
        counts = {}
        for ch in m.group():
            counts[ch] = counts.get(ch, 0) + 1
        runs.append("、".join(f"{n} 个{_SPACE_CHARS[ch]}" for ch, n in counts.items()))
    return runs


def _no_space(text):
    """去掉全部空白后的文字（用于判断两处文字只差空白）。"""
    return _SPACE_RUN_RE.sub("", text or "")


def _space_phrase(runs):
    """空白段的说法；一处空白都没有时说“0 个空格”。"""
    return "、".join(runs) if runs else "0 个空格"


def _describe_space_diff(expected, actual):
    """说清两处文字的空白差在哪：如“模板该处为 4 个半角空格，当前为 2 个全角空格”。

    只在“去掉空白后文字相同、只是空的不一样”时有话说（空几格、空的是哪种空白都要
    说清，因为“空几个格”正是判定依据）；文字本身就不同时返回空串，调用方据此
    只说“与模板不一致”。
    """
    if _no_space(expected) != _no_space(actual):
        return ""
    exp, act = _space_runs(expected), _space_runs(actual)
    if exp == act:
        return ""
    return f"模板该处为 {_space_phrase(exp)}，当前为 {_space_phrase(act)}"


class BlockContext(NamedTuple):
    """当前块在 blocks 中的位置上下文。

    所有块级检查统一接收 ``(block, ctx)``，避免每个检查函数各带一套
    prev/nxt/blocks/index 组合。
    """
    prev: Optional[dict]
    nxt: Optional[dict]
    blocks: list
    index: int


class StyleSpec(NamedTuple):
    """“整段套用某条样式规范”的 kind 声明。

    path            规则 dict 在 rules 中的路径
    rule_id         issue 规则编号前缀
    label           issue 的中文标签
    number          是否继续检查编号格式（取 rule["pattern"]）
    number_rule_id  编号 issue 的规则编号（各 kind 命名不统一，必须逐条给定）
    number_label    编号 issue 的中文标签
    number_example  编号的模板示例（如“图1-1”）；无法从实际文本解析时回退显示
    number_char     题注编号字符（“表”/“图”），给出时检查编号与题名之间的空格
    extra           追加检查的方法名（在 mixin 上按名字解析）
    """
    path: tuple
    rule_id: str
    label: str
    number: bool = False
    number_rule_id: Optional[str] = None
    number_label: Optional[str] = None
    number_example: Optional[str] = None
    number_char: Optional[str] = None
    extra: Optional[str] = None


# kind -> 样式规范。这些 kind 只差“取哪条规则、报哪个编号”，无需各写一段分支。
STYLE_BLOCKS = {
    "heading_1": StyleSpec(("headings", "heading_1"), "heading.heading_1", "标题",
                           number=True, number_rule_id="heading.heading_1.number",
                           number_label="标题编号", number_example="第1章"),
    "heading_2": StyleSpec(("headings", "heading_2"), "heading.heading_2", "标题",
                           number=True, number_rule_id="heading.heading_2.number",
                           number_label="标题编号", number_example="1.1"),
    "heading_3": StyleSpec(("headings", "heading_3"), "heading.heading_3", "标题",
                           number=True, number_rule_id="heading.heading_3.number",
                           number_label="标题编号", number_example="1.1.1"),
    "title": StyleSpec(("title",), "title", "中文题目"),
    "abstract_title": StyleSpec(("abstract", "title"), "abstract.title", "摘要标题"),
    "abstract_body": StyleSpec(("abstract", "body"), "abstract.body", "摘要正文",
                               extra="_extra_embedded_keyword"),
    "keyword_title": StyleSpec(("keywords", "title"), "keyword.title", "关键词标题",
                               extra="_extra_keyword_title"),
    "keyword_body": StyleSpec(("keywords", "body"), "keyword.body", "关键词内容",
                              extra="_extra_keyword_count"),
    "en_title": StyleSpec(("english_abstract", "title"), "en.title", "英文题目"),
    "en_abstract_title": StyleSpec(("english_abstract", "abstract_title"),
                                   "en_abstract.title", "Abstract标题"),
    "en_abstract_body": StyleSpec(("english_abstract", "body"), "en_abstract.body", "英文摘要正文",
                                  extra="_extra_embedded_keyword_en"),
    "en_keyword_title": StyleSpec(("english_abstract", "keyword_title"),
                                  "en_keyword.title", "Keywords标题",
                                  extra="_extra_en_keyword_title"),
    "en_keyword_body": StyleSpec(("english_abstract", "keyword_body"), "en_keyword.body",
                                 "英文关键词内容", extra="_extra_en_keyword_count"),
    "table_caption": StyleSpec(("tables", "caption"), "table.caption", "表题",
                               number=True, number_rule_id="table.caption_number",
                               number_label="表题编号", number_example="表1-1",
                               number_char="表",
                               extra="_extra_table_caption_position"),
    "figure_caption": StyleSpec(("figures", "caption"), "figure.caption", "图题",
                                number=True, number_rule_id="figure.caption_number",
                                number_label="图题编号", number_example="图1-1",
                                number_char="图",
                                extra="_extra_figure_caption_position"),
}

# 固定标题：模板写死的组成部分标题，kind -> (规则路径, issue 规则编号, 中文标签)。
# 这些标题学生照抄模板即可，所以逐字比对（含空白数量）：“致　　谢”是“致”+4 个
# 半角空格+“谢”，写成“致谢”、多空几格、换成别的字都是与模板不一致；
# 段落里的写法说明（“摘要（黑体三号字、居中）”）会在比对前剥掉。
# 章节标题不在此列：标题编号与题名之间的分隔符在模板里就不统一（目录条目用制表符、
# 正文标题用空格），拿它判错会冤枉人。
FIXED_TITLE_BLOCKS = {
    "abstract_title": (("abstract",), "abstract.title_text", "摘要标题"),
    "en_abstract_title": (("english_abstract",), "en_abstract.title_text", "Abstract标题"),
    "reference_title": (("references",), "reference.title_text", "参考文献标题"),
    "acknowledgement_title": (("acknowledgement",), "acknowledgement.title_text", "致谢标题"),
}

# kind -> 检查方法名；方法签名统一为 (block, ctx)。
KIND_HANDLERS = {
    "body": "_check_body",
    "author_info": "_check_author_info",
    "note": "_check_note",
    "formula": "_check_formula",
    "reference_title": "_check_reference_title",
    "reference_entry": "_check_reference_entry",
    "reference_continuation": "_check_reference_continuation",
    "list_item": "_check_list_item",
    "table_continued_note": "_check_continued_note",
    "acknowledgement_body": "_check_ack_body",
}

# block type -> 检查方法名；方法签名统一为 (block, ctx)。
BLOCK_TYPE_HANDLERS = {
    "paragraph": "_check_paragraph_block",
    "table": "_check_table_block",
    "image": "_check_image_block",
}

# 文档级检查：逐块遍历看不出结论的条款（顺序编码制）在这里登记，
# check() 逐个调用，方法签名统一为 (document)。mixin 没实现的名字自动跳过。
DOCUMENT_HOOKS = (
    "_check_reference_citation_order",
)


# 题注前缀：模板原文只写“图号/表号”，没有规定前缀怎么写；识别层认可
# “表 / 表格 / 图表”三种写法，这里剥前缀时同样都认。前缀与编号之间的空白
# 也不在判定范围内（模板没有这句），只有编号本身要按 pattern 校验。
_CAPTION_PREFIX_RE = {
    "图": re.compile(r"^图\s*"),
    "表": re.compile(r"^(?:图表|表格|表)\s*"),
}

# 题注编号片段：数字（含全角写法）+ 一个分隔符 + 数字。
# 空白先原样保留——它是否合规交给 pattern 判（模板要求“章节号与序列号之间
# 用半角'-'隔开”，中间夹空白就不是纯半角 -）。
_CAPTION_NUMBER_TOKEN_RE = re.compile(
    r"[0-9０-９][0-9０-９\s]*[-－—.．]\s*[0-9０-９]+")

# 全角数字 → 半角：模板只要求“阿拉伯数字”，全角写法仍是阿拉伯数字，不判错。
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")

# 中文数字 → 阿拉伯数字（只用于生成“应为”示例，不影响判定）。
_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
              "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}


def _cn_number_to_arabic(value):
    """把“一/十二/二十”等中文数字转成阿拉伯数字示例；解析不了时原样返回。"""
    value = (value or "").strip()
    if not value:
        return value
    if value.isdigit():
        return value
    total = section = number = 0
    for char in value:
        if char in _CN_DIGITS:
            number = _CN_DIGITS[char]
        elif char in _CN_UNITS:
            unit = _CN_UNITS[char]
            if unit == 10000:
                section = (section + number) * unit
                total += section
                section = 0
            else:
                section += (number or 1) * unit
            number = 0
        else:
            return value
    return str(total + section + number)


def _number_example_for_text(text, rule_id, fallback):
    """从实际标题文本生成“应为”示例，保留实际章号/层级。

    例如“第 3 章” → “第3章”，“3. 3. 1” → “3.3.1”。
    """
    text = (text or "").strip()
    if "heading_1" in (rule_id or ""):
        match = re.search(r"第\s*([0-9０-９零〇一二两三四五六七八九十百千万]+)\s*章", text)
        if match:
            number = match.group(1).translate(_FULLWIDTH_DIGITS)
            return f"第{_cn_number_to_arabic(number)}章"
    elif "heading_2" in (rule_id or "") or "heading_3" in (rule_id or ""):
        match = re.search(r"[0-9０-９]+(?:\s*[.．]\s*[0-9０-９]+)+", text)
        if match:
            return match.group(0).translate(_FULLWIDTH_DIGITS).replace("．", ".").replace(" ", "")
    return fallback


def _caption_number_example(label, token, fallback):
    """从题注的编号片段生成实际章节示例，如“图4.1” → “图4-1”。"""
    normalized = (token or "").translate(_FULLWIDTH_DIGITS)
    numbers = re.findall(r"\d+", normalized)
    if len(numbers) >= 2:
        return f"{label}{numbers[0]}-{numbers[1]}"
    return fallback


class RuleEngineBase:
    """规则引擎骨架：装载规则、遍历文档、记录 issue，并提供通用断言。"""

    # 字体名比较：SimSun 与 宋体 视为同一种字体（实现见模块级 _font_eq）
    _font_eq = staticmethod(_font_eq)

    def __init__(self, rules: dict, basis: dict | None = None):
        self.rules = rules
        # 依据表：rule_id 前缀 → 模板里那句说明（config/rules/<学校>/<学院>/<college>_basis.json）。
        # 只做展示用，不参与判定；缺表时不挂依据，不影响结论。
        self.basis = basis or {}
        self._issues = []
        self._blocks = []

    def basis_of(self, rule_id):
        """按最长前缀取这条规则号对应的模板依据；没有则空串。

        编排层（detection/checker_service.py）也用它给语义层的 issue 挂同一条依据，
        所以这里是公开入口，不是内部实现细节。
        """
        if not self.basis or not rule_id:
            return ""
        best = ""
        for prefix in self.basis:
            if (rule_id == prefix or rule_id.startswith(prefix)) \
                    and len(prefix) > len(best):
                best = prefix
        return self.basis.get(best, "")

    @classmethod
    def for_college(cls, college_id: str, rules_dir: str = RULES_ROOT,
                    config_path: str = COLLEGES_CONFIG_PATH):
        """按学院 ID 加载规则文件并构造引擎。

        ID 只允许字母/数字/下划线/连字符；未登记在学院注册表、或规则文件缺失，
        一律抛 ValueError，避免把内部文件路径回显给调用方。文件位置由
        `college_registry` 一处决定（config/rules/<学校>/<学院>/）。
        """
        if not isinstance(college_id, str) or not COLLEGE_ID_RE.fullmatch(college_id):
            raise ValueError(f"未知学院：{college_id}")
        path = path_of(college_id, "rules", rules_dir, config_path)
        if not os.path.isfile(path):
            raise ValueError(f"未知学院：{college_id}")
        with open(path, "r", encoding="utf-8") as f:
            rules = json.load(f)
        basis_path = path_of(college_id, "basis", rules_dir, config_path)
        basis = None
        if os.path.isfile(basis_path):
            with open(basis_path, "r", encoding="utf-8") as f:
                basis = json.load(f)
        return cls(rules, basis)

    # ====================== 主入口 ======================
    def check(self, document: dict) -> dict:
        self._issues = []
        self._blocks = document.get("blocks", [])
        self._sections = document.get("sections", [])
        self._check_sections(self._sections)
        self._check_blocks(self._blocks)
        # 文档级检查：需要看整篇文档才能判的条款（DOCUMENT_HOOKS 里登记）
        for name in DOCUMENT_HOOKS:
            hook = getattr(self, name, None)
            if hook is not None:
                hook(document)
        return {
            "schema_version": "1.0",
            "college_id": self.rules.get("college_id"),
            "passed": len(self._issues) == 0,
            "total_issues": len(self._issues),
            "issues": self._issues,
        }

    # ====================== 问题记录 ======================
    def _issue(self, rule_id, category, severity, location, actual, expected, message,
               suggestion="", **extra):
        issue = {
            "rule_id": rule_id,
            "category": category,
            "severity": severity,
            "location": location,
            "actual": actual,
            "expected": expected,
            "message": message,
            "suggestion": suggestion,
            "basis": self.basis_of(rule_id),
        }
        issue.update(extra)
        self._issues.append(issue)

    def _loc(self, block):
        return {
            "type": block.get("type", "paragraph"),
            "index": block.get("index", 0),
            "text_preview": (block.get("text") or "")[:30],
        }

    @staticmethod
    def _close(actual, expected, tolerance):
        """数值容差比较；任一方为 None 视为未知，不报错。"""
        if actual is None or expected is None:
            return True
        try:
            return abs(float(actual) - float(expected)) <= tolerance
        except (TypeError, ValueError):
            return True

    def _rule_at(self, path):
        node = self.rules
        for key in path:
            if not isinstance(node, dict):
                return None
            node = node.get(key)
        return node

    # ====================== 文档块 ======================
    def _check_blocks(self, blocks):
        for i, b in enumerate(blocks):
            if b.get("front_matter"):
                # 前置区里的图片/表格是版式元素（校徽、整页扫描的承诺书、封面信息表），
                # 不是论文插图/表格；按图表规则要求题注属于“报错内容与事实不符”。
                continue
            handler = BLOCK_TYPE_HANDLERS.get(b.get("type"))
            if handler is not None:
                getattr(self, handler)(b, self._context(blocks, i))

    @staticmethod
    def _context(blocks, i):
        return BlockContext(
            prev=blocks[i - 1] if i > 0 else None,
            nxt=blocks[i + 1] if i + 1 < len(blocks) else None,
            blocks=blocks,
            index=i,
        )

    def _check_paragraph_block(self, b, ctx):
        # 图形内部的文字（形状/文本框）按模板 §5.3「图内字体字号：宋体五号字」判。
        # 但**已被识别成更具体类型**的段落不抢：例如文本框里写着“注：…”的段落，
        # 它是注释（§注释 有专门的起排/字体/段前规定），按注释判才对。
        # 只有通用正文/未识别的文字才落到图内规则上。
        if b.get("figure_text") and (b.get("kind") in (None, "", "body", "unknown")):
            self._check_figure_text(b, ctx)
            return
        kind = b.get("kind")
        self._check_fixed_title_of(b, kind)
        spec = STYLE_BLOCKS.get(kind)
        if spec is not None:
            self._check_style_spec(b, spec, ctx)
            return
        handler = KIND_HANDLERS.get(kind)
        if handler is not None:
            getattr(self, handler)(b, ctx)

    # ====================== 固定标题文字 ======================
    def _check_fixed_title_of(self, b, kind):
        entry = FIXED_TITLE_BLOCKS.get(kind)
        if entry is not None:
            self._check_fixed_title(b, *entry)

    def _check_fixed_title(self, b, path, rule_id, label):
        """固定标题的文字须与模板原文逐字一致（含空白数量）。

        模板原文写在 rules 里（如 acknowledgement.title_text = “致　　谢”），
        没配该项就不查。报错时说清差在哪：是字数/用词不同，还是空了几格不同，
        只报“标题不符合要求”帮不上忙。
        """
        canonical = (self._rule_at(path) or {}).get("title_text")
        if not canonical:
            return
        actual = strip_trailing_note((b.get("text") or "").strip())
        if actual == canonical:
            return
        hint = _describe_space_diff(canonical, actual)
        suggestion = f"把{label}改成模板原文「{canonical}」"
        if hint:
            suggestion += f"（{hint}）"
        self._issue(rule_id, label, "error", self._loc(b),
                    f"「{actual}」", f"「{canonical}」",
                    f"{label}文字与模板不一致", suggestion=suggestion)

    def _check_style_spec(self, b, spec, ctx):
        rule = self._rule_at(spec.path)
        self._check_style_block(b, rule, spec.rule_id, spec.label)
        pattern = (rule or {}).get("pattern")
        if spec.number:
            if spec.number_char:
                # 题注：模板只规定“编号本身”（章号-序号），前缀与编号之间的空白、
                # 前缀写成“表格/图表”都不在本条判定。
                self._check_caption_number_format(
                    b, spec.number_char, pattern, spec.number_rule_id,
                    spec.number_label, spec.number_example)
            else:
                self._check_number_format(
                    b, pattern, spec.number_rule_id, spec.number_label,
                    spec.number_example)
        if spec.number_char:
            self._check_caption_number_space(b, spec.number_char)
        if spec.extra:
            getattr(self, spec.extra)(b, ctx)

    # ====================== 通用样式块（标题/摘要/表题/图题等） ======================
    def _check_run_fonts(self, b, label, *, cjk_font=None, latin_font=None,
                         size_pt=None, size_tol=0.5, loc=None,
                         font_rule_id=None, size_rule_id=None, until=None):
        """统一的 run 级排版检查（中文字体/西文字体/字号）。

        cjk_font/latin_font/size_pt 为 None 表示不检查对应项；
        font_rule_id/size_rule_id 为 issue 的规则编号，需与检查项成对提供；
        until 是只检查前 N 个字符的边界（None 表示整段都查）——模板对“注意标志”
        这类前缀有字体要求、对后面的内容没有时用它圈定范围。

        段末的排版说明括注（“三级标题（黑体小4号、居左）”）不是这一行的排版内容，
        只检查括注之前的 run：模板样例行的括注里会为了写清“小4号”而把字排成小四号，
        拿它判标题字号就是误报。位置的对应关系要求 runs 与 text 完全同源，
        否则宁可不跳过（避免把正文 run 当括注漏检）。
        """
        loc = loc or self._loc(b)
        note_at = style_note_start(b.get("text"))
        if note_at is None or "".join(run.get("text") or ""
                                      for run in b.get("runs", [])) != (b.get("text") or ""):
            note_at = None
        offset = 0
        for i, run in enumerate(b.get("runs", [])):
            text = run.get("text") or ""
            start, offset = offset, offset + len(text)
            if note_at is not None and start >= note_at:
                continue
            if until is not None and start >= until:
                continue
            # 空 run（Word 里删字后残留）不渲染任何字符，其字体字号不影响排版；
            # 模板里“致  谢”这类标题的首个 run 就是空 run，把它算成格式错是误报。
            if not text.strip():
                continue
            rloc = {**loc, "run": i}
            if cjk_font and CJK_RE.search(text):
                actual = run.get("east_asia_font")
                if actual and not _font_eq(actual, cjk_font):
                    self._issue(font_rule_id or "", label, "error", rloc,
                                actual, cjk_font, f"{label}字体不符合要求")
            if latin_font and LATIN_RE.search(text):
                actual = run.get("font_name")
                if actual and not _font_eq(actual, latin_font):
                    self._issue(font_rule_id or "", label, "error", rloc,
                                actual, latin_font, f"{label}字体不符合要求")
            if size_pt is not None and not self._close(run.get("font_size_pt"), size_pt, size_tol):
                self._issue(size_rule_id or "", label, "error", rloc,
                            f'{run.get("font_size_pt")} pt', f"{size_pt} pt",
                            f"{label}字号不符合要求")

    def _check_paragraph_metrics(self, b, rule, rule_id, label, loc=None):
        """段落级度量：首行缩进与行距。规则里没写这两项就不查。

        样式块（标题/摘要/关键词）的规则只声明字体字号对齐时不受影响；
        摘要正文这类“整段套用某条排版”的规则可以顺带声明缩进与行距。
        """
        if not rule:
            return
        loc = loc or self._loc(b)
        expected_indent = rule.get("first_line_indent_pt")
        if expected_indent is not None and not self._close(
                b.get("first_line_indent_pt"), expected_indent,
                rule.get("indent_tolerance_pt", 1.0)):
            self._issue(f"{rule_id}.first_line_indent", label, "error", loc,
                        f'{b.get("first_line_indent_pt")} pt', f"{expected_indent} pt",
                        f"{label}首行缩进不符合要求")
        expected_multiple = rule.get("line_spacing_multiple")
        if expected_multiple is None:
            return
        fixed = b.get("line_spacing_pt")
        if fixed:
            self._issue(f"{rule_id}.line_spacing", label, "error", loc,
                        f"固定行距 {fixed} pt", f"{expected_multiple} 倍",
                        f"{label}行距不符合要求（应为倍数行距）")
        elif not self._close(b.get("line_spacing_multiple"), expected_multiple,
                             rule.get("spacing_tolerance", 0.05)):
            self._issue(f"{rule_id}.line_spacing", label, "error", loc,
                        f'{b.get("line_spacing_multiple")} 倍', f"{expected_multiple} 倍",
                        f"{label}行距不符合要求")

    def _check_style_block(self, b, rule, rule_id, label):
        if not rule:
            return
        loc = self._loc(b)
        tol = rule.get("font_size_tolerance_pt", 0.5)

        font = rule.get("font_name")
        cjk_font = rule.get("east_asia_font")
        latin_font = rule.get("latin_font")
        if font and not cjk_font and not latin_font:
            # 单一 font_name 简写：中文规则（黑体/宋体）只约束中文字体，
            # 标题/图表题中的阿拉伯数字、英文不强制使用中文字体；
            # 西文规则（如 Times New Roman）只约束西文字体。
            if CJK_RE.search(font):
                cjk_font = font
            else:
                latin_font = font
        self._check_run_fonts(b, label, cjk_font=cjk_font, latin_font=latin_font,
                              size_pt=rule.get("font_size_pt"), size_tol=tol,
                              loc=loc, font_rule_id=f"{rule_id}.font",
                              size_rule_id=f"{rule_id}.font_size")

        align = b.get("alignment")
        expected_align = rule.get("alignment")
        if not _align_eq(align, expected_align):
            self._issue(f"{rule_id}.alignment", label, "error", loc,
                        align, expected_align, f"{label}对齐方式不符合要求")

        # 加粗取“第一个有文字的 run”：标题的首个 run 可能是空 run（模板里就有），
        # 它的 bold 并不代表标题本身的加粗设置。
        if rule.get("bold") is not None and b.get("runs"):
            actual_bold = next((run.get("bold") for run in b["runs"]
                                if (run.get("text") or "").strip()), None)
            if actual_bold is not None and bool(actual_bold) != bool(rule["bold"]):
                self._issue(f"{rule_id}.bold", label, "warning", loc,
                            actual_bold, rule["bold"], f"{label}加粗设置与规范不一致")

        self._check_paragraph_metrics(b, rule, rule_id, label, loc)

    # ====================== 编号格式 ======================
    def _check_number_format(self, b, pattern, rule_id, label, example=None):
        if not pattern:
            return
        text = self._visible_number_text(b)
        if text and not re.match(pattern, text):
            # “应为”保留实际章节/层级，不再全部显示固定的“第1章/1.1”。
            expected = _number_example_for_text(text, rule_id, example or pattern)
            extra = {"auto_number": True} if b.get("auto_number") else {}
            self._issue(rule_id, label, "warning", self._loc(b),
                        text[:20], expected,
                        f"{label}格式不符合要求", **extra)

    @staticmethod
    def _visible_number_text(b):
        """段落里可见的编号文字：正文文本，或自动编号渲染出的编号。

        自动编号（w:numPr）的编号不在段落文本里，须由 numbering.xml 还原
        （见 parser.numbering）。还原不出来时返回空串 → 调用方跳过该检查，
        与“编号不在文本里就没法判”的旧口径一致；能还原时才真的比格式。
        """
        if b.get("auto_number"):
            return (b.get("auto_number_text") or "").strip()
        return (b.get("text") or "").strip()

    def _check_caption_number_format(self, b, label, pattern, rule_id,
                                     number_label, example):
        """§5.1：题注编号只判**编号本身**（章号-序号），不判“图/表”前缀。

        模板对编号只有两句：按章节用阿拉伯数字依序连续编号；章节号与序列号之间
        用半角“-”隔开。它没有规定“图/表”与数字之间不得留空白，也没规定前缀
        只能写“表”一个字——把“图 4-1”“表格 3-1”报成编号格式错是超出原句的
        误判（实测 5 条）。

        做法：先剥掉题注前缀（图 / 表、表格、图表）与其后的空白，再取编号片段：
        - 全角阿拉伯数字先归一化成半角（模板只要求“阿拉伯数字”，全角数字仍是
          阿拉伯数字）；
        - 编号片段里的空白与全角短横不归一化——与“用半角 - 隔开”不符，照报；
        - 校验用 rules 的 pattern（现在只描述编号本身的形态，如 ``^\\d+-\\d+$``）。
        """
        if not pattern:
            return
        text = self._visible_number_text(b)
        if not text:
            return
        prefix = _CAPTION_PREFIX_RE[label].match(text)
        if prefix is None:
            return
        extra = {"auto_number": True} if b.get("auto_number") else {}
        rest = text[prefix.end():]
        token_match = _CAPTION_NUMBER_TOKEN_RE.match(rest)
        if token_match is None:
            # 编号不是“数字+分隔符+数字”的形态（如“图一”）：按格式错报
            self._issue(rule_id, number_label, "warning", self._loc(b),
                        text[:20], example or "章号-序号",
                        f"{number_label}格式不符合要求",
                        suggestion=(f"编号应写成“章号-序号”（章节号与序列号之间用"
                                    f"半角“-”），如“{example}”"), **extra)
            return
        token = token_match.group(0)
        normalized = token.translate(_FULLWIDTH_DIGITS)
        if re.fullmatch(pattern, normalized):
            return
        expected = _caption_number_example(label, token, example or "章号-序号")
        self._issue(rule_id, number_label, "warning", self._loc(b),
                    f"{label}{token}", expected,
                    f"{number_label}格式不符合要求",
                    suggestion=(f"编号应写成“章号-序号”（章节号与序列号之间用"
                                f"半角“-”），如“{expected}”；数字可用阿拉伯数字，"
                                "分隔符必须是半角“-”，中间不留空白"), **extra)

    def _check_caption_number_space(self, b, label):
        """§5.3/§5.5：编号与题名之间应空一个汉字位置。

        “一个汉字位置”按字面量宽：全角空格就是一个汉字，半角空格只有半个汉字宽，
        要两个才够。实测 584 条题注：527 条一个半角空格、28 条两个半角、29 条
        没有空格、0 条用全角——按模板字面，一个半角空格不够。
        """
        text = (b.get("text") or "").strip()
        head = _CAPTION_PREFIX_RE[label].match(text)
        if not head:
            return
        number = _CAPTION_NUMBER_TOKEN_RE.match(text, head.end())
        if not number:
            return
        rest = text[number.end():]
        if not rest:
            return
        gap = re.match(r"[ \u3000]*", rest).group(0)
        half, full = gap.count(" "), gap.count("\u3000")
        if full + 0.5 * half >= 1:
            return
        if not gap:
            actual = "编号后没有空格"
        else:
            parts = []
            if half:
                parts.append(f"{half} 个半角空格")
            if full:
                parts.append(f"{full} 个全角空格")
            actual = "编号后是 " + " + ".join(parts)
        self._issue("caption.number_space", label, "warning", self._loc(b),
                    actual, "编号后空一个汉字位置",
                    "编号与题名之间应空一个汉字位置",
                    "把编号与题名之间改成一个全角空格（或两个半角空格）")

    def _page_usable_height_cm(self):
        """纸张可排高度：模板只给了页边距，纸张尺寸没有说明句（已撤销），按 A4 算。"""
        margins = (self.rules.get("page") or {}).get("margins") or {}
        height = _PAPER_HEIGHT_CM.get("A4", 29.7)
        return height - (margins.get("top_cm") or 0) - (margins.get("bottom_cm") or 0)
