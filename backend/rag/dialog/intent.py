"""对话意图层：纯规则识别，不调用任何模型。

设计约定：

- 只产出「意图 + 槽位」，不做判定、不拼答案；
- 规则命中顺序固定，先命中先返回；理由写进 Intent.reason，便于单测与排障；
- 找不到明确信号时兜底为 spec_qa（保守：检索不到会如实回答未找到，不会编造）；
- 论文类意图在「会话里没有问题摘要」时由状态机转 paper_missing，不检索、不生成。
"""
import re
from dataclasses import dataclass, field

# 论文类意图：这些意图需要会话里有检测结果才能回答
PAPER_INTENTS = ("paper_overview", "paper_detail", "paper_fix",
                 "paper_basis", "paper_coverage")

# 对外暴露的意图名（与前端 RagIntent 保持一致）
INTENT_NAMES = (
    "paper_overview",
    "paper_detail",
    "paper_fix",
    "paper_basis",
    "paper_coverage",
    "spec_qa",
    "out_of_scope",
    "clarify",
)

_INDEX_RE = re.compile(r"第\s*([一二三四五六七八九十百\d]+)\s*(?:条|个|项)")
_LAST_INDEX_RE = re.compile(r"最后一条|最后一个|最后那一条")
# 相对指代：上一轮看过第 N 条，再问“下一条/上一条”；不再等同于“最后一条”
_NEXT_INDEX_RE = re.compile(r"下一条|下一个|后一条|再下一条|下一项")
_PREV_INDEX_RE = re.compile(r"上一条|上一个|前一条|上一项")
# 继续往后看：只认“再看一批”的说法，不把“还有哪些依据”误判成分页
_MORE_RE = re.compile(r"继续看|继续列|还有吗|还有呢|还有哪些没(?:列|说|讲)|看全部|全部列出|剩下的呢")
# 纠正/否定：“不是第2条，是第3条”要按最后一个序号重新定位
_CORRECTION_RE = re.compile(r"不是|不对|错了|说错")
# 依据/出处：答案只能来自 issue 里记录的模板原句
_BASIS_RE = re.compile(r"依据|出处|模板哪一?句|模板怎么写的|哪一句|哪句话|为什么|凭什么")
# 未检/覆盖：只认“没查/未查/检查范围”，不把“检测结果”误判过来
_COVERAGE_RE = re.compile(r"没查|未查|没有查|哪些没检|哪些未检|未检|漏检|检查范围|覆盖了|覆盖哪些|有没有漏")
_THIS_ISSUE_RE = re.compile(r"这条|那一条|这一条|这几条|该项|这题")
_CONTEXT_RE = re.compile(r"这些|它们|那几条|上面|刚才|这几条|那些")
_SEVERITY_RE = re.compile(r"只看\s*(错误|警告|提示)|只\s*(?:看|要)\s*(error|warning)", re.I)
_RULE_RE = re.compile(r"规则号|规则编号|对应哪条规则|哪条规则|对应规则")
_RULE_ID_RE = re.compile(r"规则号\s*[:：]?\s*([A-Za-z][\w.-]*)")
_QUESTION_SPLIT_RE = re.compile(r"[？?；;]+")
_FIX_RE = re.compile(
    r"怎么改|如何修改|怎么修改|如何修复|怎么修复|如何改|怎么办|怎么处理|"
    r"怎么解决|修改建议|能自动改吗"
)
_OVERVIEW_RE = re.compile(
    r"哪里不对|哪里有问题|有什么问题|哪些问题|有什么错|不合格|不规范|"
    r"帮我看看|总结|概况|整体情况|检测结果"
)
_SPEC_RE = re.compile(r"应该|要求|是什么|多少|几号|多大|用什么|能不能|可不可以|是否")
# 非论文领域词：比检索后返回「未检索到」更快也更诚实
_OUT_OF_SCOPE_RE = re.compile(r"天气|股票|写代码|翻译这段|讲个笑话|你是谁|写首诗|做饭")
_NO_OP_RE = re.compile(r"^(无|没有|不需要|都可以|随便|不限|都行|嗯|好的|ok|是|可以)$", re.I)
_HEADING_LEVEL_RE = re.compile(r"一级|二级|三级|1\s*级|2\s*级|3\s*级")
_HEADING_WORD_RE = re.compile(r"标题")
_MY_PAPER_RE = re.compile(r"我的|这篇|本论文|本文")

# 类目关键词 -> 规则号前缀。类目本身从会话里的 issues 动态取，不写死规则号。
_CATEGORY_HINTS = (
    ("图", ("图题", "图注", "插图", "图片", "曲线", "图"), "figure"),
    ("表", ("表题", "表格", "表"), "table"),
    ("公式", ("公式",), "formula"),
    ("参考文献", ("参考文献", "文献", "引用"), "reference"),
    ("关键词", ("关键词",), "keyword"),
    ("摘要", ("摘要",), "abstract"),
    ("标题", ("标题",), "heading"),
    ("正文", ("正文",), "body"),
    ("页边距", ("页边距", "边距", "页面设置"), "page"),
    ("页码", ("页码",), "page"),
    ("目录", ("目录",), "toc"),
    ("致谢", ("致谢",), "acknowledg"),
    ("列表", ("列表", "序号"), "list_item"),
    ("术语", ("术语", "缩写"), "terminology"),
)

_ATTRIBUTES = (
    "字号", "字体", "行距", "缩进", "对齐", "编号", "位置",
    "页边距", "页码", "字数", "间距", "大小",
)

_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}


@dataclass(frozen=True)
class Intent:
    """一次识别结果。slots 只放结构化线索，答案由事实层/规范链路生成。"""

    name: str
    slots: dict = field(default_factory=dict)
    confidence: float = 0.3
    reason: str = "fallback"

    def __post_init__(self):
        if self.name not in INTENT_NAMES:
            raise ValueError(f"未知意图：{self.name}")


def parse_cn_number(text):
    """把「3 / 三 / 十二 / 二十一」解析成整数；解析不了返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if "百" in text:
        left, _, right = text.partition("百")
        hundreds = _CN_DIGITS.get(left, 1) if left else 1
        rest = parse_cn_number(right) if right else 0
        return hundreds * 100 + (rest or 0) if right else hundreds * 100
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    if len(text) == 1 and text in _CN_DIGITS:
        return _CN_DIGITS[text]
    return None


def split_questions(text):
    """按问号/分号拆一轮多问；只有一个问题时返回空列表。"""
    parts = [part.strip() for part in _QUESTION_SPLIT_RE.split(text or "")]
    parts = [part for part in parts if part]
    return parts if len(parts) > 1 else []


def _match_attribute(question):
    """把口语问法归一到槽位值；只做词表映射，不猜语义。"""
    if "字体" in question:
        return "字体"
    if "字号" in question or ("字" in question and
                              ("多大" in question or "几号" in question or "多少" in question)):
        return "字号"
    for word in _ATTRIBUTES:
        if word in question:
            return word
    return ""


def _match_categories(question, issues):
    """返回问题里命中的全部类目（按在问题里出现的先后排序）。

    每个类目是 {"category", "rule_ids", "label"}；category 取 issues 里真实出现过的
    类目名，rule_ids 取真实规则号里以对应前缀开头的那些。两者都可能为空，调用方据此
    决定是否算「命中类目」。

    为什么返回列表：「图题和表题各有什么问题」必须两个都答；早先只取第一个命中，
    另一半会被静默丢掉。
    """
    categories = []
    rule_ids = []
    for issue in issues or []:
        category = str(issue.get("category") or "").strip()
        if category and category not in categories:
            categories.append(category)
        rule_id = str(issue.get("rule_id") or "").strip()
        if rule_id and rule_id not in rule_ids:
            rule_ids.append(rule_id)

    matched = []
    seen = set()
    for label, keywords, prefix in _CATEGORY_HINTS:
        hits = [word for word in keywords if word in question]
        if not hits:
            continue
        position = min(question.find(word) for word in hits)
        category = next((name for name in categories if label in name), "")
        rules = [item for item in rule_ids if item.startswith(prefix)][:20]
        if not category and not rules:
            continue
        # 同一个类目被多个关键词命中时只保留一次（如「图表」同时含“图”“表”）
        if category and category in seen:
            continue
        if category:
            seen.add(category)
        matched.append((position, {"category": category,
                                   "rule_ids": rules,
                                   "label": label}))
    matched.sort(key=lambda pair: pair[0])
    return [group for _position, group in matched]


def _match_category(question, issues):
    """兼容旧调用：只取第一个命中类目。返回 (category, rule_ids, label)。"""
    groups = _match_categories(question, issues)
    if not groups:
        return "", [], ""
    first = groups[0]
    return first["category"], first["rule_ids"], first["label"]


def _category_slots(groups):
    """把类目命中转成筛选槽位；多个类目用 categories 并集，不静默只取一个。"""
    groups = [group for group in (groups or []) if isinstance(group, dict)]
    if not groups:
        return {}
    if len(groups) == 1:
        group = groups[0]
        return {
            "scope": "category" if group.get("category") else "rule",
            "category": group.get("category") or "",
            "rule_ids": list(group.get("rule_ids") or []),
            "label": group.get("label") or "",
        }
    return {
        "scope": "categories",
        "categories": groups,
        "labels": [group.get("label") or "" for group in groups],
        "label": "、".join(group.get("label") or "" for group in groups),
    }


def _correction_index(text):
    """「不是第 2 条，是第 3 条」 -> 3。只看带纠正词且出现两个以上序号的句子。"""
    if not _CORRECTION_RE.search(text):
        return None
    matches = list(_INDEX_RE.finditer(text))
    if len(matches) < 2:
        return None
    return parse_cn_number(matches[-1].group(1))


def _clarify_paper_index(pending):
    # 澄清回答里的候选由 facts 层写入 pending["candidates"]
    candidates = pending.get("candidates") or []
    return Intent(
        "paper_detail",
        {"scope": "index", "candidates": candidates},
        0.5,
        "R1 澄清回答缺少序号",
    )


def _answer_name(text, default="paper_detail"):
    """同一句里可能同时有“依据”“怎么改”：依据优先，其次修改。"""
    if _BASIS_RE.search(text):
        return "paper_basis"
    if _FIX_RE.search(text):
        return "paper_fix"
    return default


def _consume_clarify(question, pending):
    """把本轮输入当作上一轮澄清的回答；不是回答就返回 None（丢弃 pending）。"""
    kind = (pending or {}).get("kind")
    if kind == "paper_index":
        match = _INDEX_RE.search(question)
        if match:
            number = parse_cn_number(match.group(1))
            if number:
                return Intent(
                    _answer_name(question),
                    {
                        "scope": "index",
                        "index": number,
                        "candidates": pending.get("candidates") or [],
                    },
                    0.95,
                    "R1 澄清回答（序号）",
                )
        if _NO_OP_RE.match(question.strip()):
            return Intent(
                "paper_detail",
                {"scope": "all"},
                0.9,
                "R1 澄清回答（全部）",
            )
        return None
    if kind == "heading_level":
        match = _HEADING_LEVEL_RE.search(question)
        if not match:
            return None
        level = match.group(0).replace("1", "一").replace("2", "二").replace("3", "三")
        if not level.endswith("级"):
            level = level + "级"
        return Intent(
            "spec_qa",
            {
                "attribute": pending.get("attribute") or "字体字号",
                "heading_level": level,
                "clarify_answer": True,
            },
            0.95,
            "R1 澄清回答（标题层级）",
        )
    return None


def detect_intent(question, has_paper, paper_errors=None,
                  pending_clarify=None, last_listed=None,
                  clarify_enabled=True, last_scope=None):
    """识别一轮问题，返回 Intent。

    Args:
        question: 用户原始问题
        has_paper: 会话里是否已有检测结果摘要
        paper_errors: 检测结果摘要列表（用于类目/规则号匹配）
        pending_clarify: 上一轮留下的澄清状态
        last_listed: 上一次答案列出的条目顺序
        clarify_enabled: 是否允许产出澄清追问
    """
    text = (question or "").strip()
    issues = paper_errors or []
    listed = last_listed or []
    context_scope = last_scope if isinstance(last_scope, dict) else {}

    # R1：上一轮在等澄清回答
    if pending_clarify:
        consumed = _consume_clarify(text, pending_clarify)
        if consumed is not None:
            return consumed
        # 不是回答：丢弃 pending，按新问题重新识别（状态由服务层清除）

    # R0：一轮多个问题先拆开，避免把两个意图混成一个答案
    parts = split_questions(text)
    if parts:
        return Intent(
            "clarify",
            {"kind": "multi_question", "options": parts[:5],
             "missing": ["choose_one"]},
            0.85,
            "R0 多问题待拆分",
        )

    # R2a：显式规则号（前端卡片按钮用，避免序号在筛选列表里错位）
    rule_match = _RULE_ID_RE.search(text)
    if rule_match:
        fix = bool(_FIX_RE.search(text))
        basis = bool(_BASIS_RE.search(text))
        return Intent(
            "paper_basis" if basis else ("paper_fix" if fix else "paper_detail"),
            {"scope": "rule", "rule_ids": [rule_match.group(1)],
             "mode": "basis" if basis else ("fix" if fix else "rule")},
            0.95,
            "R2a 显式规则号",
        )

    # R2b：上下文范围复用（“这些/它们”指上一轮筛选出的条目）
    if context_scope and context_scope.get("scope") != "index" and _CONTEXT_RE.search(text):
        if _FIX_RE.search(text) or _BASIS_RE.search(text) or not _SPEC_RE.search(text):
            scope = dict(context_scope)
            scope["context"] = "last_scope"
            if _RULE_RE.search(text):
                scope["mode"] = "rule"
            return Intent(
                _answer_name(text),
                scope,
                0.82,
                "R2b 上下文范围复用",
            )

    # R2b-1：上一轮看的是某一条（第 N 条），本轮问“这条/它” -> 继续用同一条
    cursor = int(context_scope.get("cursor") or 0)
    if (context_scope.get("scope") == "index" and cursor > 0
            and (_THIS_ISSUE_RE.search(text) or _CONTEXT_RE.search(text))):
        return Intent(
            _answer_name(text),
            {"scope": "index", "index": cursor, "candidates": listed},
            0.86,
            "R2b 单条上下文复用",
        )

    # R2c：只看错误 / 只看警告（可与“怎么改”组合）
    severity_match = _SEVERITY_RE.search(text)
    if severity_match:
        raw = (severity_match.group(1) or severity_match.group(2) or "").lower()
        severity = "error" if raw in ("错误", "error") else (
            "warning" if raw in ("警告", "warning") else "info")
        scope = dict(context_scope)
        scope.update({"scope": "severity", "severity": severity,
                      "category": "", "rule_ids": [], "label": "",
                      "categories": []})
        if _RULE_RE.search(text):
            scope["mode"] = "rule"
        return Intent(
            _answer_name(text),
            scope,
            0.85,
            "R2c 按严重级别筛选",
        )

    # R2d：规则号查询（只取 issue 里真实存在的 rule_id；有序号时交给 R2）
    if _RULE_RE.search(text) and not _INDEX_RE.search(text):
        slots = _category_slots(_match_categories(text, issues)) or {"scope": "all"}
        slots["mode"] = "rule"
        return Intent(
            _answer_name(text),
            slots,
            0.9,
            "R2d 规则号查询",
        )

    # R2：序号指代（有论文上下文时最具体，优先于其它规则）
    corrected = _correction_index(text)
    if corrected:
        slots = {"scope": "index", "index": corrected, "candidates": listed}
        if _RULE_RE.search(text):
            slots["mode"] = "rule"
        return Intent(_answer_name(text), slots, 0.95, "R2 纠正序号")
    match = _INDEX_RE.search(text)
    if match:
        number = parse_cn_number(match.group(1))
        if number:
            # 带上上一次的列出顺序：卡片上看到的 #N 就是这一句的 N
            slots = {"scope": "index", "index": number, "candidates": listed}
            if _RULE_RE.search(text):
                slots["mode"] = "rule"
            return Intent(
                _answer_name(text),
                slots,
                0.95,
                "R2 序号指代",
            )
    if _LAST_INDEX_RE.search(text):
        if listed:
            slots = {"scope": "index", "index": len(listed), "candidates": listed}
            if _RULE_RE.search(text):
                slots["mode"] = "rule"
            return Intent(
                _answer_name(text),
                slots,
                0.9,
                "R2 序号指代（最后一条）",
            )
        return Intent(
            "paper_detail",
            {"scope": "index"},
            0.6,
            "R2 无列表可指代",
        )
    next_match = _NEXT_INDEX_RE.search(text)
    prev_match = _PREV_INDEX_RE.search(text) if not next_match else None
    if next_match or prev_match:
        slots = {"scope": "index", "direction": 1 if next_match else -1}
        if _RULE_RE.search(text):
            slots["mode"] = "rule"
        return Intent(_answer_name(text), slots, 0.9, "R2 相对指代")

    # R2e：本次哪些条款没查（只读会话里保存的 unchecked 清单）
    if _COVERAGE_RE.search(text):
        return Intent("paper_coverage", {"scope": "all"}, 0.9, "R2e 未检清单")

    # R2f：继续往后看（分页）；筛选条件沿用上一轮
    if _MORE_RE.search(text):
        scope = {key: value for key, value in context_scope.items()
                 if key in ("category", "rule_ids", "label", "severity", "categories")
                 and value not in (None, "", [])}
        scope["scope"] = "page"
        scope["page"] = int(context_scope.get("page") or 1) + 1
        return Intent("paper_detail", scope, 0.85, "R2f 继续看下一批")

    # R7：非论文领域词放在规范疑问之前，避免「翻译这段是什么意思」被当成规范问题
    if _OUT_OF_SCOPE_RE.search(text):
        return Intent("out_of_scope", {}, 0.9, "R7 非论文领域")

    # R3b：问“为什么/依据” -> 只答 issue 里记录的模板原句
    if _BASIS_RE.search(text) and has_paper:
        groups = _match_categories(text, issues)
        has_focus = bool(_CONTEXT_RE.search(text) or _THIS_ISSUE_RE.search(text)
                         or _MY_PAPER_RE.search(text) or groups)
        if not groups and context_scope.get("scope") == "index" and cursor > 0:
            # 刚看过某一条，再说“依据哪句模板”就是问这一条
            return Intent(
                "paper_basis",
                {"scope": "index", "index": cursor, "candidates": listed},
                0.88,
                "R3b 依据类（当前条）",
            )
        if has_focus or not _SPEC_RE.search(text):
            slots = _category_slots(groups) or {"scope": "all"}
            slots["mode"] = "basis"
            return Intent("paper_basis", slots, 0.88, "R3 依据类")

    # R3：怎么改 / 如何修复
    if _FIX_RE.search(text):
        slots = _category_slots(_match_categories(text, issues)) or {"scope": "all"}
        return Intent(
            "paper_fix",
            slots,
            0.92,
            "R3 修改类",
        )

    groups = _match_categories(text, issues)

    # R5a：多个类目并列（「图题和表题」）必须两个都答，不能只取第一个
    if len(groups) >= 2 and has_paper:
        return Intent(
            "paper_detail",
            _category_slots(groups),
            0.87,
            "R5 多类目定位",
        )

    # R4：概览
    if _OVERVIEW_RE.search(text):
        return Intent("paper_overview", {"scope": "all"}, 0.9, "R4 概览类")

    # R5：类目 + 疑问语气 -> 定位到论文里的该类条目
    if groups and has_paper:
        if _SPEC_RE.search(text) and not _MY_PAPER_RE.search(text):
            # 「图题字号要求多少」是在问规范，不是问自己错在哪 -> 交给 R6
            pass
        else:
            return Intent(
                "paper_detail",
                _category_slots(groups),
                0.85,
                "R5 类目定位",
            )

    # R6：规范疑问（可触发「标题层级」澄清）
    if _SPEC_RE.search(text):
        attribute = _match_attribute(text)
        if (clarify_enabled and attribute and _HEADING_WORD_RE.search(text)
                and not _HEADING_LEVEL_RE.search(text)):
            return Intent(
                "clarify",
                {"kind": "heading_level", "attribute": attribute, "missing": ["heading_level"]},
                0.8,
                "R6 标题层级不明确",
            )
        return Intent(
            "spec_qa",
            {"attribute": attribute} if attribute else {},
            0.8,
            "R6 规范疑问",
        )

    # R8：没有任何明确信号时兜底规范问答；无论文上下文的论文类问题
    # 由状态机根据 has_paper 转 paper_missing，不在这里猜。
    return Intent("spec_qa", {}, 0.3, "R9 兜底")
