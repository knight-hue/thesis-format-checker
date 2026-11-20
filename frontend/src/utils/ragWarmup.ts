import { asApiError } from '../api/request'
import { getHealth } from '../api/system'

// 后端启动后知识库在后台线程加载，这期间的 RAG 请求会失败并提示「初始化中」。
// 这种失败是暂时的，等健康检查报告就绪后重试一次即可，不用麻烦用户。
const RAG_WAIT_LIMIT_MS = 90000
const RAG_WAIT_INTERVAL_MS = 2000

export function isRagWarming(error: unknown) {
  return asApiError(error).status === 503
}

export async function waitRagReady() {
  const deadline = Date.now() + RAG_WAIT_LIMIT_MS
  while (Date.now() < deadline) {
    try {
      const health = await getHealth()
      if (health.rag_initialized) return true
      if (health.rag_status === 'failed') return false
    } catch {
      // 后端整体不可达，再等也没有意义
      return false
    }
    await new Promise((resolve) => setTimeout(resolve, RAG_WAIT_INTERVAL_MS))
  }
  return false
}

// call 里的 RAG 请求若因知识库未就绪失败，就在等待就绪后重试一次；
// onWarming 用于把「正在等待」反馈到界面。
export async function retryWhenRagReady<T>(call: () => Promise<T>, onWarming?: () => void): Promise<T> {
  try {
    return await call()
  } catch (e) {
    if (!isRagWarming(e)) throw e
    // 同为 503，初始化中与初始化失败由结构化健康状态区分，不匹配中文文案。
    const health = await getHealth().catch(() => null)
    if (!health || health.rag_status === 'failed') throw e
    if (health.rag_initialized) return await call()
    if (onWarming) onWarming()
    if (!(await waitRagReady())) throw e
    return await call()
  }
}
