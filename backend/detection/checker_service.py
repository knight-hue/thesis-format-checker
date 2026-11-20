"""检测编排层：串起规则引擎与语义检查器，输出统一的检测结果。

流程：
    1. RuleEngine.for_college(college_id).check(document)   —— 确定性规则校验
    2. SemanticChecker(rag_service, rules=engine.rules).check(...) —— 语义校验（可选）

第 2 步的语义层分三类：零 LLM 的确定性条款（目录/致谢/参考文献/列表序号/单位/数字）
不依赖 rag_service；术语先走确定性 gate，只有残差才需要 AI 服务；坐标轴等定位类条款
需要 AI 服务，rag_service 为 None 时整组跳过，并在 breakdown.semantic.llm_enabled
标记 False。
语义层与规则引擎共用同一份 rules 配置（目录必需组成部分等取值以 rules 为准），
报告里的 `basis` 也统一成模板里那句说明（依据表 `config/rules/<学校>/<学院>/<college>_basis.json`）。

输出结构：
    {
        "schema_version": "1.0",
        "college_id": "jlu_cs",
        "passed": bool,          # 两个来源全部通过才为 True
        "total_issues": int,     # 两个来源 issue 之和
        "issues": [...],         # 规则引擎 issue（source="rule_engine"）+ 语义 issue（source="rag"）
                                 # 每条都带 basis（模板原句）；语义层原有的说明在 basis_detail
        "breakdown": {
            "rule_engine": {"passed", "total_issues"},
            "semantic":    {"enabled", "llm_enabled", "passed", "total_issues",
                            "llm_calls", "cache_hits", "prefilter_skips"},
        },
    }
"""
from backend.college_registry import RULES_ROOT
from backend.detection.rule_engine import RuleEngine
from backend.detection.semantic_checker import SemanticChecker

# 语义规则按学院启停：值是该要求在本院 config/rules/*.json 里的键路径。
# 路径不存在 ⇒ 这个学院的模板没有写这句说明，整条不查（与顺序编码制同一口径：
# 计算机模板有“文献类型标识：专著[M]、…”，软院模板参考文献部分只有“字体字号”一句）。
SEMANTIC_RULE_GATES = {
    "reference.format": ("references", "type_mark_text"),
}

# 由在线 AI 判定、离线复核不了的规则：保留报告，但标“仅供参考”且不计入问题总数。
# 术语的确定性结论（source=rule_engine）进 issues；只有模型残差建议（source=rag）
# 仍进 advisory_issues，避免把 42.9% 的模型猜测当成确定结论。
ADVISORY_RULE_IDS = {"terminology"}

# pending_review 是给人看的队列，不能无限增长；超出时截断并在 unchecked 说明。
MAX_PENDING_REVIEW = 200


def _cap_pending_review(items, limit=MAX_PENDING_REVIEW):
    """返回 (保留项, 被截断数量)。"""
    items = list(items or [])
    if len(items) <= limit:
        return items, 0
    return items[:limit], len(items) - limit


def _split_advisory(issues: list[dict]) -> tuple[list[dict], list[dict]]:
    """把“AI 判断、离线复核不了”的报告从总数里分出来。

    术语目前在正文+摘要里找“首次出现”，机制比模板窄；判也是在线 AI 判的。
    按定好的口径：报告里保留、标“AI 判断，仅供参考”，但**不计入问题总数**。

    返回 (counted, advisory)。分开放而不是同列表打标：报告的自洽规则是
    `total_issues == len(issues)`（前端也会拿 total 与清单长度对齐），把
    “不计入总数”的条目留在 issues 里会让两者对不上。
    """
    counted: list[dict] = []
    advisory: list[dict] = []
    for issue in issues:
        if issue.get("source") == "rag" and issue.get("rule_id") in ADVISORY_RULE_IDS:
            issue["advisory"] = True
            message = str(issue.get("message") or "")
            if not message.startswith("AI 判断，仅供参考"):
                issue["message"] = "AI 判断，仅供参考：" + message
            advisory.append(issue)
        else:
            counted.append(issue)
    return counted, advisory


def _semantic_rule_enabled(rule_id, rules):
    path = SEMANTIC_RULE_GATES.get(rule_id)
    if not path:
        return True
    node = rules
    for name in path:
        if not isinstance(node, dict) or name not in node:
            return False
        node = node[name]
    return bool(node)


def _apply_template_basis(issues, engine):
    """让每条报告的 `basis` 都是模板里那句说明（依据表按最长前缀取）。

    规则引擎自己已经挂好了；语义层的规则（`numerals`/`list.numbering`/
    `acknowledgement.content` 等）原先带的是“确定性检查：正则匹配”这类内部说明，
    对学生没有解释力，所以这里统一替换成模板原句，原来的说明挪到 `basis_detail`
    保留下来（不丢信息）。
    """
    for issue in issues:
        sentence = engine.basis_of(issue.get("rule_id"))
        if not sentence:
            continue
        current = issue.get("basis")
        if current and current != sentence:
            issue["basis_detail"] = current
        issue["basis"] = sentence


def _unchecked_items(document: dict, include_semantic: bool, rag_service,
                     engine) -> list[dict]:
    """本次**没有执行**的条款清单（能力缺口显式化）。

    模板里写着、当前环境做不到的检查不能静默消失，否则学生以为“全查过了”。
    目前有两类：

    1. 「图内字体字号：宋体五号字」对**位图**判不了——图内文字在图片上只是像素，
       字体名无处可读、字号也无法从像素可靠反推（两条理由见
       `detection/figure_image/service.py`）。这里只说“这条没查、请人工核对”，
       **不给任何估计值**：曾给过，实测那些数字量的是行结构高度而非字高，
       属于报错与事实不符。
    2. 「坐标轴：进行说明 / 坐标单位」由 AI 语义规则 `formula_axis` 负责；
       没有 AI 服务时这条整组不跑，同样要说明。
    """
    items: list[dict] = []

    if engine.rules.get("figures", {}).get("inner_content") and document:
        try:
            from .figure_image import service as figure_service

            summary = figure_service.summarize(document.get("blocks") or [])
            if summary.get("reason"):
                items.append({"rule_id": "figure.inner_font_size",
                              "reason": summary["reason"]})
        except Exception:  # noqa: BLE001 - 辅助信息，绝不影响检测本身
            items.append({"rule_id": "figure.inner_font_size",
                          "reason": "图内文字能力说明生成失败（内部错误），"
                                    "模板要求「图内字体字号：宋体五号字」，请人工核对。"})

    # 坐标轴（§5.7「坐标轴：进行说明；坐标单位：有数字标注的坐标图注明坐标单位」）由
    # AI 语义规则 formula_axis 负责，属"定位类"规则：没有 AI 服务时整组不跑。
    # 是否要在这里说明，取决于**这个学院的模板有没有写这句**——依据表里有原句才算
    # "模板要求了它"（口径同 SEMANTIC_RULE_GATES：原句不存在 ⇒ 该院模板没写，
    # 整条不查，也就无所谓"未检"）。
    # 踩过的坑：原条件写的是 `rules["formula"]["axis"]`，而两份规则文件里**都没有**
    # 这个键，条件恒为假 —— 这条"未检说明"从来不会出现，坐标轴就成了静默漏检。
    if include_semantic and rag_service is None and engine.basis_of("formula_axis"):
        items.append({"rule_id": "formula_axis",
                      "reason": "坐标轴的说明与单位需要 AI 语义判断，当前未启用 AI 服务"
                                "（模板要求「坐标轴：进行说明；坐标单位：有数字标注的"
                                "坐标图注明坐标单位」），请人工核对。写在图注或正文里"
                                "的说明文字，改用允许处理论文内容的 AI 服务后可自动核对；"
                                "画在图片内部的坐标文字不在自动核对范围内。"})

    # 正文标点的能力边界：只查了能零误报的那几类，剩下的半角/全角不判。
    # 不能把“没查”说成“没问题”，也不能把半角方括号（正文里多是引用序号 [1]）
    # 当标点报——这一半做不到，就写明白是哪一半。
    if engine.rules.get("body", {}).get("check_fullwidth_punctuation"):
        items.append({
            "rule_id": "body.fullwidth_punct",
            "reason": "正文标点只查了逗号、分号、冒号、感叹号、问号、句号、"
                      "省略号、双引号与圆括号；方括号、破折号、书名号的"
                      "半角/全角未判（正文里的半角方括号多是引用序号），请人工核对。"})

    # 图表与说明文字“相隔过远”没有模板阈值：不给估算距离，也不让 AI 猜。
    # “是否排在首次提及之后”已由规则引擎 figure/table.placement 判过。
    if document:
        blocks = [b for b in (document.get("blocks") or []) if isinstance(b, dict)]
        has_figure = any(
            b.get("type") == "image" and not b.get("inline")
            and not b.get("front_matter") for b in blocks)
        has_table = any(b.get("type") == "table" for b in blocks)
        if has_figure:
            items.append({"rule_id": "figure.placement",
                          "reason": "图与说明文字是否相隔过远，模板没有给出距离标准，"
                                    "本次未判（图是否排在首次提及之后已判），请人工核对。"})
        if has_table:
            items.append({"rule_id": "table.placement",
                          "reason": "表与说明文字是否相隔过远，模板没有给出距离标准，"
                                    "本次未判（表是否排在首次提及之后已判），请人工核对。"})
    return items


def check_document(document: dict, college_id: str, rag_service=None,
                   include_semantic: bool = True,
                   rules_dir: str = RULES_ROOT,
                   semantic_skip_reason: str = "",
                   terminology_strict: bool = False,
                   terminology_b04_domain_exempt: bool = False) -> dict:
    """对一篇论文解析结果执行完整检测。

    - document:         论文解析契约 dict（blocks/sections）
    - college_id:       学院 ID，如 jlu_cs
    - rag_service:      RAGService 实例；为 None 时语义层只跑零 LLM 的确定性条款
    - include_semantic: 是否执行语义检查；为 False 时只跑确定性规则引擎
    - rules_dir:        规则文件目录
    - semantic_skip_reason: 语义定位条款未执行的原因（privacy / unavailable / initializing /
      disabled），只用于向用户解释，不参与判定
    - terminology_strict: 严格模式，AI 等条件豁免词仍要求首次解释
    - terminology_b04_domain_exempt: 允许 B04 医学词条按论文实际领域豁免
    """
    if not document or not isinstance(document, dict):
        raise ValueError("document 不能为空，且必须为 dict")

    # 1) 确定性规则引擎（无外部依赖）
    engine = RuleEngine.for_college(college_id, rules_dir=rules_dir)
    rule_result = engine.check(document)
    for issue in rule_result["issues"]:
        issue["source"] = "rule_engine"

    # 2) 语义检查（可选）：确定性条款零 LLM 始终执行；定位条款需要 AI 服务
    semantic_result = None
    if include_semantic:
        checker = SemanticChecker(rag_service)
        semantic_result = checker.check(
            document, college_id,
            terminology_strict=terminology_strict,
            terminology_b04_domain_exempt=terminology_b04_domain_exempt,
        )
        # 本院模板没写这句说明的语义条款整条丢掉，统计口径跟着调整
        semantic_result["issues"] = [
            issue for issue in semantic_result["issues"]
            if _semantic_rule_enabled(issue.get("rule_id"), engine.rules)
        ]
        semantic_result["pending_review"] = [
            item for item in (semantic_result.get("pending_review") or [])
            if _semantic_rule_enabled(item.get("rule_id"), engine.rules)
        ]
        semantic_result["total_issues"] = len(semantic_result["issues"])
        semantic_result["passed"] = not semantic_result["issues"]

    issues = list(rule_result["issues"])
    if semantic_result:
        issues.extend(semantic_result["issues"])
    _apply_template_basis(issues, engine)

    counted, advisory = _split_advisory(issues)
    passed = not counted
    pending_review, dropped_pending = _cap_pending_review(
        semantic_result.get("pending_review") if semantic_result else [])
    # IssueResponse.decision 默认 confirmed；确定性语义规则没有显式写 decision，
    # 也应按 confirmed 计入，否则计数会漏掉列表序号等确定性条款。
    semantic_confirmed = (sum(
        1 for issue in semantic_result.get("issues") or []
        if issue.get("decision", "confirmed") == "confirmed")
        if semantic_result else 0)

    return {
        "schema_version": "1.0",
        "college_id": college_id,
        "passed": passed,
        "total_issues": len(counted),
        "issues": counted,
        # “AI 判断，仅供参考”的条目单独列出：不计入 total_issues，也不混在 issues 里，
        # 否则前端显示的总数与清单长度会对不上。
        "advisory_issues": advisory,
        # 三态契约：规则与模型都无法判定的条目；不计入 total_issues / passed。
        "pending_review": pending_review,
        # 未执行的条款（能力缺口）：报告里要显示，不能静默。
        "unchecked": _unchecked_items(document, include_semantic, rag_service, engine) + [
            item for item in (semantic_result or {}).get("unchecked", [])
            if _semantic_rule_enabled(item.get("rule_id"), engine.rules)
        ] + ([{
            "rule_id": "terminology",
            "reason": f"待人工条目过多，仅返回前 {MAX_PENDING_REVIEW} 条，"
                      f"其余 {dropped_pending} 条需人工核对",
        }] if dropped_pending else []),
        "breakdown": {
            "rule_engine": {
                "passed": rule_result["passed"],
                "total_issues": rule_result["total_issues"],
            },
            "semantic": {
                "enabled": include_semantic,
                "llm_enabled": semantic_result["llm_enabled"] if semantic_result else False,
                "reason": semantic_skip_reason if not (
                    semantic_result and semantic_result.get("llm_enabled")) else "",
                "passed": semantic_result["passed"] if semantic_result else None,
                "total_issues": semantic_result["total_issues"] if semantic_result else 0,
                "llm_calls": checker.stats["llm_calls"] if semantic_result else 0,
                "cache_hits": checker.stats["cache_hits"] if semantic_result else 0,
                "prefilter_skips": checker.stats["prefilter_skips"] if semantic_result else 0,
                # 三态计数：confirmed 仅统计确定性结论；pending 单独计数，
                # suggested 仍进 advisory_issues，不计入 total_issues（D1）。
                "confirmed_count": semantic_confirmed,
                "pending_review_count": len(pending_review),
            },
        },
    }
