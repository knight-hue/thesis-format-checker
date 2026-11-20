import request from './request'
import type { components } from '../types/api.gen'
import type { ChatResult, RawChatResponse } from '../types/chat'
import type { CollegeInfo } from '../types/store'

// 本地离线模式下单次问答较慢（qwen2.5-1.5b-instruct-q4_k_m 实测约 50 秒），远超默认的 30 秒超时。
const RAG_TIMEOUT = 120000

// RAG 问答（自由问题，不做规则后缀拼接），返回三段式答案。
// conversationId 非空时后端启用多轮对话记忆。
export function normalizeChatResult(data: RawChatResponse): ChatResult {
  return {
    conclusion: data.conclusion ?? '',
    basis: data.basis ?? '',
    suggestion: data.suggestion ?? '',
    sources: Array.isArray(data.sources) ? data.sources : [],
    intent: (data.intent || 'spec_qa') as ChatResult['intent'],
    actions: Array.isArray(data.actions) ? data.actions : [],
    blocks: Array.isArray(data.blocks) ? data.blocks : [],
    suggestions: Array.isArray(data.suggestions) ? data.suggestions : [],
    clarify: data.clarify ?? null,
    answerSource: data.answer_source ?? 'spec_kb',
    responseId: data.response_id ?? '',
    trace: Array.isArray(data.trace) ? data.trace : [],
    fromCache: data.from_cache === true,
    paperVersion: data.paper_version ?? '',
  }
}

export async function askRag(collegeId: string, question: string, conversationId: string | null = null, specOnly = false): Promise<ChatResult> {
  const body: components['schemas']['RAGRequest'] = {
    college_id: collegeId,
    question,
    spec_only: specOnly,
  }
  if (conversationId) body.conversation_id = conversationId
  return normalizeChatResult(await request.post<RawChatResponse>('/rag/answer', body, { timeout: RAG_TIMEOUT }))
}

// 单条规则解释（保留旧语义）。userQuestion 应为带上下文的问题文本，
// 只传 rule_id 时模型会判定与论文格式无关而拒答。
export function explainRule(collegeId: string, ruleId: string, userQuestion?: string): Promise<ChatResult> {
  const question = (userQuestion || '').trim() || '请解释这条论文格式规范'
  // spec_only：规则解释不是对话，不能让意图层把它拆成“多问题”或论文类问题
  return askRag(collegeId, `${question}（规则编号：${ruleId}）`, null, true)
}

// 可用学院列表（含 学校/学院/专业/模板版本 坐标字段）。
export function listColleges(): Promise<CollegeInfo[]> {
  return request.get<CollegeInfo[]>('/colleges')
}

// 开启“新对话”：清空后端会话的对话历史，保留已绑定的论文检测结果。
export function resetSession(conversationId: string) {
  return request.post<components['schemas']['SessionResetResponse']>('/session/reset', { conversation_id: conversationId })
}

// 回答反馈：只提交评分与元数据，不提交问题/回答正文。
export function submitFeedback(
  responseId: string,
  rating: components['schemas']['ChatFeedbackRequest']['rating'],
  conversationId: string | null,
  intent: string,
  answerSource: string,
) {
  const body: components['schemas']['ChatFeedbackRequest'] = {
    response_id: responseId,
    rating,
    intent,
    answer_source: answerSource,
  }
  if (conversationId) body.conversation_id = conversationId
  return request.post<components['schemas']['ChatFeedbackResponse']>('/rag/feedback', body)
}
