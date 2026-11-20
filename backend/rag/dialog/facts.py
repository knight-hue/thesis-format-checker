"""事实层：论文类答案只由检测结果字段拼装。

硬约束（项目规矩）：
- 学生可见的每一句话都要能由 issue 字段或 paper_total 推出；
- 不出现「通常/一般/大约/估计」这类推断词；
- 不把 unchecked 说成「已通过」；
- 数字只来自总数、分类计数；列表序号与「前 N 条」属展示常量。
"""
from collections import Counter

from ..session_manager import MAX_LISTED_ENTRIES, _recall, format_location

OVERVIEW_LIMIT = 10
DETAIL_LIMIT = 3
PAGE_SIZE = 10
LAST_LISTED_LIMIT = MAX_LISTED_ENTRIES
CLARIFY_CANDIDATES = 5

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}

_NO_SUGGESTION = "该条不提供自动修改，需按规范手工调整。"
_FIX_NOTE = "系统不会自动修改，请在「检测结果」页勾选后使用修复下载。"
_NO_MATCH = "没有找到与当前条件匹配的问题条目。\n可到「检测结果」页查看全部问题。"


def _severity(issue):
    return _SEVERITY_ORDER.get(str(issue.get("severity") or ""), 9)


def _location(issue):
    location = issue.get("location")
    # 会话里存的是 summarize_issues 生成的字符串；原始 issue 可能是定位对象
    if isinstance(location, str):
        return location.strip() or "位置未标注"
    return format_location(location) or "位置未标注"


def _sort_issues(issues):
    return sorted(issues, key=lambda item: (_severity(item), str(item.get("rule_id") or "")))


def _message(issue):
    return str(issue.get("message") or "").strip() or str(issue.get("rule_id") or "")


def _expected(issue):
    return str(issue.get("expected") or "").strip()


def _suggestion(issue):
    return str(issue.get("suggestion") or "").strip()


def _actual(issue):
    value = issue.get("actual")
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value).strip()
    return ""


def _basis(issue):
    """模板原句。只取检测结果里的字段，不补写任何模板文字。"""
    return str(issue.get("basis") or "").strip()


def _category(issue):
    return str(issue.get("category") or "").strip() or "其他"


def _rule_id(issue):
    return str(issue.get("rule_id") or "").strip()


def _entry(issue):
    """写进会话的「上一次列出顺序」条目，只放可复核的定位与规则号。"""
    return {
        "rule_id": _rule_id(issue),
        "location": _location(issue),
        "message": _message(issue),
    }


def _listed(ordered, limit=LAST_LISTED_LIMIT):
    return [_entry(item) for item in ordered[:limit]]


def _meta(matched_total, selected, listed, offset=0, page=1, counts=None):
    """一次选择的统计信息：总数只用于“共 N 条/还有更多”的文案。"""
    matched_total = int(matched_total or 0)
    return {
        "matched_total": matched_total,
        "selected_count": len(selected),
        "offset": int(offset),
        "page": int(page),
        "has_more": matched_total > int(offset) + len(selected),
        "listed": listed,
        # 多类目回答的分类计数：让“图 N 条、表 M 条”在答复里可见，不靠推断
        "category_counts": dict(counts or {}),
    }


def _in_group(issue, group):
    if not isinstance(group, dict):
        return False
    category = str(group.get("category") or "").strip()
    if category and _category(issue) == category:
        return True
    return _rule_id(issue) in {str(item) for item in (group.get("rule_ids") or []) if str(item)}


def _filter(issues, slots):
    """按类目/规则号/严重级别过滤；categories 是多个类目的并集。"""
    filtered = issues
    groups = [group for group in (slots.get("categories") or [])
              if isinstance(group, dict)]
    if groups:
        categories = {str(group.get("category") or "").strip() for group in groups}
        categories.discard("")
        rule_ids = {str(item) for group in groups
                    for item in (group.get("rule_ids") or []) if str(item)}
        filtered = [
            item for item in filtered
            if _category(item) in categories or _rule_id(item) in rule_ids
        ]
        return filtered

    category = str(slots.get("category") or "").strip()
    rule_ids = [str(item) for item in (slots.get("rule_ids") or []) if str(item)]
    severity = str(slots.get("severity") or "").strip()
    if category:
        filtered = [item for item in filtered if _category(item) == category]
    if rule_ids:
        filtered = [item for item in filtered if _rule_id(item) in rule_ids]
    if severity:
        filtered = [item for item in filtered
                    if str(item.get("severity") or "") == severity]
    return filtered


def build_overview(issues, total):
    """概览：总数 + 分类计数 + 最前面的若干条。返回 (text, listed)。"""
    issues = [item for item in (issues or []) if isinstance(item, dict)]
    total = int(total or 0)
    listed = [_entry(item) for item in _sort_issues(issues)[:LAST_LISTED_LIMIT]]

    if total <= 0:
        return ("当前记录的问题为 0 条。\n"
                "可到「检测结果」页查看本次检测的完整结果。"), []

    stored = len(issues)
    note = ""
    if stored < total:
        note = f"（本次统计基于已保存的 {stored} 条）"

    counts = Counter(_category(item) for item in issues)
    lines = [f"这篇论文共检测出 {total} 条问题{note}。", "按类别统计："]
    for category, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])):
        lines.append(f"- {category}：{count} 条")

    top = _sort_issues(issues)[:OVERVIEW_LIMIT]
    if top:
        lines.append(f"以下列出最前面的 {len(top)} 条：")
        for index, issue in enumerate(top, 1):
            line = f"{index}. {_location(issue)}：{_message(issue)}"
            expected = _expected(issue)
            if expected:
                line += f"（规范要求：{expected}）"
            lines.append(line)

    lines.append("可到「检测结果」页查看全部问题并勾选需要修复的条目。")
    return "\n".join(lines), listed


def select_issues(issues, slots, query=""):
    """按槽位选条目。返回 (选中列表, 说明, 统计信息)。

    统计信息里的 matched_total 是**满足条件的全部条数**，不是本次列出的条数；
    文案据此区分「只有 3 条」与「共 20 条，先列 3 条」。

    scope=index 时优先用澄清候选；scope=page 时按 10 条一页继续往后看；
    scope=all 时用 BM25 按当前问题召回前几条。
    """
    issues = [item for item in (issues or []) if isinstance(item, dict)]
    slots = slots or {}
    scope = slots.get("scope") or "all"

    if scope == "index":
        candidates = slots.get("candidates") or []
        number = int(slots.get("index") or 0)
        if number <= 0:
            return [], "缺少序号", _meta(len(issues), [], _listed(_sort_issues(issues)))
        if candidates:
            if number > len(candidates):
                return [], "序号超出候选范围", _meta(len(candidates), [], list(candidates))
            target = candidates[number - 1]
            rule_id = str(target.get("rule_id") or "")
            location = str(target.get("location") or "")
            matched = [
                item for item in issues
                if _rule_id(item) == rule_id and _location(item) == location
            ]
            if matched:
                return matched[:1], "", _meta(1, matched[:1], list(candidates))
            return [], "该条已不在当前检测结果里", _meta(len(candidates), [], list(candidates))
        if number > len(issues):
            return [], "序号超出问题条数", _meta(len(issues), [], _listed(_sort_issues(issues)))
        ordered = _sort_issues(issues)
        return ([ordered[number - 1]], "",
                _meta(len(ordered), ordered[number - 1:number], _listed(ordered)))

    filtered = _filter(issues, slots)

    if scope == "page":
        ordered = _sort_issues(filtered)
        page = max(1, int(slots.get("page") or 1))
        # 第一次只列前 3 条；「继续看」从第 4 条开始每页 10 条，不跳过中间条目
        offset = 0 if page <= 1 else DETAIL_LIMIT + (page - 2) * PAGE_SIZE
        if offset >= LAST_LISTED_LIMIT:
            return ([], "对话里可逐条指代的条目已到上限",
                    _meta(len(ordered), [], _listed(ordered), offset=offset, page=page))
        selected = ordered[offset:offset + PAGE_SIZE]
        reason = ""
        if not selected:
            reason = "没有更多条目了" if filtered else "没有匹配的条目"
        return (selected, reason,
                _meta(len(ordered), selected, _listed(ordered), offset=offset, page=page))

    if scope == "categories":
        groups = [group for group in (slots.get("categories") or [])
                  if isinstance(group, dict)]
        ordered = _sort_issues(filtered)
        counts = Counter(_category(item) for item in ordered)
        if len(groups) >= 2:
            # 每个类目先各取 1 条，再按排序补足：否则「图题和表题」可能全被图占满
            selected = []
            used = set()
            for group in groups:
                for item in ordered:
                    if _in_group(item, group) and id(item) not in used:
                        selected.append(item)
                        used.add(id(item))
                        break
            for item in ordered:
                if len(selected) >= DETAIL_LIMIT:
                    break
                if id(item) not in used:
                    selected.append(item)
                    used.add(id(item))
            selected = selected[:DETAIL_LIMIT]
            # 列出顺序与卡片序号一致：先已选条目，再剩余条目
            listed_issues = selected + [item for item in ordered
                                        if id(item) not in {id(sel) for sel in selected}]
        else:
            selected = ordered[:DETAIL_LIMIT]
            listed_issues = ordered
        return (selected, "" if selected else "没有匹配的条目",
                _meta(len(ordered), selected, _listed(listed_issues), counts=counts))

    if scope in ("category", "rule", "severity"):
        ordered = _sort_issues(filtered)
        selected = ordered[:DETAIL_LIMIT]
        return (selected, "" if selected else "没有匹配的条目",
                _meta(len(ordered), selected, _listed(ordered)))

    recalled, _mode = _recall(issues, query, DETAIL_LIMIT)
    recalled = list(recalled)
    return (recalled, "" if recalled else "没有可召回的问题",
            _meta(len(recalled), recalled, [_entry(item) for item in recalled]))


def detail_header_matched(matched_total, shown, offset=0, start_number=None, counts=None):
    """详情类回答的头一句话：总数/区间/分类计数。只由数据推出。"""
    matched_total = int(matched_total or 0)
    shown = int(shown or 0)
    offset = int(offset or 0)
    if start_number and shown == 1:
        return f"第 {int(start_number)} 条："
    prefix = ""
    counts = counts or {}
    if counts:
        parts = [f"{name} {count} 条" for name, count in
                 sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))]
        prefix = "、".join(parts) + "；"
    if offset > 0:
        base = f"第 {offset + 1}–{offset + shown} 条（共 {matched_total} 条）："
    elif matched_total > shown:
        base = f"共 {matched_total} 条，先列前 {shown} 条："
    else:
        base = f"命中 {shown} 条："
    return prefix + base


def detail_footer(matched_total, shown, offset=0):
    if int(matched_total or 0) > int(offset or 0) + int(shown or 0):
        return "回复「继续看」可看下一批。"
    return ""


def build_detail(issues, mode="detail", matched_total=None, offset=0,
                 start_number=None, category_counts=None):
    """逐条答案。mode=fix 时把修改建议放在更显眼的位置。返回 (text, listed)。"""
    items = [item for item in (issues or []) if isinstance(item, dict)]
    if not items:
        return (_NO_MATCH, [])

    total = len(items) if matched_total is None else int(matched_total or 0)
    offset = max(0, int(offset or 0))
    header = detail_header_matched(total, len(items), offset, start_number,
                                   category_counts)
    single_index = bool(start_number) and len(items) == 1

    lines = [header]
    for index, issue in enumerate(items, offset + 1):
        prefix = "" if single_index else f"{index}. "
        lines.append(f"{prefix}{_location(issue)}：{_message(issue)}")
        expected = _expected(issue)
        if expected:
            lines.append(f"   规范要求：{expected}")
        actual = _actual(issue)
        if actual:
            lines.append(f"   实测：{actual}")
        suggestion = _suggestion(issue)
        if suggestion:
            lines.append(f"   修改建议：{suggestion}")
        elif mode == "fix":
            lines.append(f"   {_NO_SUGGESTION}")
    footer = detail_footer(total, len(items), offset)
    if footer:
        lines.append(footer)
    if mode == "fix":
        lines.append(_FIX_NOTE)
    return "\n".join(lines), [_entry(item) for item in items[:LAST_LISTED_LIMIT]]


def build_missing_paper():
    """会话里没有问题摘要时的固定文案：不检索、不生成、不给数字。"""
    return ("还没有可用的检测结果。请先上传论文并完成检测，再来问论文里的具体问题。\n"
            "如果你想了解规范本身的要求，也可以直接问，例如「一级标题应该用什么字号」。")


def build_basis(issues, matched_total=None):
    """模板依据：逐条给出检测结果里记录的模板原句。

    依据为空时**不说**“符合模板”，只如实说明这次结果没有记录原句，
    不拿一般排版常识补位。返回 (text, listed)。
    """
    items = [item for item in (issues or []) if isinstance(item, dict)]
    if not items:
        return (_NO_MATCH, [])

    total = len(items) if matched_total is None else int(matched_total or 0)
    lines = [detail_header_matched(total, len(items), 0)]
    missing = 0
    for index, issue in enumerate(items, 1):
        basis = _basis(issue)
        if not basis:
            missing += 1
            continue
        head = f"{index}. {_location(issue)}"
        rule_id = _rule_id(issue)
        if rule_id:
            head += f"（{rule_id}）"
        lines.append(f"{head}：{basis}")

    if len(lines) == 1:
        return ("这次检测结果里没有记录这些条目对应的模板原句，无法给出依据。\n"
                "可到「检测结果」页按规则号核对模板条款。"), []
    if missing:
        lines.append(f"其余 {missing} 条没有记录模板原句，可到「检测结果」页按规则号核对。")
    return "\n".join(lines), [_entry(item) for item in items[:LAST_LISTED_LIMIT]]


def basis_notes(issues, matched_total=None):
    """依据回答的抬头/尾注（卡片里已经带逐条原句，这里只补总数与缺失说明）。"""
    items = [item for item in (issues or []) if isinstance(item, dict)]
    if not items:
        return ""
    total = len(items) if matched_total is None else int(matched_total or 0)
    lines = [detail_header_matched(total, len(items), 0)]
    missing = sum(1 for item in items if not _basis(item))
    if missing == len(items):
        lines.append("这次检测结果里没有记录这些条目对应的模板原句，无法给出依据。")
    elif missing:
        lines.append(f"其余 {missing} 条没有记录模板原句，可到「检测结果」页按规则号核对。")
    return "\n".join(lines)


def build_coverage(unchecked):
    """这次**没有执行**的条款清单。

    unchecked 为 None 表示会话里没有保存过清单（旧版本检测结果），
    此时只能说“没有记录”，不得说成“都已检查”。
    """
    if unchecked is None:
        return ("会话里没有保存这次的「未执行条款」清单（可能是旧版本的检测结果）。\n"
                "请到「检测结果」页查看本次的未检查项；没记录不等于查过。")
    items = [item for item in unchecked if isinstance(item, dict)]
    if not items:
        return ("本次检测没有记录未执行的条款。\n"
                "这只说明没有未检项记录，具体覆盖范围以「检测结果」页为准。")
    lines = [f"本次有 {len(items)} 条条款没有执行（模板要求，但当前能力做不到）："]
    for index, item in enumerate(items, 1):
        rule_id = str(item.get("rule_id") or "").strip()
        reason = str(item.get("reason") or "").strip()
        line = f"{index}. {rule_id}：{reason}" if rule_id else f"{index}. {reason}"
        lines.append(line)
    lines.append("这些条目只代表没查，不等于通过，请人工核对。")
    return "\n".join(lines)


def build_out_of_scope():
    return ("抱歉，您的问题超出了论文格式规范的范围，我只能回答与论文格式相关的问题。\n"
            "您可以问我：这篇论文有哪些问题、某类问题怎么改，或某条规定具体怎么写。")


def build_clarify_paper_index(issues, last_listed=None):
    """序号缺失时的追问：给出候选，选项是可直接回填的「第 N 条」。"""
    issues = [item for item in (issues or []) if isinstance(item, dict)]
    listed = list(last_listed or [])
    if not listed:
        listed = [_entry(item) for item in _sort_issues(issues)[:CLARIFY_CANDIDATES]]
    if not listed:
        return None
    candidates = listed[:CLARIFY_CANDIDATES]
    lines = ["你指的是哪一条？"]
    for index, item in enumerate(candidates, 1):
        message = str(item.get("message") or "").strip()
        location = str(item.get("location") or "").strip()
        lines.append(f"{index}. {location}：{message}" if location else f"{index}. {message}")
    return {
        "kind": "paper_index",
        "question": "\n".join(lines),
        "options": [f"第 {index} 条" for index in range(1, len(candidates) + 1)],
        "candidates": candidates,
    }


def build_clarify_heading(attribute):
    attribute = attribute or "字体字号"
    return {
        "kind": "heading_level",
        "question": f"你问的是哪一级标题的{attribute}？",
        "options": ["一级标题", "二级标题", "三级标题"],
        "attribute": attribute,
    }
