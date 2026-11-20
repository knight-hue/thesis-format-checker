# -*- coding: utf-8 -*-
"""运行时术语确定性 gate（P1-2a）。

把离线 v5 的判定逻辑迁到运行时，但不接 FastAPI / RAG：

- 词表与策略数据：``data/terminology_exemptions_2027.json`` / ``terminology_policy.json``
- 检查范围：英文摘要 / 中文摘要 / 正文 / 致谢各自独立
- 首次出现：每个 ``(token, scope)`` 只判第一次
- 释义：``has_gloss`` 要求括号内容真正展开缩写
- 输出：``Decision``（match / mismatch / not_applicable / unknown）

运行时三态映射、LLM 残差与 issue 构造放在 ``checker`` / ``checker_service``，
本模块保持纯函数，便于单测和 300 条离线 parity。
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .patterns import _TERM_RE, _TERM_SKIP_WORDS

DATA_DIR = Path(__file__).resolve().parent / "data"
GAZETTEER_FILE = DATA_DIR / "terminology_exemptions_2027.json"
POLICY_FILE = DATA_DIR / "terminology_policy.json"

CJK = r"\u4e00-\u9fff"

# 参与“首次出现”判断的连续行文层。标题、图题、表题、公式不算首次定义位置。
SCOPE_KINDS: Dict[str, Set[str]] = {
    "body": {"body", "list_item", "note"},
    "zh_abstract": {"abstract_body"},
    "en_abstract": {"en_abstract_body"},
    "acknowledgement": {"acknowledgement_body"},
}
SCOPE_OF_KIND: Dict[str, str] = {
    kind: scope for scope, kinds in SCOPE_KINDS.items() for kind in kinds
}
PROSE_KINDS = frozenset(SCOPE_OF_KIND)

# 这些块不是“连续行文层”，不应参与术语首次出现，也不应生成待人工项：
# 标题、作者/封面、关键词、目录、图表题、参考文献、公式、表格等。
NON_PROSE_KINDS = frozenset({
    "title", "en_title", "cover_info", "author_info", "pledge_title",
    "pledge_body", "keyword_title", "keyword_body", "en_keyword_title",
    "en_keyword_body", "toc", "figure_caption", "table_caption",
    "table_continued_note", "reference_entry", "reference_continuation",
    "heading_1", "heading_2", "heading_3", "formula", "formula_number",
    "table", "figure", "image",
})

_CODE_BLOCK_RE = re.compile(r"[{}]|->|\breturn\b|\bselect\b")

SCOPE_LABELS = {
    "body": "正文",
    "zh_abstract": "中文摘要",
    "en_abstract": "英文摘要",
    "acknowledgement": "致谢",
}

# 括号释义里常见的参考文献角标：`（KG[1]）`、`（CNN[8][9]）`。
_REF_MARK_RE = r"(?:\s*\[\s*\d+(?:\s*[-–—,，]\s*\d+)*\s*\])*"

REASON_EXEMPT = "exempt_by_level"
REASON_NOT_FIRST = "not_first_in_scope"
REASON_NO_GLOSS = "first_occurrence_no_gloss"
REASON_CONTEXT = "context_unavailable"
REASON_UNLISTED = "model_guess_unlisted_term"


@dataclass(frozen=True)
class Occurrence:
    """一次术语出现；``scope=None`` 表示没有可用的逐段范围（如 PDF）。"""

    token: str
    normalized: str
    scope: Optional[str]
    kind: Optional[str]
    block_index: Optional[int]
    text: str
    span: str
    first_in_scope: Optional[bool]
    earlier_in_scope: Tuple[Dict[str, object], ...] = ()
    start: int = -1
    end: int = -1


@dataclass(frozen=True)
class Decision:
    """确定性 gate 的判定；confidence 留空表示“未校准”。"""

    occurrence: Occurrence
    verdict: str  # match / mismatch / not_applicable / unknown
    reason: str
    reason_code: Optional[str]
    rule_hits: Tuple[str, ...] = ()
    confidence: Optional[float] = None


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"术语策略数据缺失：{path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"术语策略数据不是合法 JSON：{path}") from exc


def _index_terms(data: dict) -> Dict[str, Tuple[dict, ...]]:
    by_term: Dict[str, List[dict]] = defaultdict(list)
    for item in data.get("terms") or []:
        term = str(item.get("term") or "").strip()
        if term:
            by_term[term].append(item)
    return {term: tuple(items) for term, items in by_term.items()}


@dataclass(frozen=True)
class TerminologyPolicy:
    gazetteer: Dict[str, Tuple[dict, ...]]
    data: dict
    gate_tokens: frozenset
    alias_lookup: Dict[str, str]

    @property
    def gate(self) -> dict:
        return self.data.get("gate") or {}

    @property
    def b04(self) -> dict:
        return self.data.get("b04_domain_exemption") or {}

    @property
    def medical_terms(self) -> frozenset:
        return frozenset(self.data.get("medical_field_terms") or [])

    def canonical(self, token: str) -> str:
        upper = str(token or "").strip().upper()
        return self.alias_lookup.get(upper, upper)

    def entries(self, token: str) -> Tuple[dict, ...]:
        token = str(token or "").strip()
        return (self.gazetteer.get(token)
                or self.gazetteer.get(token.upper())
                or ())

    def sections_levels(self, token: str) -> Tuple[set, set]:
        entries = self.entries(token)
        return ({str(e.get("section") or "") for e in entries},
                {str(e.get("level") or "") for e in entries})

    def core_term(self, token: str) -> bool:
        sections, _ = self.sections_levels(token)
        return any(section.startswith("C") for section in sections)

    def d_needs_gloss(self, token: str) -> bool:
        """D 级里注明“首次宜说明/展开”的缩写仍需解释；官方专名则不需要。"""
        for entry in self.entries(token):
            if entry.get("level") != "D":
                continue
            note = str(entry.get("note") or "")
            if re.search(r"(首次)?宜(说明|展开)|首次宜", note):
                return True
        return False

    def chinese_names(self, token: str) -> set:
        names = set()
        for entry in self.entries(token):
            name = str(entry.get("chinese") or "").strip()
            name = re.split(r"[；;、，,（(]", name)[0].strip()
            if 2 <= len(name) <= 12:
                names.add(name)
        return names

    def b04_allowed(self, token: str, field: str,
                    hits: Sequence[str]) -> Tuple[bool, str]:
        """B04 领域豁免：论文实际领域为医学 + 词条本身可领域豁免。

        是否启用由调用方的 ``b04_enabled`` 决定（配置默认值或请求开关）；
        这里不再重复读配置，避免请求开关被全局默认值挡住。
        """
        if field != "medicine":
            return False, "论文实际领域未识别为医学/生命科学（命中 %s）" % (
                "、".join(hits) or "无")
        entries = [e for e in self.entries(token)
                   if str(e.get("section") or "") == "B04"]
        if not entries:
            return False, ""
        excluded = {str(item).upper()
                    for item in self.gate.get("b04_excluded_terms") or []}
        if self.canonical(token) in excluded:
            return False, "该词条带歧义，不参与领域豁免"
        if not all("仅医学/生命科学专业" in str(e.get("note") or "")
                   for e in entries):
            return False, "该词条带歧义说明，不适用领域豁免"
        return True, "B04 医学词条：论文实际研究领域为医学/生命科学（%s）" % (
            "、".join(hits[:3]))

    def exempt_na(self, token: str, *, strict: bool = False,
                  b04_ok: bool = False, b04_why: str = "",
                  include_legacy_gate: bool = True) -> Tuple[bool, str]:
        """词表/政策层面的“不适用”。

        顺序很重要：条件豁免（AI）和负责人裁决优先，不能被旧 gate 的
        UBIQUITOUS（其中也含 AI）吞掉；否则 strict 模式永远不生效。
        """
        canonical = self.canonical(token)
        gate = self.gate

        for item in self.data.get("level_overrides") or []:
            if self.canonical(item.get("term")) != canonical:
                continue
            if item.get("mode") == "conditional":
                if strict:
                    return False, "严格模式：条件豁免词仍要求首次解释"
                return True, item.get("note") or "B 级条件豁免"

        if canonical in {str(x).upper()
                         for x in gate.get("conditional_b_level") or []}:
            if strict:
                return False, "严格模式：条件豁免词仍要求首次解释"
            return True, "B 级条件豁免（计算机/软件/人工智能专业通用缩写）"

        if canonical in {str(x).upper()
                         for x in gate.get("proper_name_allowlist") or []}:
            return True, "负责人裁定的专名（产品/机构名）"

        if canonical in {self.canonical(item.get("term"))
                         for item in self.data.get("proper_name_rulings") or []}:
            return True, "负责人裁定的专名"

        if canonical in {str(x).upper()
                         for x in gate.get("algorithm_proper_names") or []}:
            return True, "算法专名（非英文缩略语），不按缩写首次解释要求"

        if canonical in {str(x).upper()
                         for x in gate.get("model_proper_names") or []}:
            return True, "模型专名，不按缩写首次解释要求"

        if canonical in {str(x).upper()
                         for x in gate.get("official_format_names") or []}:
            return True, "官方文件格式名称（建议补入 A 级源表）"

        if include_legacy_gate and canonical in {str(x).upper()
                                                 for x in gate.get("proper_names") or []}:
            return True, "旧 gate：模型/数据集/会议/机构专名"

        if include_legacy_gate and canonical in {str(x).upper()
                                                 for x in gate.get("ubiquitous") or []}:
            return True, "旧 gate：通用缩写"

        if canonical in {str(x).upper()
                         for x in gate.get("b_generic_allowlist") or []}:
            return True, "接口/硬件类通用缩写（B 级条件豁免）"

        sections, levels = self.sections_levels(token)
        if any(section.startswith("C") for section in sections):
            return False, "含 C 级释义，不自动豁免"
        if any(section.startswith("B") for section in sections):
            if b04_ok:
                return True, b04_why
            return False, "B 级条件豁免只覆盖通用接口缩写，本词属领域概念"
        if "A" in levels:
            return True, "词表 A 级官方标识"
        return False, ""


def _gate_tokens(data: dict) -> frozenset:
    gate = data.get("gate") or {}
    names = (
        "ubiquitous", "proper_names", "conditional_b_level",
        "b_generic_allowlist", "proper_name_allowlist", "manual_non_exempt",
        "algorithm_proper_names", "model_proper_names",
        "official_format_names",
    )
    values = set()
    for name in names:
        values.update(str(item).upper() for item in gate.get(name) or [])
    return frozenset(values)


def _alias_lookup(data: dict) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for canonical, variants in (data.get("token_aliases") or {}).items():
        for variant in variants or [canonical]:
            lookup[str(variant).upper()] = str(canonical).upper()
        lookup[str(canonical).upper()] = str(canonical).upper()
    return lookup


@lru_cache(maxsize=1)
def load_policy() -> TerminologyPolicy:
    data = _load_json(POLICY_FILE)
    gazetteer = _index_terms(_load_json(GAZETTEER_FILE))
    if not gazetteer:
        raise RuntimeError(f"术语词表为空：{GAZETTEER_FILE}")
    if not data.get("gate"):
        raise RuntimeError(f"术语策略缺少 gate：{POLICY_FILE}")
    return TerminologyPolicy(
        gazetteer=gazetteer, data=data, gate_tokens=_gate_tokens(data),
        alias_lookup=_alias_lookup(data))


def _sentence_of(text: str, start: int, end: int) -> str:
    left = 0
    for mark in "。！？；\n":
        pos = text.rfind(mark, 0, start)
        if pos >= 0:
            left = max(left, pos + 1)
    right = len(text)
    for mark in "。！？；\n":
        pos = text.find(mark, end)
        if pos >= 0:
            right = min(right, pos + 1)
    sentence = text[left:right].strip()
    if sentence:
        return sentence
    return text[max(0, start - 40):min(len(text), end + 40)].strip()


def _clip(text: str, start: int, radius: int = 40) -> str:
    left = max(0, start - radius)
    right = min(len(text), start + radius)
    return ("…" if left else "") + text[left:right] + (
        "…" if right < len(text) else "")


_CITATION_YEAR_RE = re.compile(r"\s*[（(]\s*(?:19|20)\d{2}\s*[)）]")
_QUOTE_PAIRS = (("“", "”"), ("\"", "\""), ("‘", "’"))
# 紧邻「模型/框架/系统/平台/算法/协议」的大写代号，按具名专名处理（如「关系抽取模型RLTP」）。
_MODEL_CONTEXT_RE = re.compile(r"(?:模型|框架|系统|平台|算法|协议)$")


def _looks_like_citation(text: str, start: int, end: int) -> bool:
    """「KATOMAA（2018）」是作者名+年份的引文，不是要解释的缩写。"""
    return _CITATION_YEAR_RE.match(text[end:end + 8]) is not None


def _looks_like_quoted_example(text: str, start: int, end: int) -> bool:
    """「如“Mr.”、“UFO”等缩写词」是被引用的示例词，不是论文术语。"""
    before = text[max(0, start - 2):start]
    after = text[end:end + 3]
    quoted = False
    for opening, closing in _QUOTE_PAIRS:
        if before.endswith(opening) and after.startswith(closing):
            quoted = True
            break
    if not quoted:
        return False
    return "等" in text[end:end + 12] or "如" in text[max(0, start - 6):start]


def _model_context_name(text: str, start: int, token: str = "") -> bool:
    """「关系抽取模型RLTP」：代号紧跟在「模型/框架/系统」等词后，按具名专名处理。"""
    if start < 0 and token:
        start = text.find(token)
    if start <= 0:
        return False
    return _MODEL_CONTEXT_RE.search(text[max(0, start - 6):start]) is not None


def is_candidate(token: str, policy: TerminologyPolicy) -> bool:
    """是否是术语规则要看的 token。

    与离线 v5 的候选口径一致，但补上词表里的混合大小写专名：

    - 全大写、2–8 个字母的缩写（CNN / RNN / SGD）；
    - 词表精确命中且含大写字母（Transformer / ViT / t-SNE / K-means / TF-IDF）；
    - 别名/旧 gate 命中且含大写字母（I/O）。

    不把 ``map`` / ``led`` / ``In`` / ``train.py`` / ``F1`` 这类普通词、
    文件名或带数字指标当缩写；词表的 uppercase 回退只用于判定，不用于候选。
    """
    core = str(token or "").strip(".,;:()[]（）\"' ")
    if not core or core.lower() in _TERM_SKIP_WORDS:
        return False
    has_upper = any(ch.isupper() for ch in core)
    alpha_like = core.replace("-", "").replace("/", "").isalpha()
    if not has_upper or not alpha_like:
        return False
    if policy.gazetteer.get(core):
        return True
    if policy.canonical(core) in policy.gate_tokens:
        return True
    return core.isalpha() and core.isupper() and 2 <= len(core) <= 8


def collect_occurrences(document: dict,
                        policy: TerminologyPolicy) -> List[Occurrence]:
    """按文档顺序收集候选出现，并标出每个 (token, scope) 的首次出现。"""
    blocks = [b for b in (document.get("blocks") or [])
              if isinstance(b, dict) and (b.get("text") or "").strip()]
    blocks.sort(key=lambda b: b.get("index")
                if isinstance(b.get("index"), int) else 10 ** 9)

    prior: Dict[Tuple[str, Optional[str]], List[dict]] = defaultdict(list)
    occurrences: List[Occurrence] = []
    for block in blocks:
        text = str(block.get("text") or "").strip()
        kind = block.get("kind")
        if kind in NON_PROSE_KINDS:
            continue
        if _CODE_BLOCK_RE.search(text):
            continue
        scope = SCOPE_OF_KIND.get(kind)
        index = block.get("index")
        for match in _TERM_RE.finditer(text):
            core = match.group(0).strip(".,;:()[]（）\"' ")
            if not core:
                continue
            if core == core.lower() and core.lower() in _TERM_SKIP_WORDS:
                continue
            if not is_candidate(core, policy):
                continue
            if _looks_like_citation(text, match.start(), match.end()):
                continue
            if _looks_like_quoted_example(text, match.start(), match.end()):
                continue
            normalized = policy.canonical(core)
            key = (normalized, scope)
            earlier = tuple(prior[key][-2:]) if scope is not None else ()
            span = _sentence_of(text, match.start(), match.end())
            occurrence = Occurrence(
                token=core,
                normalized=normalized,
                scope=scope,
                kind=kind,
                block_index=index,
                text=text,
                span=span,
                # scope 缺失（PDF/未知块）时无法验证首次出现，必须留 None。
                first_in_scope=None if scope is None else not earlier,
                earlier_in_scope=earlier,
                start=match.start(),
                end=match.end(),
            )
            occurrences.append(occurrence)
            if scope is not None:
                prior[key].append({
                    "index": index,
                    "kind": kind,
                    "excerpt": _clip(text, match.start()),
                })
    return occurrences


def has_gloss(text: str, token: str, span: str = "", *,
              policy: Optional[TerminologyPolicy] = None) -> bool:
    """首次出现处是否真正展开了缩写；迁自离线 v5 的 ``has_gloss``。"""
    if not text:
        return False
    policy = policy or load_policy()
    at = text.find(span) if span else 0
    if at < 0:
        at = 0
    token_at = text.find(token, at)
    if token_at < 0:
        token_at = text.find(token)
    if token_at < 0:
        return False
    window = text[max(0, token_at - 90): token_at + 45]
    # 无括号解释（中文名/英文全名组件）可能比括号释义更长，单独放宽后向窗口。
    wide = text[max(0, token_at - 90): token_at + 120]

    # 中文名（TOKEN）/（TOKEN[1]）
    if re.search(r"[（(]\s*" + re.escape(token) + _REF_MARK_RE
                 + r"\s*[)）]", window):
        return True
    # （English Name, TOKEN） / （English Name，简称 TOKEN）/（…TOKEN[1]）
    if re.search(r"[（(][^（()）]{1,90}[,，、]\s*"
                 r"(?:简称|缩写为|缩写)?\s*"
                 + re.escape(token) + _REF_MARK_RE
                 + r"\s*[)）]", window):
        return True
    # TOKEN（中文名）
    if re.search(re.escape(token) + r"\s*[（(]\s*[" + CJK + r"]", window):
        return True
    # （TOKEN，English Full Name）：缩写在前、英文全称在后。
    # PDF 抽取常在缩写后带空格或逗号（如「（MOOP ，Multi-Objective Optimization Problem）」），
    # 这仍是一次完整的括号释义，不能判成未解释。
    for match in re.finditer(
            r"[（(]\s*" + re.escape(token) + r"\s*[,，、]\s*([^（()）]{4,90})[)）]", wide):
        words = [w for w in re.split(r"[^A-Za-z]+", match.group(1)) if len(w) >= 3]
        if len(words) >= 2:
            return True
    # TOKEN，也就是/即/指 中文全称：中文同位解释（如「叫PWA，也就是渐进式网络应用」）。
    if re.search(re.escape(token) + r"\s*[，,]\s*(?:也就是|即|是指|指的是|表示|全称是)\s*"
                 r"[" + CJK + r"]{3,20}", wide):
        return True
    # 中文全称 + 也就是/即/又称 + 英文全称/缩略语：
    # 「知识图谱也就是KnowledgeGraph，KG」「中文自然语言处理也就是NLP」。
    before_token = text[max(0, token_at - 60):token_at]
    if re.search(r"[" + CJK + r"]{2,20}\s*(?:也就是|即|又称|简称|缩写为)\s*"
                 r"(?:[A-Za-z][A-Za-z\-]*(?:\s+[A-Za-z][A-Za-z\-]*)*)?[\s，,、]*$",
                 before_token):
        return True
    # TOKEN（English name）：括号内容必须真正展开，不能只是包含该缩写的短语。
    for match in re.finditer(
            re.escape(token) + r"\s*[（(]\s*([^（()）]{1,60})[)）]", window):
        content = match.group(1)
        if token.lower() in content.lower():
            continue
        if re.search(r"[A-Za-z]", content):
            return True
    # TOKEN + 名词 +（中文解释），括号内至少 4 个中文。
    if re.search(re.escape(token) + r"\s*(?:操作|方法|算法|技术|模型|系统|框架|机制|策略)?\s*"
                 r"[（(]\s*([" + CJK + r"、，,]{4,20})\s*[)）]", window):
        return True
    names = policy.chinese_names(token)
    entries = policy.entries(token)
    english = str(entries[0].get("english") or "") if entries else ""

    # TOKEN + 中文名（无括号）：CDN内容分发网络 / CNN卷积神经网络 / BP反向传播算法。
    for name in names:
        if re.search(re.escape(token) + r"\s*[，,：:]?\s*" + re.escape(name),
                     wide):
            return True

    # TOKEN + 英文全名组件（无括号）：MVC ... Model / View / Controller。
    words = [word for word in re.split(r"[^A-Za-z]+", english)
             if len(word) >= 3]
    words = list(dict.fromkeys(words))
    if len(words) >= 2 and all(
            re.search(r"\b" + re.escape(word) + r"\b", wide,
                      re.IGNORECASE)
            for word in words):
        return True

    # TOKEN [值/指标/分数] 是/指/表示/即/为 <词表中文名>。
    # 例：FN 是假负例；AUC值是ROC曲线下面积；不能把“DCNN 是 101 层的 ResNet”当释义。
    definition = re.search(
        re.escape(token) + r"\s*(?:值|指标|分数)?\s*"
        r"(?:是|指|表示|即|为|定义为|指的是)\s*"
        r"([^。；;\n]{2,80})", wide)
    if names and definition and any(name in definition.group(1)
                                    for name in names):
        return True
    # 中文名称紧邻缩写；允许中文名与缩写之间有一个开括号。
    # 例：均方误差(MSE Loss) / 卷积神经网络（CNN）
    before = text[max(0, token_at - 16): token_at]
    for name in names:
        if re.search(re.escape(name) + r"\s*[（(]?\s*$", before):
            return True
    return False


def paper_field(document: dict,
                policy: Optional[TerminologyPolicy] = None
                ) -> Tuple[str, List[str]]:
    """从标题 + 关键词 + 中文摘要判断论文实际研究领域。"""
    policy = policy or load_policy()
    texts = []
    for block in document.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        if block.get("kind") not in {"title", "keyword_body", "en_keyword_body",
                                     "keyword_title", "abstract_body"}:
            continue
        text = str(block.get("text") or "").strip()
        if text:
            texts.append(text)
    joined = " ".join(texts)
    hits = sorted({term for term in policy.medical_terms if term in joined})
    return ("medicine" if len(hits) >= 3 else "general"), hits


def classify(occurrence: Occurrence, *, policy: TerminologyPolicy,
             strict: bool = False, b04_enabled: bool = False,
             paper_field_name: str = "general",
             paper_field_hits: Sequence[str] = (),
             include_legacy_gate: bool = True) -> Decision:
    """对一次出现给出 v5 判定。"""
    token = occurrence.token
    b04_ok, b04_why = False, ""
    if b04_enabled:
        b04_ok, b04_why = policy.b04_allowed(
            token, paper_field_name, paper_field_hits)

    na, why = policy.exempt_na(
        token, strict=strict, b04_ok=b04_ok, b04_why=b04_why,
        include_legacy_gate=include_legacy_gate)
    if na:
        return Decision(occurrence, "not_applicable", why,
                        REASON_EXEMPT)

    if policy.canonical(token) in {str(x).upper()
                                   for x in policy.gate.get("non_term_examples") or []}:
        return Decision(occurrence, "not_applicable",
                        "引文作者名 / 引号内示例词 / 产品名片段，不按术语要求解释"
                        "（2026-11-18 裁定）",
                        REASON_EXEMPT)

    # 词表未收录、但紧邻「模型/框架/系统」等词的大写代号，按具名专名处理。
    # 只对未收录词生效：已收录的核心术语（如 DAG）不会被这条上下文规则豁免。
    if (not policy.entries(token)
            and _model_context_name(occurrence.text or "", occurrence.start, token)):
        return Decision(occurrence, "not_applicable",
                        "紧邻「模型/框架/系统」等词的大写代号，按具名专名处理",
                        REASON_EXEMPT)

    _, levels = policy.sections_levels(token)
    d_level = "D" in levels and not policy.d_needs_gloss(token)

    first = occurrence.first_in_scope
    if first is None:
        # 无逐段解析时不能验证“首次”，但局部片段若已给出释义，
        # 至少不应把它当违规；其余仍进 pending，不猜首次出现。
        local_text = occurrence.text or occurrence.span
        if has_gloss(local_text, token, occurrence.span, policy=policy):
            return Decision(occurrence, "match",
                            "局部片段已给出释义；无逐段解析，未验证是否首次",
                            None)
        if d_level:
            return Decision(occurrence, "not_applicable",
                            "词表 D 级官方专名，无逐段解析时按不适用处理",
                            REASON_EXEMPT)
        return Decision(occurrence, "unknown",
                        "无法回溯原文段落（PDF 未解析）",
                        REASON_CONTEXT)

    if first is False:
        where = "、".join(
            "para#%s" % item.get("index")
            for item in occurrence.earlier_in_scope
            if item.get("index") is not None
        ) or "同段更早位置"
        scope = SCOPE_LABELS.get(occurrence.scope or "", "本范围")
        return Decision(occurrence, "not_applicable",
                        "%s内更早位置已出现（%s），本条非首次出现" % (scope, where),
                        REASON_NOT_FIRST)

    if has_gloss(occurrence.text, token, occurrence.span, policy=policy):
        return Decision(occurrence, "match",
                        "本范围内首次出现且紧邻处给出中文名/英文全称/括号释义",
                        None)

    if d_level:
        return Decision(occurrence, "not_applicable",
                        "词表 D 级官方模型/数据集/软件专名，首次出现未展开也按不适用处理",
                        REASON_EXEMPT)

    canonical = policy.canonical(token)
    if policy.core_term(token) or (
            strict and canonical in {str(x).upper()
                                     for x in policy.gate.get("conditional_b_level") or []}):
        return Decision(occurrence, "mismatch",
                        "本范围内首次出现、无释义，且属需解释的核心术语",
                        REASON_NO_GLOSS, ("gloss_missing",))

    if policy.entries(token):
        return Decision(occurrence, "mismatch",
                        "本范围内首次出现、无释义，且词表标注为需解释的术语",
                        REASON_NO_GLOSS, ("gloss_missing",))

    if canonical in {str(x).upper()
                     for x in policy.gate.get("manual_non_exempt") or []}:
        return Decision(occurrence, "mismatch",
                        "本范围内首次出现、无释义；方法/框架缩写不属于规则豁免的专名或通用词",
                        REASON_NO_GLOSS, ("gloss_missing",))

    return Decision(occurrence, "unknown",
                    "本范围内首次出现、无释义，但词表未收录（可能是专名），证据不足",
                    REASON_UNLISTED)


def evaluate_document(document: dict, *, strict: bool = False,
                      b04_domain_exempt: bool = False,
                      include_legacy_gate: bool = True,
                      policy: Optional[TerminologyPolicy] = None
                      ) -> List[Decision]:
    """运行时入口：每个 (token, scope) 只判首次出现。"""
    policy = policy or load_policy()
    field_name, field_hits = paper_field(document, policy)
    b04_enabled = bool(b04_domain_exempt or policy.b04.get("enabled"))
    occurrences = collect_occurrences(document, policy)

    decisions: List[Decision] = []
    known_tokens = set()
    for occurrence in occurrences:
        if occurrence.scope is None:
            continue
        known_tokens.add(occurrence.normalized)
        if not occurrence.first_in_scope:
            continue
        decisions.append(classify(
            occurrence, policy=policy, strict=strict,
            b04_enabled=b04_enabled, paper_field_name=field_name,
            paper_field_hits=field_hits,
            include_legacy_gate=include_legacy_gate))

    # 没有逐段范围的 token：只对未在已知 scope 出现过的 token 报一次，
    # 避免同一缩写既在正文判过、又在表格/PDF 块里重复报。
    seen_unknown = set()
    for occurrence in occurrences:
        if occurrence.scope is not None:
            continue
        if occurrence.normalized in known_tokens:
            continue
        if occurrence.normalized in seen_unknown:
            continue
        seen_unknown.add(occurrence.normalized)
        decisions.append(classify(
            occurrence, policy=policy, strict=strict,
            b04_enabled=b04_enabled, paper_field_name=field_name,
            paper_field_hits=field_hits,
            include_legacy_gate=include_legacy_gate))
    return decisions


def evaluate_token(document: dict, token: str, span: str = "", *,
                   block_index: Optional[int] = None,
                   scope: Optional[str] = None,
                   strict: bool = False,
                   b04_domain_exempt: bool = False,
                   include_legacy_gate: bool = True,
                   policy: Optional[TerminologyPolicy] = None) -> Decision:
    """定点判定：给离线 parity / 人工复核用，可判“非首次出现”。"""
    policy = policy or load_policy()
    field_name, field_hits = paper_field(document, policy)
    b04_enabled = bool(b04_domain_exempt or policy.b04.get("enabled"))

    candidates = []
    for occurrence in collect_occurrences(document, policy):
        if occurrence.normalized != policy.canonical(token):
            continue
        if block_index is not None and occurrence.block_index != block_index:
            continue
        candidates.append(occurrence)

    if not candidates and block_index is not None:
        # block_index 只是定位提示：PDF 的旧块号（pdf#N）与解析后的块号可能对不上。
        # 只接受 span 能对上的候选，避免把远处同名出现当成本条证据。
        for occurrence in collect_occurrences(document, policy):
            if occurrence.normalized != policy.canonical(token):
                continue
            if span and (span in occurrence.span or occurrence.span in span):
                candidates.append(occurrence)
                break

    target = None
    if candidates:
        if span:
            target = next(
                (item for item in candidates
                 if span in item.span or item.span in span), None)
        if target is None:
            target = candidates[0]

    if target is None:
        # 没有逐段原文可回溯：不能假设“首次出现”，交 context_unavailable。
        target = Occurrence(
            token=token, normalized=policy.canonical(token), scope=scope,
            kind=None, block_index=block_index, text=span, span=span,
            first_in_scope=None, earlier_in_scope=(),
        )
    return classify(
        target, policy=policy, strict=strict, b04_enabled=b04_enabled,
        paper_field_name=field_name, paper_field_hits=field_hits,
        include_legacy_gate=include_legacy_gate)
