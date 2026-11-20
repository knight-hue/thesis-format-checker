import request, { asApiError, ApiError } from './request'
import type { components } from '../types/api.gen'
import type { PendingJob, PaperFile, ProgressCallback, CheckJobStatus } from '../types/store'

const CREATE_TIMEOUT = 60000
const QUERY_TIMEOUT = 10000
const POLL_INTERVAL = 1000
const MAX_TRANSIENT_ERRORS = 5
const PENDING_JOB_KEY = 'thesis_pending_check_job'

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

function generateUuid() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') {
    return globalThis.crypto.randomUUID()
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
    const value = Math.floor(Math.random() * 16)
    const digit = char === 'x' ? value : (value & 0x3) | 0x8
    return digit.toString(16)
  })
}

function savePendingJob(job: PendingJob) {
  try {
    sessionStorage.setItem(PENDING_JOB_KEY, JSON.stringify(job))
  } catch {
    // sessionStorage 不可用时仍可在当前调用中完成轮询，只是不支持刷新恢复。
  }
}

export function getPendingCheckJob(): PendingJob | null {
  try {
    const raw = sessionStorage.getItem(PENDING_JOB_KEY)
    if (!raw) return null
    const job: Partial<PendingJob> | null = JSON.parse(raw)
    return job && typeof job.jobId === 'string' && typeof job.fileName === 'string'
      && typeof job.startedAt === 'number' && typeof job.collegeId === 'string'
      && typeof job.includeSemantic === 'boolean' && typeof job.conversationId === 'string'
      && typeof job.recheck === 'boolean' ? job as PendingJob : null
  } catch {
    return null
  }
}

export function clearPendingCheckJob() {
  try {
    sessionStorage.removeItem(PENDING_JOB_KEY)
  } catch {
    // 忽略浏览器存储不可用
  }
}

function recoverableError(message: string, cause: unknown) {
  const error = new ApiError(message)
  error.recoverable = true
  error.cause = cause
  return error
}

function isTransient(error: unknown) {
  const apiError = asApiError(error)
  return !apiError.status || apiError.code === 'ECONNABORTED' || apiError.code === 'ETIMEDOUT'
}

async function queryStatus(jobId: string) {
  return request.post<CheckJobStatus>('/check-jobs/status', { job_id: jobId }, { timeout: QUERY_TIMEOUT })
}

async function fetchResult(jobId: string) {
  const payload = await request.post<components['schemas']['CheckJobResultResponse']>(
    '/check-jobs/result',
    { job_id: jobId },
    { timeout: QUERY_TIMEOUT },
  )
  return payload.result
}

async function waitForResult(pending: PendingJob, onProgress: ProgressCallback, allowInitialNotFound = false) {
  let transientErrors = 0
  let missingRetries = 0
  while (true) {
    let status: CheckJobStatus
    try {
      status = await queryStatus(pending.jobId)
      transientErrors = 0
    } catch (caught) {
      const error = asApiError(caught)
      if (error.status === 404) {
        if (allowInitialNotFound && missingRetries++ < MAX_TRANSIENT_ERRORS) {
          await delay(POLL_INTERVAL)
          continue
        }
        clearPendingCheckJob()
        throw new Error('检测任务不存在或结果已过期，请重新上传论文')
      }
      if (!isTransient(error) || ++transientErrors >= MAX_TRANSIENT_ERRORS) {
        throw recoverableError('暂时无法查询检测进度，任务 ID 已保存，刷新页面后可继续', error)
      }
      await delay(POLL_INTERVAL)
      continue
    }

    if (typeof onProgress === 'function') onProgress(status)
    if (status.status === 'failed') {
      clearPendingCheckJob()
      throw new Error(status.message || '论文检测失败，请重新上传')
    }
    if (status.status === 'succeeded') {
      try {
        const result = await fetchResult(pending.jobId)
        clearPendingCheckJob()
        return result
      } catch (caught) {
        const error = asApiError(caught)
        if (isTransient(error)) {
          throw recoverableError('检测已经完成，但结果暂时获取失败，刷新页面后可继续', error)
        }
        throw error
      }
    }
    await delay(POLL_INTERVAL)
  }
}

// 上传只等待后端保存并校验文件；耗时的解析/检测通过任务状态轮询。
// 客户端预生成 job_id 并在发送前保存：即使创建请求的响应超时，也能查询同一任务。
export async function parseDocument(
  file: PaperFile,
  collegeId = import.meta.env.VITE_COLLEGE_ID || '',
  includeSemantic = true,
  conversationId = '',
  recheck = false,
  onProgress: ProgressCallback = null,
) {
  const pending = {
    jobId: generateUuid(),
    fileName: file?.name || 'paper.docx',
    collegeId,
    includeSemantic,
    conversationId,
    recheck,
    startedAt: Date.now(),
  }
  savePendingJob(pending)

  const form = new FormData()
  form.append('file', file, file.name || 'paper.docx')
  form.append('job_id', pending.jobId)
  form.append('college_id', collegeId)
  form.append('include_semantic', includeSemantic ? 'true' : 'false')
  if (conversationId) form.append('conversation_id', conversationId)
  if (recheck) form.append('recheck', 'true')

  let creationUncertain = false
  try {
    await request.post<components['schemas']['CheckJobAcceptedResponse']>('/check-jobs/create', form, { timeout: CREATE_TIMEOUT })
  } catch (caught) {
    const error = asApiError(caught)
    // 409 表示相同客户端任务 ID 已被接受；创建响应超时也可能是后端仍在处理上传。
    if (error.status !== 409 && !isTransient(error)) {
      clearPendingCheckJob()
      throw error
    }
    creationUncertain = error.status !== 409
  }
  return waitForResult(pending, onProgress, creationUncertain)
}

export async function resumePendingDocumentCheck(onProgress: ProgressCallback = null) {
  const pending = getPendingCheckJob()
  if (!pending) return null
  const result = await waitForResult(pending, onProgress)
  return { result, pending }
}
