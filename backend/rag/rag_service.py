"""RAG 服务：对外门面。

对外能力与内部实现的对应关系：
- 问答 answer：rag.answer（prompt/解析）+ rag.cache + rag.kb_manager
- 合规判定 judge_compliance*：委托 rag.llm_service（生成模型门面）
- 段落改写 rewrite_passage：委托 rag.llm_service（生成模型门面）
- 检测结果入会话 bind_paper_errors / remove_fixed_issues：rag.session_manager
安全过滤见 rag.safety，生成模型门面见 rag.llm_service。
"""
import logging
import time
import uuid

from backend.config import (
    COLLEGES_CONFIG_PATH,
    USE_QUERY_REWRITE,
    QUERY_REWRITE_MODE,
    USE_CACHE,
    USE_DIALOG_ROUTER,
    DIALOG_MAX_STEPS,
    DIALOG_CLARIFY_ENABLED,
    SESSION_DB_PATH,
    SESSION_MAX_HISTORY_TURNS,
    SESSION_EXPIRE_SECONDS,
)
from backend.rag.answer import (
    ANSWER_SCHEMA_VERSION,
    build_answer_prompt,
    build_norm_qa_messages,
    parse_answer,
)
from backend.rag.answer_guard import GUARD_VERSION, guard_answer
from backend.rag.cache import LRUCache
from backend.rag.dialog.graph import DialogState, run_dialog
from backend.rag.dialog.blocks import suggestions_for
from backend.rag.dialog.agent.router import should_use_agent
from backend.rag.dialog.intent import detect_intent
from backend.rag.kb_manager import KBManager
from backend.rag.llm_service import LLMService
from backend.rag.norm_qa_contract import build_context as build_norm_qa_context
from backend.rag.query_rewriter import RuleQueryRewriter
from backend.rag.safety import check_output_safety, check_prompt_injection
from backend.rag.session_manager import result_version

logger = logging.getLogger(__name__)


# ========== RAG 服务 ==========
class RAGService:
    """RAG 服务主类：问答编排与检测结果入会话，以及语义判定/段落改写的对外出口。

    自身负责问答链路（安全过滤 → 缓存 → 查询改写 → 检索 → 生成 → 解析）、
    检测结果与对话会话的绑定/同步，以及运行统计；判定与改写委托给生成模型门面
    （rag.llm_service.LLMService），检索与索引归 kb_manager。
    """

    def __init__(self, config_path=COLLEGES_CONFIG_PATH, llm_service=None):
        # 初始化知识库管理器
        self.kb_manager = KBManager(config_path)

        # 生成模型门面。
        # 由调用方传入时复用同一个门面，检测链路与问答链路共用一份客户端。
        self.llm = llm_service or LLMService()

        # 初始化查询改写器
        self.query_rewriter = None
        if USE_QUERY_REWRITE and QUERY_REWRITE_MODE == "rule":
            self.query_rewriter = RuleQueryRewriter()

        # 结果缓存
        self.cache = LRUCache() if USE_CACHE else None

        from backend.rag.session_manager import SessionManager
        self.session_manager = SessionManager(
            db_path=SESSION_DB_PATH,
            expire_seconds=SESSION_EXPIRE_SECONDS,
            max_history_turns=SESSION_MAX_HISTORY_TURNS
        )

        # 统计信息
        self.stats = {
            "total_requests": 0,
            "cache_hits": 0,
            "rewrite_count": 0,
            "total_time": 0,
            "total_search_time": 0,
            "total_generate_time": 0,
            "total_rewrite_time": 0,
        }

        # 打印初始化信息
        print("✅ RAG 服务初始化完成")
        print(f"   - 生成模型：{self.llm.describe()}")
        print(f"   - 查询改写：{'规则版' if USE_QUERY_REWRITE and self.query_rewriter else '未启用'}")
        print(f"   - 结果缓存：{'启用' if USE_CACHE else '未启用'}")
        print(f"   - 可用学院：{len(self.kb_manager.list_colleges())} 个\n")

    def answer(self, college_id, question, conversation_id=None, spec_only=False):
        """
        回答问题 - 主入口
        完整流程：
        0. 会话上下文（读取/创建会话：按当前问题召回论文检测结果，并叠加多轮对话历史）
        1. 输入安全检查
        2. 缓存检查（用原始问题 + 论文上下文指纹做key；有对话历史时跳过）
        3. 查询改写（规则版，传入学院词汇表）
        4. 检索
        5. 生成答案
        6. 输出安全检查
        7. 返回结果（并写回对话历史）
        """
        start_time = time.time()
        self.stats["total_requests"] += 1

        # 原始问题（用于缓存key和返回）
        original_question = question

        # ===== 第0层：会话上下文（多轮对话记忆 + 论文检测结果）=====
        paper_text = ""
        history_text = ""
        paper_sig = ""
        if conversation_id:
            try:
                if self.session_manager.get_session(conversation_id) is None:
                    self.session_manager.create_session(conversation_id, college_id)
                paper_text = self.session_manager.build_paper_context(
                    conversation_id, original_question
                )
                history_text = self.session_manager.build_history_context(conversation_id)
                paper_sig = self.session_manager.paper_fingerprint(conversation_id)
            except Exception as exc:
                # 会话存储不可用时降级为无记忆的单轮问答，不阻断主流程
                logger.warning("会话记忆不可用，已降级为单轮问答：%s", exc)
                conversation_id = None
                paper_text = ""
                history_text = ""

        session_context = "\n\n".join(t for t in (paper_text, history_text) if t)
        # 论文检测结果会影响答案，用指纹纳入缓存 key；对话历史逐轮变化，有历史时直接跳过缓存

        # ===== 第1层：输入安全检查 =====
        injection_detected, reason = check_prompt_injection(question)
        if injection_detected:
            return self._make_result(college_id, original_question, found=False,
                                     conclusion="检测到可疑内容，无法回答",
                                     basis="安全策略",
                                     suggestion="请您提出与论文格式相关的正常问题",
                                     safety_warning=reason,
                                     intent="out_of_scope",
                                     answer_source="none",
                                     paper_version=paper_sig,
                                     total_time=time.time() - start_time)

        # ===== 第1.5层：对话编排（开关关闭时完全走原链路）=====
        # spec_only：调用方（如规则解释弹窗）明确要求只走规范问答，不让自然语言分类接管
        if USE_DIALOG_ROUTER and not spec_only:
            session = None
            if conversation_id:
                try:
                    session = self.session_manager.get_session(conversation_id)
                except Exception as exc:
                    logger.warning("读取对话状态失败，本轮按规范问答处理：%s", exc)
            paper_errors = (session or {}).get("paper_errors") or []
            paper_total = (session or {}).get("paper_total") or 0
            has_paper = bool((session or {}).get("paper_checked"))
            pending_clarify = (session or {}).get("pending_clarify")
            last_listed = (session or {}).get("last_listed") or []
            intent = detect_intent(
                question,
                has_paper,
                paper_errors,
                pending_clarify=pending_clarify,
                last_listed=last_listed,
                clarify_enabled=DIALOG_CLARIFY_ENABLED,
                last_scope=(session or {}).get("last_scope") or {},
            )
            # 澄清状态只活一轮：本轮无论消费还是丢弃，都先清掉
            if conversation_id and pending_clarify:
                try:
                    self.session_manager.set_pending_clarify(conversation_id, None)
                except Exception as exc:
                    logger.warning("清除澄清状态失败（忽略）：%s", exc)
            if conversation_id:
                try:
                    self.session_manager.set_last_intent(conversation_id, intent.name)
                except Exception as exc:
                    logger.warning("记录最近意图失败（忽略）：%s", exc)
            logger.info("对话意图：intent=%s reason=%s", intent.name, intent.reason)
            if should_use_agent(intent, original_question, has_paper):
                agent_result = self._answer_agent(
                    college_id, original_question, conversation_id, intent, start_time,
                )
                if agent_result is not None:
                    return agent_result
            if intent.name == "spec_qa" and intent.slots.get("heading_level"):
                level = intent.slots.get("heading_level")
                attribute = intent.slots.get("attribute") or "字体字号"
                question = f"{level}标题的{attribute}要求是什么？"
            elif intent.name != "spec_qa":
                return self._answer_dialog(
                    college_id, original_question, conversation_id, intent, start_time,
                )

        # ===== 第2层：缓存检查（用原始问题做key；有历史上下文时跳过缓存避免串记忆）=====
        # guard 版本进入 key：旧版本 guard 生成的缓存不能绕过新 guard。
        cache_key = f"{GUARD_VERSION}:{ANSWER_SCHEMA_VERSION}:{college_id}:{paper_sig}:{original_question}"
        if self.cache and not history_text:
            cached = self.cache.get(cache_key)
            if cached:
                self.stats["cache_hits"] += 1
                cached["total_time"] = time.time() - start_time
                cached["from_cache"] = True
                cached["response_id"] = uuid.uuid4().hex
                cached["paper_version"] = paper_sig
                return cached

        # ===== 第3层：查询改写 =====
        rewrite_info = None
        rewrite_time = 0
        if USE_QUERY_REWRITE and self.query_rewriter:
            rewrite_start = time.time()
            # ===== 改动2：传入学院词汇表，只补知识库中存在的词 =====
            college_vocab = self.kb_manager.get_vocab(college_id)
            question, rewrite_info = self.query_rewriter.rewrite(question, college_vocab=college_vocab)
            rewrite_time = time.time() - rewrite_start
            self.stats["total_rewrite_time"] += rewrite_time
            if rewrite_info and rewrite_info.get("changed"):
                self.stats["rewrite_count"] += 1

        # ===== 第4层：检索 =====
        search_start = time.time()
        retriever = self.kb_manager.get_retriever(college_id)
        retrieved_docs = retriever.invoke(question)
        search_time = time.time() - search_start
        recall_count = len(retrieved_docs)
        self.stats["total_search_time"] += search_time

        # 没检索到 → 回答未找到
        if len(retrieved_docs) == 0:
            result = self._make_result(college_id, original_question, found=False,
                                       conclusion="未检索到匹配的论文格式规范",
                                       basis="无对应条款",
                                       suggestion="请查阅学院完整论文格式规范原文确认要求",
                                       search_time=search_time,
                                       recall_count=recall_count,
                                       rewrite_info=rewrite_info,
                                       rewrite_time=rewrite_time,
                                       paper_version=paper_sig,
                                       total_time=time.time() - start_time)
            if self.cache and not history_text:
                self.cache.set(cache_key, result)
            if conversation_id:
                self.session_manager.update_history(
                    conversation_id, original_question, result["conclusion"])
            return result

        # ===== 第5层：生成答案 =====
        context, sources = self._build_context(retrieved_docs)

        if spec_only:
            # 规则解释弹窗是规范问答：只要 question + clauses + version，
            # 不伪造 observation/tool_result。训练端使用同一份 norm_qa 契约。
            norm_qa_context = build_norm_qa_context(
                college_id, question,
                self.kb_manager.colleges.get(college_id) or {},
                retrieved_docs,
            )
            spec_messages = build_norm_qa_messages(norm_qa_context)
            prompt = None
        else:
            spec_messages = None
            prompt = build_answer_prompt(context, question, session_context)

        # 调用大模型（走生成模型门面，由开关决定是外部还是本地）
        generate_start = time.time()
        if spec_messages is not None:
            # 保留 system/user 两条消息，与训练时的 chat template 一致。
            answer_text = self.llm.generate_messages(spec_messages)
        else:
            answer_text = self.llm.generate(prompt)
        generate_time = time.time() - generate_start
        self.stats["total_generate_time"] += generate_time

        # ===== 第6层：输出安全检查 =====
        output_safe, safety_reason = check_output_safety(answer_text)
        if not output_safe:
            return self._make_result(college_id, original_question, found=False,
                                     conclusion="回答内容可能存在安全风险，已被拦截",
                                     basis="安全策略",
                                     suggestion="请您提出与论文格式相关的正常问题",
                                     safety_warning=safety_reason,
                                     answer_source="none",
                                     paper_version=paper_sig,
                                     total_time=time.time() - start_time)

        # ===== 第7层：解析三段式回答 + 安全 guard =====
        parsed = parse_answer(answer_text)
        guarded = guard_answer(original_question, parsed, sources, college_id, session_context)
        if guarded["changed"]:
            logger.warning("RAG 回答被安全 guard 改写：reasons=%s", guarded["reasons"])
        parsed = guarded
        answer_text = guarded["text"]
        result = self._make_result(
            college_id, original_question,
            found=True,
            conclusion=parsed["conclusion"],
            basis=parsed["basis"],
            suggestion=parsed["suggestion"],
            can_judge=parsed.get("can_judge"),
            observed=parsed.get("observed", []),
            unknown=parsed.get("unknown", []),
            answer_schema_version=parsed.get("answer_schema_version", "1.0"),
            full_answer=answer_text,
            sources=sources,
            search_time=search_time,
            recall_count=recall_count,
            generate_time=generate_time,
            rewrite_info=rewrite_info,
            rewrite_time=rewrite_time,
            paper_version=paper_sig,
            total_time=time.time() - start_time
        )

        # 写回对话历史（多轮记忆）
        if conversation_id:
            self.session_manager.update_history(
                conversation_id, original_question, answer_text)

        # 存入缓存（用原始问题做key；有历史上下文时不缓存）
        if self.cache and not history_text:
            self.cache.set(cache_key, result)
        self.stats["total_time"] += result["total_time"]
        return result

    def _answer_agent(self, college_id, original_question, conversation_id,
                      intent, start_time):
        """复合/兜底请求交给 ReAct Agent；返回 None 表示应当降级到确定性链路。

        Agent 只读工具、最多几步、输出必须通过事实校验；任何一步不满足都返回 None，
        由调用方回落到 ``_answer_dialog``，保证「Agent 挂了功能不挂」。
        """
        from backend.rag.dialog.agent import loop as agent_loop
        from backend.rag.dialog.agent.tools import ToolContext, build_card_blocks
        from backend.rag.dialog import blocks as dialog_blocks

        try:
            from backend import config as _config
            max_steps = int(getattr(_config, "AGENT_MAX_STEPS", 4) or 4)
        except Exception:  # pragma: no cover - 配置异常时用默认值
            max_steps = 4

        session = {}
        if conversation_id:
            try:
                session = self.session_manager.get_session(conversation_id) or {}
            except Exception as exc:
                logger.warning("Agent 读取会话失败，按无上下文处理：%s", exc)
                session = {}

        ctx = ToolContext(
            college_id=college_id,
            college_name=str(((self.kb_manager.colleges.get(college_id) or {}).get("college_name")
                              or (self.kb_manager.colleges.get(college_id) or {}).get("name")
                              or "")),
            paper_errors=session.get("paper_errors") or [],
            paper_total=int(session.get("paper_total") or 0),
            paper_unchecked=session.get("paper_unchecked"),
            last_listed=session.get("last_listed") or [],
            last_scope=session.get("last_scope") or {},
            spec_answer=lambda query: self.answer(
                college_id, query, conversation_id=None, spec_only=True),
        )

        run = agent_loop.run_agent(
            original_question, ctx,
            agent_loop.service_chat_fn(self.llm),
            max_steps=max_steps,
        )
        if not run.ok:
            logger.info("Agent 未产出可用答案（%s，steps=%s），降级到确定性链路",
                        run.stops, run.steps)
            return None

        answer = run.answer
        cards = build_card_blocks(ctx, answer.get("cards"))
        result = self._make_result(
            college_id, original_question,
            found=True,
            conclusion=answer.get("conclusion", ""),
            basis=answer.get("basis", ""),
            suggestion=answer.get("suggestion", ""),
            actions=[{"type": "open-result"}] if cards else [],
            blocks=cards or dialog_blocks.text_blocks(answer.get("conclusion", "")),
            suggestions=suggestions_for(
                intent.name, {"matched_total": int(ctx.paper_total or 0)}),
            answer_source="agent",
            intent=intent.name,
            trace=list(run.trace),
            paper_version=result_version(ctx.paper_errors, ctx.paper_total),
            total_time=time.time() - start_time,
        )
        if conversation_id:
            try:
                self.session_manager.update_history(
                    conversation_id, original_question, answer.get("conclusion", ""))
            except Exception as exc:
                logger.warning("Agent 写入对话历史失败（忽略）：%s", exc)
        self.stats["total_time"] += result["total_time"]
        return result

    def _answer_dialog(self, college_id, original_question, conversation_id, intent, start_time):
        """论文类/澄清/越界分支：事实层直接拼装，不检索、不调用模型。

        规范问答（spec_qa）不进入这里，仍走原有七层链路，保证行为不变。
        """
        session = {}
        if conversation_id:
            try:
                session = self.session_manager.get_session(conversation_id) or {}
            except Exception as exc:
                logger.warning("读取会话失败，按无上下文处理：%s", exc)
        if conversation_id:
            try:
                self.session_manager.set_last_intent(conversation_id, intent.name)
            except Exception as exc:
                logger.warning("记录最近意图失败（忽略）：%s", exc)
        errors = session.get("paper_errors") or []
        paper_total = session.get("paper_total") or 0
        paper_version = result_version(errors, paper_total)
        state = DialogState(
            question=original_question,
            intent=intent,
            has_paper=bool(session.get("paper_checked")),
            paper_errors=errors,
            paper_total=paper_total,
            paper_unchecked=session.get("paper_unchecked"),
            last_listed=session.get("last_listed") or [],
            pending_clarify=session.get("pending_clarify"),
            last_scope=session.get("last_scope") or {},
        )

        def spec_stub(_question):
            # 论文/澄清/越界分支不应落到规范问答；保留保守兜底，避免护栏触发时无结果
            return self._make_result(
                college_id, original_question, found=False,
                conclusion="未检索到匹配的论文格式规范",
                basis="无对应条款",
                suggestion="请查阅学院完整论文格式规范原文确认要求",
                answer_source="spec_kb",
                paper_version=paper_version,
                total_time=time.time() - start_time,
            )

        run_dialog(state, spec_stub, max_steps=DIALOG_MAX_STEPS)
        outcome = state.outcome
        slots = {key: value for key, value in (intent.slots or {}).items()
                 if key != "candidates" and value not in ("", [], None)}

        if outcome.spec_result is not None:
            result = outcome.spec_result
        else:
            result = self._make_result(
                college_id, original_question,
                found=outcome.found,
                conclusion=outcome.conclusion,
                basis=outcome.basis,
                suggestion=outcome.suggestion,
                actions=outcome.actions,
                blocks=outcome.blocks,
                suggestions=outcome.suggestions,
                clarify=outcome.clarify,
                answer_source=outcome.answer_source,
                intent=intent.name,
                slots=slots,
                paper_version=paper_version,
                total_time=time.time() - start_time,
            )
            if conversation_id and outcome.history_answer:
                try:
                    self.session_manager.update_history(
                        conversation_id, original_question, outcome.history_answer)
                except Exception as exc:
                    logger.warning("写入对话历史失败（忽略）：%s", exc)
            self.stats["total_time"] += result["total_time"]

        result["trace"] = outcome.trace or []

        if conversation_id:
            try:
                self.session_manager.set_pending_clarify(conversation_id, outcome.pending_clarify)
                if outcome.last_listed is not None:
                    self.session_manager.set_last_listed(conversation_id, outcome.last_listed)
                if outcome.last_scope is not None:
                    self.session_manager.set_last_scope(conversation_id, outcome.last_scope)
            except Exception as exc:
                logger.warning("保存对话状态失败（忽略）：%s", exc)
        return result

    def reset_history(self, conversation_id):
        """清空会话对话历史，保留论文检测结果（用于前端“新对话”）。"""
        try:
            return self.session_manager.clear_history(conversation_id)
        except Exception as exc:
            logger.warning("清空对话历史失败，已忽略：%s", exc)
            return False

    def save_feedback(self, response_id, conversation_id="", rating="", intent="",
                      answer_source=""):
        """保存回答反馈；只写评分与元数据，不写论文内容。"""
        try:
            return self.session_manager.save_feedback(
                response_id=response_id,
                conversation_id=conversation_id,
                rating=rating,
                intent=intent,
                answer_source=answer_source,
            )
        except Exception as exc:
            logger.warning("保存对话反馈失败，已忽略：%s", exc)
            return False

    def bind_paper_errors(self, conversation_id, college_id, issues, keep_history=False,
                          unchecked=None):
        """把一次检测结果写入会话，作为多轮问答的问题上下文。

        实现见 SessionManager.bind_paper_errors；会话存储不可用时静默降级，
        不影响检测结果返回，只是对话里没有论文上下文。
        unchecked 为本次检测「没有执行的条款」，对话里的「哪些没查」只认它。
        """
        if not conversation_id:
            return
        try:
            self.session_manager.bind_paper_errors(
                conversation_id, college_id, issues, keep_history=keep_history,
                unchecked=unchecked,
            )
        except Exception as exc:
            logger.warning(
                "写入会话检测结果失败，已忽略：conversation_id=%s %s", conversation_id, exc
            )

    def remove_fixed_issues(self, conversation_id, issues):
        """下载修复结果后，把已修复的问题从会话检测结果里同步删掉。

        实现见 SessionManager.remove_paper_errors；失败静默降级，绝不影响下载。
        """
        if not conversation_id or not issues:
            return
        try:
            self.session_manager.remove_paper_errors(conversation_id, issues)
        except Exception as exc:
            logger.warning(
                "同步会话中的检测结果失败，已忽略：conversation_id=%s %s", conversation_id, exc
            )

    def judge_compliance_mixed_batch(self, college_id, tasks, items):
        """一次判定多条规则的多组片段；实现见 LLMService.judge_compliance_mixed_batch。"""
        return self.llm.judge_compliance_mixed_batch(college_id, tasks, items)

    @property
    def documents_allowed(self):
        """论文内容能否发给当前模型端点（非本机端点默认 False）。"""
        return bool(getattr(self.llm, "documents_allowed", False))

    def rewrite_passage(self, college_id, task):
        """改写单个段落使其合规；实现见 LLMService.rewrite_passage。"""
        return self.llm.rewrite_passage(college_id, task)

    @staticmethod
    def _build_context(retrieved_docs):
        """把检索结果整理成 prompt 上下文，并生成返回给前端的 sources。"""
        context_blocks = []
        sources = []
        for idx, doc in enumerate(retrieved_docs):
            full_path = doc.metadata.get("full_path", "未知章节")
            rerank_score = doc.metadata.get("rerank_score", None)
            context_blocks.append(
                f"【规范片段{idx + 1}｜章节：{full_path}"
                f"{'｜相关度：' + f'{rerank_score:.4f}' if rerank_score else ''}】\n"
                f"{doc.page_content}"
            )
            sources.append({
                "chapter": doc.metadata.get("chapter", "未知章节"),
                "section": doc.metadata.get("section", ""),
                "full_path": full_path,
                "content": doc.page_content,
                "preview": doc.page_content[:70].replace('\n', ' '),
                "rerank_score": rerank_score
            })
        return "\n\n".join(context_blocks), sources

    def _make_result(self, college_id, question, **kwargs):
        """构造统一的返回结果"""
        college_info = self.kb_manager.colleges[college_id]
        display_name = (
            college_info.get("college_name")
            or college_info.get("name")
            or college_info.get("college")
            or college_id
        )
        result = {
            "college": display_name,
            "question": question,
            "conclusion": "",
            "basis": "",
            "suggestion": "",
            "can_judge": None,
            "observed": [],
            "unknown": [],
            "answer_schema_version": "1.0",
            "sources": [],
            "found": False,
            "from_cache": False,
            "rewrite_info": None,
            "rewrite_time": 0,
            "search_time": 0,
            "generate_time": 0,
            "total_time": 0,
            # 对话编排字段：默认值与旧行为一致（纯规范问答）
            "intent": "spec_qa",
            "slots": {},
            "actions": [],
            "blocks": [],
            "suggestions": [],
            "clarify": None,
            "answer_source": "spec_kb",
            "response_id": uuid.uuid4().hex,
            "trace": [],
            "paper_version": "",
        }
        result.update(kwargs)
        if not result.get("suggestions"):
            result["suggestions"] = suggestions_for(result.get("intent") or "spec_qa")
        return result

    def get_stats(self):
        """获取统计信息"""
        total = self.stats["total_requests"]
        avg_time = self.stats["total_time"] / total if total > 0 else 0
        cache_hit_rate = self.stats["cache_hits"] / total * 100 if total > 0 else 0
        avg_search = self.stats["total_search_time"] / total if total > 0 else 0
        avg_generate = self.stats["total_generate_time"] / total if total > 0 else 0
        avg_rewrite = self.stats["total_rewrite_time"] / total if total > 0 else 0
        rewrite_rate = self.stats["rewrite_count"] / total * 100 if total > 0 else 0
        return {
            "total_requests": total,
            "cache_hits": self.stats["cache_hits"],
            "cache_hit_rate": f"{cache_hit_rate:.1f}%",
            "rewrite_count": self.stats["rewrite_count"],
            "rewrite_rate": f"{rewrite_rate:.1f}%",
            "avg_response_time": f"{avg_time * 1000:.0f}ms",
            "avg_rewrite_time": f"{avg_rewrite * 1000:.1f}ms",
            "avg_search_time": f"{avg_search * 1000:.0f}ms",
            "avg_generate_time": f"{avg_generate * 1000:.0f}ms",
        }
