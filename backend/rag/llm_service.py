"""生成模型服务门面：只持有生成模型，不加载知识库。

语义判定（compliance）与段落改写（rewrite）只需要"调用 LLM"，不需要检索，
因此把它们从 RAGService 里单独拎出来：检测链路（/api/check、/api/check-file）
只需要本门面就绪即可工作，不必等 Chroma / BM25 索引加载完。

问答链路（RAGService.answer）在此基础上额外需要知识库，见 rag.rag_service。
"""
from __future__ import annotations

import logging

from backend.config import LLM_MAX_TOKENS, LLM_TEMPERATURE
from backend.rag.compliance import ComplianceJudge
from backend.rag.llm_client import (
    create_llm_client,
    documents_may_leave_machine,
    is_local_endpoint,
)
from backend.rag.rewrite import PassageRewriter

logger = logging.getLogger(__name__)


class LLMService:
    """生成模型门面：合规判定、段落改写，以及问答链路的裸生成。"""

    def __init__(self, client=None, llm_model=None):
        if client is None or llm_model is None:
            self.client, self.llm_model = create_llm_client()
        else:
            # 允许复用外部已经建好的客户端（测试与自定义入口用）
            self.client = client
            self.llm_model = llm_model
        # 论文内容外发硬闸：非本机端点且没有显式放行时，语义判定/改写一律不跑。
        self.base_url = str(getattr(self.client, "base_url", "") or "")
        self.documents_allowed = documents_may_leave_machine(self.base_url)
        if self.documents_allowed and not is_local_endpoint(self.base_url):
            logger.warning(
                "论文内容外发已放行（演示/联调模式）：语义判定与改写会发送正文到 %s；"
                "演示结束请关闭 THESIS_ALLOW_CLOUD_LLM_FOR_DOCUMENTS 或 "
                "local_settings.ALLOW_CLOUD_LLM_FOR_DOCUMENTS",
                self.base_url,
            )
        self.compliance_judge = ComplianceJudge(self.client, self.llm_model)
        self.passage_rewriter = PassageRewriter(self.client, self.llm_model)

    def describe(self) -> str:
        """初始化信息里的一行说明。"""
        if not self.documents_allowed:
            guard = "｜论文内容禁止外发（语义判定降级）"
        elif is_local_endpoint(self.base_url):
            guard = ""
        else:
            guard = "｜论文内容已放行外发（演示模式）"
        return f"{self.llm_model}｜{self.client.base_url}{guard}"

    def judge_compliance_mixed_batch(self, college_id, tasks, items):
        """一次判定多条规则的多组片段；实现见 ComplianceJudge.judge_mixed_batch。"""
        return self.compliance_judge.judge_mixed_batch(tasks, items)

    def rewrite_passage(self, college_id, task):
        """改写单个段落使其合规；实现见 PassageRewriter.rewrite。"""
        return self.passage_rewriter.rewrite(task)

    def generate_messages(self, messages, temperature=LLM_TEMPERATURE,
                          max_tokens=LLM_MAX_TOKENS):
        """按完整消息列表生成，保留 system/user 角色。

        规则解释弹窗与离线 explanation SFT 使用同一份 system+user 契约；
        不能把两段拼成一条 user 消息，否则训练和线上看到的输入不一致。
        """
        if not isinstance(messages, (list, tuple)) or not messages:
            raise ValueError("messages must be a non-empty list")
        response = self.client.chat.completions.create(
            model=self.llm_model,
            messages=list(messages),
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
        )
        return response.choices[0].message.content or ""

    def chat_with_tools(self, messages, tools, temperature=0.0,
                        max_tokens=LLM_MAX_TOKENS):
        """带工具调用的对话，返回原始 message（含 tool_calls）。

        只给对话 Agent 使用：temperature 固定 0，保证同样的输入走出同样的路径。
        异常照旧上抛，由 Agent 循环捕获并降级到确定性链路。
        """
        if not isinstance(messages, (list, tuple)) or not messages:
            raise ValueError("messages must be a non-empty list")
        if not tools:
            raise ValueError("tools must be a non-empty list")
        response = self.client.chat.completions.create(
            model=self.llm_model,
            messages=list(messages),
            temperature=temperature,
            max_tokens=max_tokens,
            tools=list(tools),
            tool_choice="auto",
            stream=False,
        )
        return response.choices[0].message

    def generate(self, prompt, temperature=LLM_TEMPERATURE, max_tokens=LLM_MAX_TOKENS):
        """单条 user 消息的便捷入口，保留旧调用。

        与 rag.llm_client 的请求封装不同，这里不做失败兜底：问答主链路的异常
        照旧上抛，由 API 层转成 500，避免把模型故障伪装成"没有相关内容"。
        """
        return self.generate_messages(
            [{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
