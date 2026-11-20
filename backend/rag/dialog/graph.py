"""同步状态机：router -> 论文类节点 / 规范问答 / 澄清 / 越界。

与图书馆项目的 AgentGraph 不同：
- 节点是普通函数，不用 async generator（本项目接口同步返回，节点内部不流式）；
- 状态字段显式声明，节点只能改写 outcome/next，便于单测；
- 规范问答通过 spec_answer 回调注入，避免与 RAGService 循环依赖。
"""
from dataclasses import dataclass, field

from . import blocks, facts, tools
from .intent import PAPER_INTENTS, Intent

MAX_STEPS = 4


@dataclass
class DialogOutcome:
    conclusion: str = ""
    basis: str = ""
    suggestion: str = ""
    found: bool = False
    actions: list = field(default_factory=list)
    blocks: list = field(default_factory=list)
    suggestions: list = field(default_factory=list)
    clarify: dict = None
    answer_source: str = "none"
    last_listed: list = None
    last_scope: dict = None
    pending_clarify: dict = None
    history_answer: str = ""
    spec_result: dict = None
    trace: list = field(default_factory=list)


@dataclass
class DialogState:
    question: str
    intent: Intent
    has_paper: bool
    paper_errors: list
    paper_total: int
    last_listed: list
    pending_clarify: dict
    paper_unchecked: list = None
    last_scope: dict = field(default_factory=dict)
    steps: int = 0
    next: str = "router"
    outcome: DialogOutcome = field(default_factory=DialogOutcome)


def _set_answer(state, text, *, found=True, source="paper_facts",
                actions=None, blocks=None, suggestions=None, listed=None,
                scope=None, basis="", suggestion=""):
    state.outcome.conclusion = text
    state.outcome.basis = basis
    state.outcome.suggestion = suggestion
    state.outcome.found = found
    state.outcome.answer_source = source
    state.outcome.actions = actions or []
    state.outcome.blocks = blocks or []
    state.outcome.suggestions = suggestions or []
    if listed is not None:
        state.outcome.last_listed = listed
    if scope is not None:
        state.outcome.last_scope = scope
    state.outcome.history_answer = text


def _router(state):
    intent = state.intent.name
    if intent in PAPER_INTENTS and not state.has_paper:
        state.next = "paper_missing"
        return
    state.next = {
        "paper_overview": "paper_overview",
        "paper_detail": "paper_detail",
        "paper_fix": "paper_fix",
        "paper_basis": "paper_basis",
        "paper_coverage": "paper_coverage",
        "spec_qa": "spec_qa",
        "out_of_scope": "out_of_scope",
        "clarify": "clarify",
    }.get(intent, "spec_qa")


def _paper_overview(state):
    text, listed = facts.build_overview(state.paper_errors, state.paper_total)
    _set_answer(
        state,
        text,
        source="paper_facts",
        actions=[{"type": "open-result"}],
        blocks=blocks.overview_blocks(state.paper_errors, state.paper_total),
        suggestions=blocks.suggestions_for(
            "paper_overview", stats={"matched_total": int(state.paper_total or 0)}),
        listed=listed,
        scope={"scope": "all"},
    )


# 保持下来的筛选键（游标/页码单独记录，不混进过滤条件）
_SCOPE_KEYS = ("category", "rule_ids", "label", "severity", "categories")


def _highlight_action(selected):
    """高亮动作：除了规则号，还给出规则号+定位，前端才能只高亮那一条。"""
    rule_ids = []
    targets = []
    for issue in selected or []:
        rule_id = str(issue.get("rule_id") or "").strip()
        if rule_id and rule_id not in rule_ids:
            rule_ids.append(rule_id)
        location = facts._location(issue)
        target = {"rule_id": rule_id, "location": location}
        if rule_id and location and target not in targets:
            targets.append(target)
    action = {"type": "highlight-issues", "rule_ids": rule_ids[:20]}
    if targets:
        action["targets"] = targets[:20]
    return [action]


def _relative_slots(state, slots):
    """把「下一条/上一条」换成具体序号。

    返回 (slots, 状态)；状态取值：
    - "end"：已经到上一轮列表末尾，直接回事实文案；
    - "clarify"：游标还不够定位，走「你指的是哪一条」追问；
    - None：已换成具体序号。
    """
    direction = slots.get("direction")
    if not direction:
        return slots, None
    cursor = int((state.last_scope or {}).get("cursor") or 0)
    listed = state.last_listed or []
    if direction > 0 and listed and cursor >= len(listed):
        return {}, "end"
    index = cursor + int(direction)
    base = {key: (state.last_scope or {}).get(key) for key in _SCOPE_KEYS
            if (state.last_scope or {}).get(key) not in (None, "", [])}
    if index < 1:
        return {"scope": "index", "candidates": listed}, "clarify"
    base.update({"scope": "index", "index": index, "candidates": listed})
    return base, None


def _paper_items(state, mode):
    slots, relative = _relative_slots(state, dict(state.intent.slots or {}))
    if relative == "end":
        count = len(state.last_listed or [])
        text = (f"上一次列出的 {count} 条已经是最后一批了。\n"
                "可到「检测结果」页查看完整结果。")
        _set_answer(state, text, source="paper_facts",
                    actions=[{"type": "open-result"}],
                    blocks=blocks.text_blocks(text),
                    suggestions=blocks.suggestions_for("paper_detail"))
        return
    if relative == "clarify":
        state.next = "clarify"
        return
    if slots.get("scope") == "index" and not slots.get("index"):
        state.next = "clarify"
        return
    selected, reason, meta = facts.select_issues(state.paper_errors, slots, state.question)
    if not selected and reason:
        if slots.get("scope") == "index":
            state.next = "clarify"
            return
        if slots.get("scope") == "page":
            total = int(meta.get("matched_total") or 0)
            offset = int(meta.get("offset") or 0)
            if total and total > offset:
                text = (f"共 {total} 条；对话里最多逐条列出前 {facts.LAST_LISTED_LIMIT} 条，"
                        "其余请到「检测结果」页查看。")
            elif total:
                text = f"共 {total} 条，已经列到最后了。\n可到「检测结果」页查看完整结果。"
            else:
                text = facts._NO_MATCH
        else:
            text = f"没有找到与当前条件匹配的问题条目（{reason}）。\n可到「检测结果」页查看全部问题。"
        _set_answer(state, text, source="paper_facts",
                    actions=[{"type": "open-result"}],
                    blocks=blocks.text_blocks(text),
                    suggestions=blocks.suggestions_for("paper_detail"))
        return
    scope_slots = {key: slots.get(key) for key in ("scope",) + _SCOPE_KEYS
                   if slots.get(key) not in (None, "", [])}
    offset = int(meta.get("offset") or 0)
    start_number = None
    if slots.get("scope") == "index" and slots.get("index"):
        start_number = int(slots["index"])
        scope_slots["cursor"] = start_number
    elif slots.get("scope") == "page":
        scope_slots["page"] = int(meta.get("page") or 1)
        scope_slots["cursor"] = offset + len(selected)
    # 列表回答不预设游标：下一句「下一条」应当从这一批的第 1 条开始数

    if mode == "basis":
        state.outcome.trace.append("tool:basis_lookup")
        text, listed = facts.build_basis(selected, matched_total=meta.get("matched_total"))
        notes = facts.basis_notes(selected, matched_total=meta.get("matched_total"))
        _set_answer(
            state, text, source="paper_facts",
            actions=_highlight_action(selected),
            listed=_listed_out(state, slots, meta, listed),
            blocks=blocks.text_blocks(notes)
            + blocks.detail_blocks(selected, "依据对应的条目", start_number or offset + 1),
            suggestions=blocks.suggestions_for("paper_basis", scope_slots, meta),
            scope=scope_slots,
        )
        return

    if slots.get("mode") == "rule":
        state.outcome.trace.append("tool:rule_lookup")
        text, rule_ids, rule_reason = tools.rule_lookup(
            state.paper_errors, slots, state.question)
        if not text:
            text = (f"没有找到与当前条件匹配的规则号（{rule_reason}）。\n"
                    "可到「检测结果」页查看每条问题的规则号。")
            _set_answer(state, text, source="paper_facts",
                        actions=[{"type": "open-result"}],
                        blocks=blocks.text_blocks(text), scope=scope_slots)
            return
        _set_answer(
            state, text, source="paper_facts",
            actions=_highlight_action(selected),
            blocks=blocks.text_blocks(text, "对应规则")
            + blocks.detail_blocks(selected, start_index=offset + 1),
            suggestions=blocks.suggestions_for("paper_detail", scope_slots, meta),
            scope=scope_slots,
        )
        return
    text, listed = facts.build_detail(
        selected, mode=mode, matched_total=meta.get("matched_total"), offset=offset,
        start_number=start_number, category_counts=meta.get("category_counts"))
    lead = facts.detail_header_matched(
        meta.get("matched_total"), len(selected), offset, start_number,
        meta.get("category_counts"))
    footer = facts.detail_footer(meta.get("matched_total"), len(selected), offset)
    _set_answer(
        state, text, source="paper_facts", actions=_highlight_action(selected),
        listed=_listed_out(state, slots, meta, listed),
        blocks=blocks.text_blocks("\n".join(part for part in (lead, footer) if part))
        + blocks.detail_blocks(selected, start_index=start_number or offset + 1),
        suggestions=blocks.suggestions_for(
            "paper_fix" if mode == "fix" else "paper_detail", scope_slots, meta),
        scope=scope_slots,
    )


def _listed_out(state, slots, meta, listed):
    """写回会话的「上次列出顺序」。

    按键位回答（第 N 条）时沿用已有列表，否则“下一条”只会记得这一条；
    筛选/分页回答时用完整有序列表（上限见 facts.LAST_LISTED_LIMIT），
    卡片的 #11 才能用“第 11 条”指代。
    """
    full = list(meta.get("listed") or [])
    if slots.get("scope") == "index":
        return list(state.last_listed or []) or full or list(listed or [])
    return full or list(listed or [])


def _paper_detail(state):
    _paper_items(state, "detail")


def _paper_fix(state):
    _paper_items(state, "fix")


def _paper_basis(state):
    _paper_items(state, "basis")


def _paper_coverage(state):
    text = facts.build_coverage(state.paper_unchecked)
    _set_answer(
        state, text, source="paper_facts",
        actions=[{"type": "open-result"}],
        blocks=blocks.text_blocks(text),
        suggestions=blocks.suggestions_for("paper_coverage"),
    )


def _paper_missing(state):
    text = facts.build_missing_paper()
    _set_answer(state, text, found=False, source="none",
                blocks=blocks.text_blocks(text),
                suggestions=blocks.suggestions_for("paper_missing"))


def _clarify(state):
    slots = state.intent.slots or {}
    kind = slots.get("kind")
    if kind == "multi_question":
        options = [str(item) for item in (slots.get("options") or []) if str(item).strip()]
        if not options:
            _out_of_scope(state)
            return
        state.outcome.clarify = {
            "kind": "multi_question",
            "question": f"你一次问了 {len(options)} 个问题，先回答哪一个？",
            "options": options[:5],
        }
        state.outcome.found = True
        state.outcome.answer_source = "clarify"
        state.outcome.history_answer = state.outcome.clarify["question"]
        return
    if kind == "paper_index" or slots.get("scope") == "index":
        payload = facts.build_clarify_paper_index(state.paper_errors, state.last_listed)
    else:
        payload = facts.build_clarify_heading(slots.get("attribute"))
    if payload is None:
        if state.has_paper:
            # 检测过但当前没有条目（0 条问题，或问题已全部修复）：
            # 不能说“还没检测”，也不能编一条不存在的条目。
            _set_answer(
                state,
                "当前记录的问题为 0 条，没有可选的条号。\n可到「检测结果」页查看完整结果。",
                source="paper_facts",
                actions=[{"type": "open-result"}],
            )
        else:
            _paper_missing(state)
        return
    payload["original_intent"] = state.intent.name
    state.outcome.clarify = payload
    state.outcome.pending_clarify = payload
    state.outcome.found = True
    state.outcome.answer_source = "clarify"
    state.outcome.history_answer = payload["question"]


def _spec_qa(state, spec_answer):
    slots = state.intent.slots or {}
    question = state.question
    level = str(slots.get("heading_level") or "").strip()
    if level:
        attribute = str(slots.get("attribute") or "字体字号")
        question = f"{level}标题的{attribute}要求是什么？"
    state.outcome.trace.append("tool:spec_answer")
    result = spec_answer(question)
    state.outcome.spec_result = result
    state.outcome.found = bool(result.get("found"))
    state.outcome.answer_source = "spec_kb"
    # 规范链路自己写会话历史，这里不重复写
    state.outcome.history_answer = ""
    # 已消费的澄清：显式置空，由服务层落库
    if slots.get("clarify_answer"):
        state.outcome.pending_clarify = None


def _out_of_scope(state):
    text = facts.build_out_of_scope()
    _set_answer(state, text, found=False, source="out_of_scope",
                blocks=blocks.text_blocks(text),
                suggestions=blocks.suggestions_for("out_of_scope"))


def run_dialog(state, spec_answer, max_steps=MAX_STEPS):
    """驱动状态机。返回同一个 state（outcome 已填充）。

    护栏：步数超过 max_steps 时直接落规范问答，保证不会死循环。
    """
    max_steps = max(1, int(max_steps or 1))
    while state.next:
        state.steps += 1
        if state.steps > max_steps:
            # 兜底：不再跳转，直接交给规范链路
            state.outcome.trace.append("step_guard")
            _spec_qa(state, spec_answer)
            state.next = None
            break
        node = state.next
        state.next = None
        state.outcome.trace.append(node)
        if node == "router":
            _router(state)
        elif node == "paper_overview":
            _paper_overview(state)
        elif node == "paper_detail":
            _paper_detail(state)
        elif node == "paper_fix":
            _paper_fix(state)
        elif node == "paper_basis":
            _paper_basis(state)
        elif node == "paper_coverage":
            _paper_coverage(state)
        elif node == "paper_missing":
            _paper_missing(state)
        elif node == "clarify":
            _clarify(state)
        elif node == "spec_qa":
            _spec_qa(state, spec_answer)
        elif node == "out_of_scope":
            _out_of_scope(state)
        else:
            # 未知节点：保守走规范问答
            _spec_qa(state, spec_answer)
    return state
