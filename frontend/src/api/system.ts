import type { components } from '../types/api.gen'
import request from './request'

// 健康检查。rag_status 为 initializing 表示后端已可用、知识库仍在后台加载；
// 就绪后 rag_initialized 变 true，failed 表示初始化失败（AI 能力不可用）。
export function getHealth() {
  return request.get<components['schemas']['HealthResponse']>('/health', { timeout: 5000 })
}
