import type { components } from './api.gen'

// 对话协议已进入 OpenAPI（RAGResponse），这里只做 camelCase 展示层映射，
// 不再手写一份并行的接口定义。
export type RagIntent = 'paper_overview' | 'paper_detail' | 'paper_fix'
  | 'paper_basis' | 'paper_coverage'
  | 'spec_qa' | 'out_of_scope' | 'clarify'

export type ChatAction = components['schemas']['ChatActionItem']
export type ClarifyPayload = components['schemas']['ClarifyPayload']
export type ChatAnswerSource = components['schemas']['RAGResponse']['answer_source']
export type ChatIssueCard = components['schemas']['ChatIssueCard']
export type ChatBlock = components['schemas']['ChatBlock']
export type ChatSuggestion = components['schemas']['ChatSuggestion']
export type ChatFeedbackRating = components['schemas']['ChatFeedbackRequest']['rating']
// 兼容旧后端（响应可能还没有新字段）；normalizeChatResult 负责补默认值。
export type RawChatResponse = Partial<components['schemas']['RAGResponse']>

export interface ChatResult {
  conclusion: string
  basis: string
  suggestion: string
  sources: components['schemas']['SourceItem'][]
  intent: RagIntent
  actions: ChatAction[]
  blocks: ChatBlock[]
  suggestions: ChatSuggestion[]
  clarify: ClarifyPayload | null
  answerSource: ChatAnswerSource
  responseId: string
  trace: string[]
  fromCache: boolean
  // 检测结果指纹：与最新回答不同即说明这条消息基于旧结果
  paperVersion: string
}

export interface AnswerSection {
  title: string
  blocks: { html: string; indent: boolean }[]
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  text: string
  result?: ChatResult
  sections?: AnswerSection[]
  feedback?: ChatFeedbackRating | 'sending' | 'failed'
}
