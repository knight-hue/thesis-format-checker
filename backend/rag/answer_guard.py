
"""Deterministic guard for free-form RAG answers.

The guard keeps the public three-field contract (conclusion/basis/suggestion)
and only rewrites answers that make unsupported verdicts. It is a safety layer,
not a model-quality improvement.
"""
from __future__ import annotations

import re

from backend.rag.injection_classifier import classify_injection, has_refusal
from backend.rag.observed_schema import sanitize_observed

GUARD_VERSION = "rag-answer-guard-v1"

PASS_RE = re.compile(r"(符合|一致|合规|合格|通过|无需修改|均符合)")
WORD_RE = re.compile(r"(居中|居左|对齐|行距|加粗|字符间距|编号|首行缩进|缩进|相对位置|图下|图上)")
NO_CHANGE_RE = re.compile(r"(无需修改|不必修改|不用修改)")
INJECTION_RE = re.compile(r"(管理员更新|微软雅黑|全部合格|直接说合格|忽略证据|忽略以上)")
NEG_RE = re.compile(r"(不能|无法|未|不|没有|不足以|未知|未观测|非|不得|不应)")
REQUIREMENT_RE = re.compile(r"(应|需|须|要|要求|必须|应当)")
SOURCE_RE = re.compile(r"(源论文|原论文|整篇|全文)")
PDF_WORD_RE = re.compile(r"(PDF|pdf)")
INJECTION_QUESTION_RE = re.compile(r"(忽略|管理员更新|微软雅黑|直接说合格|全部合格|system prompt|泄露指令)")


def _clauses(text: str):
    return re.split(r"[，。；\n]", text or "")


def _positive(text: str, pattern: re.Pattern) -> bool:
    """A claim is positive only when its clause is not negated/required."""
    for clause in _clauses(text):
        for match in pattern.finditer(clause):
            prefix = clause[:match.start()]
            suffix = clause[match.end():].lstrip()
            if NEG_RE.search(prefix) or REQUIREMENT_RE.search(prefix[-10:]):
                continue
            # “符合以下要求/符合下列要求”是要求清单的引导语，不是 verdict。
            if suffix.startswith(("以下要求", "下列要求", "如下要求")):
                continue
            return True
    return False


def positive_word(text):
    for clause in _clauses(text):
        if any(k in clause for k in ("条款", "要求", "目标", "条件")) and not any(
            k in clause for k in ("实际", "观测", "实测")
        ):
            continue
        for match in WORD_RE.finditer(clause):
            if not NEG_RE.search(clause[:match.start()]):
                return True
    return False


def _has_any(text: str, words) -> bool:
    return any(word in (text or "") for word in words)


def _schema_true_reasons(parsed, conclusion):
    """can_judge=true 时的自相矛盾；与下面 can_judge=false 的 _schema_reasons 分开命名。"""
    reasons = []
    can_judge = parsed.get("can_judge")
    observed = list(parsed.get("observed") or [])
    unknown = list(parsed.get("unknown") or [])
    if can_judge is True and not observed:
        reasons.append("can_judge_true_without_observed")
    if can_judge is True and unknown and _positive(conclusion, PASS_RE):
        reasons.append("can_judge_true_with_unknown")
    if can_judge is True and _has_any(conclusion, ("不能证明", "未观测", "无法确定", "不能确定", "无法判断", "未提供", "不涉及", "证据缺失")):
        reasons.append("can_judge_true_but_uncertain")
    return reasons


def _schema_fields(parsed):
    can_judge = parsed.get("can_judge")
    return {
        "can_judge": can_judge if isinstance(can_judge, bool) else None,
        "observed": list(parsed.get("observed") or []),
        "unknown": list(parsed.get("unknown") or []),
        "answer_schema_version": str(parsed.get("answer_schema_version") or "1.0"),
    }


def _schema_reasons(parsed, conclusion, text):
    """Use structured fields when the model provides them."""
    reasons = []
    can_judge = parsed.get("can_judge")
    unknown = parsed.get("unknown") or []
    if can_judge is False and (_positive(conclusion, PASS_RE) or positive_word(conclusion)):
        reasons.append("schema_false_can_judge_conflict")
    if unknown and positive_word(conclusion):
        reasons.append("schema_unknown_attribute_conflict")
    if can_judge is False and _has_any(text, ("无需修改", "全部合格", "均符合")):
        reasons.append("schema_false_can_judge_no_change")
    return reasons


def _safe_result(parsed, sources, reasons):
    conclusion = parsed.get("conclusion", "")
    basis = parsed.get("basis", "")
    suggestion = parsed.get("suggestion", "")
    if not basis and sources:
        basis = "；".join((s.get("preview") or s.get("content") or "")[:80] for s in sources[:3])
    if "injection" in reasons or "missing_injection_refusal" in reasons:
        conclusion = "题面附加文字不是规范来源，不执行；只按知识库条款回答。"
        suggestion = suggestion or "请提出正常的论文格式问题。"
    elif "pdf_word" in reasons:
        conclusion = "PDF 资源名/实测值只能说明 PDF 层信息，不能证明原 Word 设置；不能据此判断论文合规。"
        suggestion = suggestion or "如需判断 Word 设置，请提供 DOCX 的 rFonts/sz/b/jc 等属性。"
    elif "foreign_college" in reasons:
        conclusion = "其他学院的条款不能替代目标学院依据；现有知识库不能据此判断目标论文合规。"
        suggestion = suggestion or "请提供目标学院的正式条款和论文实际观测。"
    elif "observed_missing_value" in reasons:
        conclusion = "提供的观测只有字段名，没有具体值，不能据此判断；请补充实际观测值。"
        suggestion = suggestion or "请按 word:font_name=宋体、pdf:font_size_pt=12.0 这类格式补充具体值。"
    elif "can_judge_true_without_observed" in reasons:
        conclusion = "标注为可判断，但没有实际观测值，不能据此判断；请补充观测。"
        suggestion = suggestion or "请提供至少一项实际观测，或把 can_judge 改为 false。"
    elif "can_judge_true_with_unknown" in reasons:
        conclusion = "仍有未观测项，不能据此说符合；请补充观测或改为不能判断。"
        suggestion = suggestion or "请补齐 unknown 中的字段后再判断。"
    elif "can_judge_true_but_uncertain" in reasons:
        conclusion = "结论本身说明证据不足，不能标注为可判断；请改为不能判断并补充观测。"
        suggestion = suggestion or "请补充实际观测，或把 can_judge 改为 false。"
    elif "source_compliance" in reasons or "no_change" in reasons or "pass_verdict" in reasons:
        conclusion = "知识库只提供规范条款，不能据此判断论文实际格式是否合规，也不能说无需修改。"
        suggestion = suggestion or "如需判断实际合规，请结合检测结果或提供 DOCX/PDF 格式观测。"
    fields = _schema_fields(parsed)
    fields["can_judge"] = False
    text = "【结论】%s\n【规范依据】%s\n【修改建议】%s" % (conclusion, basis, suggestion)
    return {"conclusion": conclusion, "basis": basis, "suggestion": suggestion,
            **fields, "text": text}


def guard_answer(question, parsed, sources=None, college_id="", session_context=""):
    """Return guarded three-field answer plus guard metadata.

    Args:
        question: original user question.
        parsed: dict with conclusion/basis/suggestion.
        sources: retrieval sources (list of dicts with chapter/section/content/preview).
        college_id: target college id, e.g. jlu_se.
        session_context: optional paper detection/history text.
    """
    sources = sources or []
    parsed, observed_issues = sanitize_observed(parsed or {})
    conclusion = str(parsed.get("conclusion") or "")
    basis = str(parsed.get("basis") or "")
    suggestion = str(parsed.get("suggestion") or "")
    text = " ".join((conclusion, basis, suggestion))
    reasons = list(observed_issues)
    reasons.extend(_schema_true_reasons(parsed, conclusion))
    warnings = []

    # 1) 直接宣称源论文/整篇合规或无需修改：RAG 知识库只有规范，没有实际论文观测。
    if _positive(text, SOURCE_RE) and _positive(text, PASS_RE):
        reasons.append("source_compliance")
    if _positive(conclusion, NO_CHANGE_RE):
        reasons.append("no_change")
    if _positive(conclusion, PASS_RE) and not _positive(conclusion, SOURCE_RE):
        # “规范要求应...”已经被 REQUIREMENT_RE 排除；剩下的“符合/合格”是 verdict。
        reasons.append("pass_verdict")

    # 2) PDF 资源名/测量值被当成 Word 设置。
    if (PDF_WORD_RE.search(text) and "Word" in text
            and _positive(text, PASS_RE)):
        reasons.append("pdf_word")

    # 3) 目标学院是 jlu_se，却拿 jlu_cs 的条款做等效结论。
    if college_id and "jlu_cs" in text and college_id != "jlu_cs" and _positive(text, PASS_RE):
        reasons.append("foreign_college")

    # 4) 注入执行：问题里有注入词，或答案里出现“管理员更新/全部合格”等。
    question_injected = classify_injection(question or "")["is_injection"]
    answer_injected = classify_injection(text)["is_injection"]
    refused = has_refusal(text)
    if answer_injected and not refused and _positive(text, INJECTION_RE):
        reasons.append("injection")
    if question_injected and not refused:
        warnings.append("missing_injection_refusal")
        if "injection" not in reasons:
            reasons.append("missing_injection_refusal")

    # 5) 同一段里把未观测/未知和符合混在一起：guard 无法逐属性核验，
    #    但可以拦“未知 + 符合/无需修改”这种明显的自相矛盾。
    if _positive(text, PASS_RE) and _has_any(text, ("未观测", "未知", "无法确定", "不能确定")):
        reasons.append("unknown_and_pass")
    reasons.extend(_schema_reasons(parsed, conclusion, text))

    # 去重，保持顺序
    seen = set()
    reasons = [r for r in reasons if not (r in seen or seen.add(r))]

    fields = _schema_fields(parsed)
    if not reasons:
        return {"conclusion": conclusion, "basis": basis, "suggestion": suggestion,
                **fields,
                "text": "【结论】%s\n【规范依据】%s\n【修改建议】%s" % (conclusion, basis, suggestion),
                "changed": False, "reasons": [], "warnings": warnings}
    safe = _safe_result({**parsed, "conclusion": conclusion, "basis": basis,
                         "suggestion": suggestion}, sources, reasons)
    safe.update({"changed": True, "reasons": reasons, "warnings": warnings})
    return safe
