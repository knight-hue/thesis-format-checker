# -*- coding: utf-8 -*-
"""SemanticChecker：语义类规则检查编排。

- 确定性规则：直接调用 deterministic.run_deterministic（零 LLM），**不依赖 rag_service**。
- 术语：`terminology.evaluate_document` 的确定性 gate → 残差才送 LLM。
- 定位规则：patterns 扫候选 → 预筛 → 限流 → 合并去重 → 批量送 RAG 精判。

rag_service 为 None（AI 服务未就绪）时：确定性规则与术语 gate 照常执行，
术语残差进 pending_review；其他定位规则整组跳过，由 check() 返回值里的
llm_enabled=False 向下游标明"这层没跑"，避免静默假装已检查。
"""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from .rules import (
    DEFAULT_MAX_CANDIDATES,
    DETERMINISTIC_SEMANTIC_RULES,
    LOCATED_SEMANTIC_RULES,
    MIXED_BATCH_SIZE,
    RULE_MAX_CANDIDATES,
    SEMANTIC_RULES,
    make_issue,
)
from .patterns import _locate_candidates, _obviously_compliant
from .deterministic import run_deterministic
from .terminology import (
    REASON_CONTEXT,
    REASON_UNLISTED,
    evaluate_document,
)

VALID_RESULT_STATUS = {"match", "mismatch", "unknown", "not_applicable"}


def _result_status(result) -> str:
    """把 LLM 批量结果统一成四态；非法/缺失按 unknown。"""
    if not isinstance(result, dict):
        return "unknown"
    status = result.get("status")
    if status in VALID_RESULT_STATUS:
        return status
    if result.get("compliant") is True:
        return "match"
    if result.get("compliant") is False:
        return "mismatch"
    return "unknown"


def _terminology_suggestion(decision) -> str:
    if decision.reason_code == REASON_CONTEXT:
        return "该来源没有逐段文本，无法验证首次出现；请人工核对原文"
    if decision.reason_code == REASON_UNLISTED:
        return "该缩写未在词表中，建议人工确认是否需要首次解释"
    if decision.verdict == "mismatch":
        return "在外文缩写首次出现处用括号注明中文含义或英文全称"
    return "请人工确认该术语的首次出现与释义"


class SemanticChecker:
    """语义类规则检查器：按 kind 提取切片，确定性规则走正则、定位规则走 RAG 批量判定。"""

    def __init__(self, rag_service=None, max_workers=7, enable_cache=True):
        self.rag = rag_service
        # 无 AI 服务时只跑零 LLM 的确定性规则，定位规则整组跳过；
        # 非本机端点且未显式放行时，rag_service 也会被上层置为 None（隐私硬闸）。
        self.llm_enabled = rag_service is not None and getattr(
            rag_service, "documents_allowed", True)
        self.max_workers = max_workers
        self.enable_cache = enable_cache
        self._cache = {}
        self._lock = Lock()
        self.unchecked = []
        self.pending_review = []
        self.stats = {"llm_calls": 0, "cache_hits": 0, "prefilter_skips": 0}

    def _collect_blocks(self, document):
        """按 kind 聚合段落，保留段落 index 与原文，供改写定位使用。"""
        blocks = {}
        for b in document.get("blocks", []):
            if b.get("type") != "paragraph":
                continue
            kind = b.get("kind")
            text = (b.get("text") or "").strip()
            if kind and text:
                blocks.setdefault(kind, []).append({"index": b.get("index"), "text": text})
        return blocks

    def check(self, document, college_id, *, terminology_strict=False,
              terminology_b04_domain_exempt=False):
        """对文档逐条执行语义规则，返回与规则引擎一致的 issue 列表结构。

        规则分三路：
        ① 确定性规则（目录/致谢/参考文献/列表序号/单位/数字）：零 LLM，全文覆盖；
        ② 术语：确定性 gate 先判，只有残差才送 LLM；
        ③ 其他定位规则（坐标轴）：全文扫候选片段后合并去重一次性混合判定，
           依赖 AI 服务，rag_service 为 None 时整组跳过。
        """
        self.unchecked = []
        self.pending_review = []
        blocks_by_kind = self._collect_blocks(document)

        issues = []
        # ① 确定性规则：零 LLM，顺序执行
        for rule in SEMANTIC_RULES:
            if rule["rule_id"] in DETERMINISTIC_SEMANTIC_RULES:
                issues.extend(run_deterministic(rule, blocks_by_kind))

        # ② 术语：确定性 gate → 残差 LLM / pending_review
        terminology_rule = next(
            (rule for rule in SEMANTIC_RULES if rule["rule_id"] == "terminology"),
            None,
        )
        if terminology_rule is not None:
            issues.extend(self._run_terminology(
                terminology_rule, document, college_id,
                strict=terminology_strict,
                b04_domain_exempt=terminology_b04_domain_exempt,
            ))

        # ③ 其他定位规则：各自定位+预筛+限流，汇总去重后一次混合判定
        located_rules = [r for r in SEMANTIC_RULES
                         if r["rule_id"] in LOCATED_SEMANTIC_RULES]
        if located_rules and self.llm_enabled:
            issues.extend(self._run_located_merged(
                located_rules, blocks_by_kind, college_id))

        return {
            "passed": len(issues) == 0,
            "total_issues": len(issues),
            "issues": issues,
            "pending_review": list(self.pending_review),
            "llm_enabled": self.llm_enabled,
            "unchecked": list(self.unchecked),
        }

    # ====================== 定位规则候选收集 ======================
    def _collect_located_candidates(self, rule, blocks_by_kind):
        """定位单条规则的候选片段：全文扫候选 → 预筛 → 限流。

        返回 [(fragment_text, block_index), ...]（已去重）。
        """
        rule_id = rule["rule_id"]
        # 收集相关 kind 的全部段落，按 index 还原文档全局顺序
        blocks = []
        for kind in rule["kinds"]:
            blocks.extend(blocks_by_kind.get(kind, []))
        blocks.sort(key=lambda b: b.get("index") if isinstance(b.get("index"), int) else 10**9)

        candidates = []  # [(fragment_text, block_index), ...]
        for b in blocks:
            text = (b.get("text") or "").strip()
            if not text:
                continue
            for frag in _locate_candidates(rule_id, text):
                candidates.append((frag, b.get("index")))
        # 保序去重：相同片段只判一次，保留首个段落 index
        seen = {}
        unique = []
        for frag, idx in candidates:
            if frag not in seen:
                seen[frag] = idx
                unique.append((frag, idx))
        candidates = unique

        # 送审前预筛：丢弃明显无法判定的噪声片段
        candidates = [(f, idx) for f, idx in candidates
                      if not _obviously_compliant(rule_id, f)]

        # 限流：均匀采样保证全文覆盖
        cap = RULE_MAX_CANDIDATES.get(rule_id, DEFAULT_MAX_CANDIDATES)
        if len(candidates) > cap:
            self.unchecked.append({"rule_id": rule_id, "reason": f"候选片段{len(candidates)}个，仅抽查{cap}个，其余需核对"})
            step = len(candidates) / cap
            candidates = [candidates[int(i * step)] for i in range(cap)]
        return candidates

    # ====================== 术语 gate + 残差 ======================
    def _run_terminology(self, rule, document, college_id, *, strict=False,
                         b04_domain_exempt=False):
        """确定性 gate 先判；无法判定的词表未收录项才进 LLM 残差。"""
        decisions = evaluate_document(
            document, strict=strict, b04_domain_exempt=b04_domain_exempt)

        issues = []
        residual = []
        for decision in decisions:
            if decision.verdict == "mismatch":
                issues.append(self._terminology_issue(
                    rule, decision, source="rule_engine",
                    decision_value="confirmed"))
            elif decision.verdict == "unknown":
                if decision.reason_code == REASON_CONTEXT:
                    self.pending_review.append(self._terminology_issue(
                        rule, decision, source="rule_engine",
                        decision_value="pending_review",
                        message="需要人工确认术语首次出现"))
                else:
                    residual.append(decision)

        if residual:
            if self.llm_enabled:
                issues.extend(self._judge_terminology_residual(
                    rule, college_id, residual))
            else:
                for decision in residual:
                    self.pending_review.append(self._terminology_issue(
                        rule, decision, source="rule_engine",
                        decision_value="pending_review",
                        message="需要人工确认术语首次出现（AI 服务不可用）"))
        return issues

    def _terminology_issue(self, rule, decision, *, source, decision_value,
                           message=None, conclusion=None):
        occurrence = decision.occurrence
        location = {
            "type": "document",
            "index": occurrence.block_index,
            "kinds": [occurrence.kind] if occurrence.kind else [],
        }
        issue = make_issue(
            rule,
            [{"index": occurrence.block_index, "text": occurrence.span}],
            actual=(occurrence.span or occurrence.token)[:60],
            conclusion=conclusion or decision.reason,
            basis=("确定性检查：词表/范围/首次出现/释义"
                   if decision_value == "confirmed"
                   else "模型建议：词表未收录"),
            suggestion=_terminology_suggestion(decision),
            message=message,
            source=source,
        )
        issue["decision"] = decision_value
        issue["confidence"] = decision.confidence
        issue["evidence"] = {
            "scope": occurrence.scope,
            "first_occurrence": occurrence.first_in_scope,
            "earlier_occurrences": list(occurrence.earlier_in_scope),
            "rule_hits": list(decision.rule_hits),
            "reason_code": decision.reason_code,
        }
        if decision_value == "pending_review":
            # pending_review 不经过 compact_check_result 的字段补齐，
            # 这里直接满足 IssueResponse 的必填字段。
            issue["fixable"] = False
            issue["fix_reason"] = "需要人工确认"
            issue["occurrence_count"] = 1
            issue["location_samples"] = [location]
            issue["variants"] = []
        return issue

    def _judge_terminology_residual(self, rule, college_id, decisions):
        items = [{
            "rule_id": "terminology",
            "fragment": decision.occurrence.span or decision.occurrence.token,
            "block_index": decision.occurrence.block_index,
        } for decision in decisions]
        results = self._judge_mixed_cached(
            college_id, {"terminology": rule}, items)

        issues = []
        for decision, result in zip(decisions, results):
            status = _result_status(result)
            if status == "mismatch":
                issues.append(self._terminology_issue(
                    rule, decision, source="rag",
                    decision_value="suggested",
                    conclusion=(result.get("conclusion")
                                or decision.reason),
                    message="术语可能未解释",
                ))
            elif status == "unknown":
                self.pending_review.append(self._terminology_issue(
                    rule, decision, source="rule_engine",
                    decision_value="pending_review",
                    message="需要人工确认术语首次出现（模型无法判定）"))
            # match / not_applicable：不返回
        return issues

    # ====================== 定位规则执行 ======================
    def _run_located_merged(self, rules, blocks_by_kind, college_id):
        """② 定位规则（合并版）：各规则先出候选，汇总去重后一次性混合判定。"""
        # 1. 各规则候选汇总
        items = []  # [{"rule_id", "fragment", "block_index"}]
        for rule in rules:
            for frag, idx in self._collect_located_candidates(rule, blocks_by_kind):
                items.append({
                    "rule_id": rule["rule_id"],
                    "fragment": frag,
                    "block_index": idx,
                })

        if not items:
            self.stats["prefilter_skips"] += 1
            return []

        # 2. 按 (rule_id, fragment) 去重，保持顺序
        seen = set()
        unique_items = []
        for it in items:
            key = (it["rule_id"], it["fragment"])
            if key not in seen:
                seen.add(key)
                unique_items.append(it)
        items = unique_items

        # 3. 混合批量判定 + 缓存
        rules_by_id = {r["rule_id"]: r for r in rules}
        results = self._judge_mixed_cached(college_id, rules_by_id, items)

        # 4. 回填生成 issue；unknown / not_applicable 绝不生成“通过”
        issues = []
        for it, res in zip(items, results):
            status = _result_status(res)
            if status in {"unknown", "not_applicable"}:
                failure = res.get("reason") or (
                    "not_applicable" if status == "not_applicable" else "missing_evidence")
                # CheckResponse.unchecked 只暴露 rule_id / reason（见 UncheckedItemResponse），
                # 因此把状态与失败码写进 reason，避免声明了却序列化不到的字段。
                # 若前端需要结构化的 status，要先走 Apifox 提案再扩展该模型。
                label = "不适用" if status == "not_applicable" else "无法判定"
                self.unchecked.append({
                    "rule_id": it["rule_id"],
                    "reason": f"{label}（{failure}）：第{it.get('block_index')}块："
                              f"{res.get('conclusion') or '无有效结果'}",
                })
            elif status == "mismatch":
                rule = rules_by_id[it["rule_id"]]
                issues.append(make_issue(
                    rule, [{"index": it["block_index"], "text": it["fragment"]}],
                    actual=it["fragment"][:60],
                    conclusion=res.get("conclusion", ""),
                    basis=res.get("basis", ""),
                    suggestion=res.get("suggestion", ""),
                ))
        return issues

    # ====================== RAG 批量判定 + 缓存 ======================
    def _judge_mixed_cached(self, college_id, rules_by_id, items):
        """合并判定分片并发 + 缓存；一次调用最多判 MIXED_BATCH_SIZE 个片段。"""
        batches = [items[i:i + MIXED_BATCH_SIZE]
                   for i in range(0, len(items), MIXED_BATCH_SIZE)]

        def judge_one(i, batch):
            key = (college_id, "mixed-v2", hashlib.sha256(json.dumps(
                {"rules": rules_by_id, "items": batch,
                 "model": str(getattr(self.rag, "llm_model", ""))},
                ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest())
            if self.enable_cache:
                with self._lock:
                    hit = self._cache.get(key)
                    if hit is not None:
                        self.stats["cache_hits"] += 1
                        return i, hit
            try:
                res = self.rag.judge_compliance_mixed_batch(
                    college_id, rules_by_id, batch)
            except TimeoutError:
                res = [{"status": "unknown", "reason": "timeout",
                        "compliant": None, "conclusion": "LLM 调用超时，跳过判定",
                        "basis": "", "suggestion": ""}] * len(batch)
            except Exception:
                res = [{"status": "unknown", "reason": "llm_error",
                        "compliant": None, "conclusion": "LLM 调用失败，跳过判定",
                        "basis": "", "suggestion": ""}] * len(batch)
            if not isinstance(res, list) or len(res) != len(batch) or not all(isinstance(r, dict) for r in res):
                res = [{"status": "unknown", "reason": "invalid_schema",
                        "compliant": None, "conclusion": "批量结果数量或类型错误"} for _ in batch]
            with self._lock:
                self.stats["llm_calls"] += 1
                if self.enable_cache and all(isinstance(r.get("compliant"), bool) for r in res):
                    self._cache[key] = res
            return i, res

        if len(batches) == 1:
            _, res = judge_one(0, batches[0])
            return res

        ordered = [None] * len(batches)
        workers = min(self.max_workers, len(batches))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(judge_one, i, b)
                       for i, b in enumerate(batches)]
            for fut in as_completed(futures):
                try:
                    i, res = fut.result()
                    ordered[i] = res
                except Exception:
                    # 单个分片失败不拖垮整体，稍后兜底
                    pass

        # 兜底：失败分片按合规
        flat = []
        for i, res in enumerate(ordered):
            if res is None:
                res = [{"status": "unknown", "reason": "llm_error",
                        "compliant": None, "conclusion": "分片判定失败，跳过",
                        "basis": "", "suggestion": ""}] * len(batches[i])
            flat.extend(res)
        return flat
