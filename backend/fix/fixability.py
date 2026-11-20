# -*- coding: utf-8 -*-
"""可修复性判定：告诉前端某条 issue 后端能否自动修复，以及不能修复的原因。

设计意图：
    检测结果（/api/check-file、/api/check）直接给出每条 issue 的
    ``fixable``（bool）与 ``fix_reason``（不可修复时的原因），前端据此
    禁用勾选框并展示原因，不再自己维护一份“能否修复”的白名单。
判定口径：
    「静态能力」——只依据 rule_id / source / location.type 判断后端
    fix_document 是否存在对应处理通道；不预测运行时是否真的改成功
    （如 block 越界、段落内容变化、期望值为空等执行期 skip 仍以后端
    返回的 X-Skipped-Count / skipped reason 为准）。唯一的运行时前提是
    llm_available：AI 服务未就绪时语义改写通道整体不可用。
"""
from .fix_blocks import (
    _TEXT_LAYER_RULE_IDS,
    _STRUCTURAL_RULE_IDS,
    _resolve_format,
)
from backend.detection.semantic_checker import (
    MANUAL_ONLY_SEMANTIC_RULES,
    REWRITABLE_SEMANTIC_RULES,
)

# 公式的专用修复通道（对应 fix_blocks._apply_formula_fix）。
_FORMULA_RULE_IDS = {
    "formula.number_format",
    "formula.number_continuity",
    "formula.number_position",
    "formula.dotted_line",
}

# 关键词数量类规则的专属说明：修复要增删关键词，属于作者的语义决策，确定性改写
# 做不了。明确给出原因，避免落到「暂无自动修复映射」这句像在等排期的兜底话术上。
_MANUAL_ONLY_RULE_IDS = {
    "keyword.count": "关键词数量需人工增删，不自动改写",
    "en_keyword.count": "英文关键词数量需人工增删，不自动改写",
    "table.continued_note_in_caption": (
        "续表标注要单独成段放在续表左上方，程序不改动表题文字，请手工移出"),
}


def classify_fixability(issue, llm_available=True):
    """返回 (fixable, reason)。fixable=False 时 reason 为不可修复原因。

    llm_available=False 表示当前没有可用的 AI 服务，语义改写通道不可用。
    """
    rule_id = issue.get("rule_id", "") or ""
    src = issue.get("source")
    loc = issue.get("location") or {}
    loc_type = loc.get("type")

    # 0) 编号来自 Word 自动编号（numbering.xml）：判定能从 numbering.xml 还原出
    #    编号，但修复层只改段落文本/runs，改不了编号定义；留给人工或让用户改用
    #    手工编号，不能承诺“一键修”。
    if issue.get("auto_number"):
        # 编号由 numbering.xml 渲染：写回层改该级 numFmt（如 chineseCounting → decimal），
        # 保留 Word 自动编号，而不是把编号写进段落文字。
        return True, ""

    # 1) 语义类（source="rag"）：仅白名单内可文本改写，且必须有 AI 服务。
    if src == "rag":
        if rule_id in MANUAL_ONLY_SEMANTIC_RULES:
            return False, MANUAL_ONLY_SEMANTIC_RULES[rule_id]
        if rule_id not in REWRITABLE_SEMANTIC_RULES:
            return False, "该语义规则不提供自动文本改写"
        if not llm_available:
            return False, "AI 服务不可用，语义类问题暂不自动改写"
        return True, ""

    # 2) 无 source：前端注入的图片类修复项。
    if not src:
        if rule_id in ("image.replace", "image.insert"):
            return True, ""
        return False, "未知来源且无对应修复通道"

    # 3) 规则引擎（source="rule_engine"）。
    if src != "rule_engine":
        return False, "未知问题来源，不自动修复"

    # 3.1 只能人工处理的规则（数量类）：检出但无确定性改写通道。
    if rule_id in _MANUAL_ONLY_RULE_IDS:
        return False, _MANUAL_ONLY_RULE_IDS[rule_id]

    # 3.2 结构性规则：由结构层统一处理，与定位类型无关。
    if rule_id in _STRUCTURAL_RULE_IDS:
        return True, ""

    # 3.3 文字类确定性规则：仅段落定位可修（文字层逐段改写）。
    if rule_id in _TEXT_LAYER_RULE_IDS:
        if loc_type == "paragraph":
            return True, ""
        return False, f"文字类规则定位非段落（type={loc_type}），不自动修复"

    # 3.4 按定位类型分派到格式层通道。
    if loc_type == "section":
        if rule_id.startswith("page.") and rule_id.endswith("_cm"):
            return True, ""
        return False, "页面设置规则暂无自动修复映射"

    if loc_type == "table_cell":
        # 表内可自动改的只有模板写了的那两项：中文字体（§5.5）与字号（§5.5）。
        # 表内西文/数字用什么字体，模板在图表那一章没有说明句（只有 §4.3 正文那句），
        # 故 table.cell_latin_font 已撤销、不再出现在这里。
        if rule_id in ("table.cell_east_asia_font", "table.cell_font",
                       "table.cell_font_size"):
            return True, ""
        return False, "表格单元格规则暂无自动修复映射"

    if loc_type == "image":
        if rule_id == "image.replace":
            return True, ""
        # 位图的图内文字改不了（图内文字是像素：字体名无处可读、字号无法从像素可靠
        # 反推，检测层因此不报这类 issue）；这里保留兜底：万一真收到图内这类问题，
        # 给出可执行的具体原因，不落到“暂无自动修复映射”这句像在等排期的兜底话术上。
        if rule_id.startswith("figure.inner"):
            return False, ("图内文字是位图，程序无法直接修改；请在绘图/制图工具里把它"
                           "改成宋体五号后重新插入")
        return False, "图片规则暂无自动修复映射"

    if loc_type == "paragraph":
        if rule_id in ("figure.caption_number", "table.caption_number"):
            return True, ""
        if rule_id.startswith("formula."):
            if rule_id == "formula.alignment":
                # 走通用格式的 alignment 后缀。
                return (True, "") if _resolve_format(rule_id) is not None \
                    else (False, "公式规则暂无自动修复映射")
            if rule_id in _FORMULA_RULE_IDS:
                return True, ""
            return False, "公式规则暂无自动修复映射"
        if _resolve_format(rule_id) is not None:
            return True, ""
        return False, "暂无自动修复映射"

    return False, f"非可写回定位（type={loc_type}）"


def annotate_issues(issues, llm_available=True):
    """就地给每条 issue 标注 fixable / fix_reason，返回原列表。

    llm_available=False（无 AI 服务）时，语义类 issue 一律标为不可修复：语义改写
    通道必须走 rag_service，此时检测仍会报出问题，只是不能自动改写。
    """
    for issue in issues:
        fixable, reason = classify_fixability(issue, llm_available=llm_available)
        issue["fixable"] = fixable
        if fixable:
            issue.pop("fix_reason", None)
        else:
            issue["fix_reason"] = reason
    return issues
