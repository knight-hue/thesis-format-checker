import axios from 'axios'
import type { Issue } from '../types/issue'
import type { PaperFile } from '../types/store'

export interface FixResult {
  blob: Blob
  applied: number
  skipped: number
  fixedIndexes: number[]
  writebackSkipCount: number
  writebackSkips: string[]
}
import { isMockEnabled, mockAdapter } from '../mock'

// 解析 X-Fixed-Indexes：逗号分隔的下标串（后端未返回时为空）。
function parseFixedIndexes(value: unknown): number[] {
  if (!value) return []
  return String(value)
    .split(',')
    .map((part) => Number.parseInt(part.trim(), 10))
    .filter((n) => Number.isInteger(n) && n >= 0)
}

// 解析 X-Writeback-Warnings：后端把「写回阶段被跳过的改动」转义成 ASCII JSON 数组放在响应头里，
// 例如「第2节：段落含域代码，不可安全改写」。明细超过响应头上限时后端只回 X-Writeback-Skip-Count，
// 这里解析失败一律当作「拿不到明细」，由调用方退化成只报条数。
function parseWritebackSkips(value: unknown): string[] {
  if (!value) return []
  try {
    const parsed = JSON.parse(String(value))
    return Array.isArray(parsed) ? parsed.filter((item) => typeof item === 'string') : []
  } catch {
    return []
  }
}

// 写回修复：把用户确认的 issue 列表应用到原始 docx，返回修复后的文件流。
// 用独立 axios 实例，以便读取 X-Applied-Count / X-Skipped-Count / X-Fixed-Indexes 响应头，
// 以及写回明细 X-Writeback-Skip-Count / X-Writeback-Warnings（两者一起构成「写回跳过了什么」）。
// 传 conversationId 时，后端会把本次真正修复的项从会话检测结果中移除，让对话与列表一致。
export async function fixPaper(file: PaperFile, collegeId: string, issues: Issue[], conversationId?: string): Promise<FixResult> {
  const client = axios.create({ baseURL: '/api', timeout: 120000 })
  if (isMockEnabled()) {
    client.defaults.adapter = mockAdapter
  }

  const form = new FormData()
  form.append('file', file, file.name || 'paper.docx')
  form.append('college_id', collegeId)
  form.append('issues_json', JSON.stringify(issues))
  if (conversationId) form.append('conversation_id', conversationId)
  // images 留空：当前检测器不会产出 image.replace / image.insert 类修复项。

  let response
  try {
    response = await client.post<Blob>('/fix', form, { responseType: 'blob' })
  } catch (error) {
    throw new Error(await extractError(error))
  }

  const applied = Number(response.headers?.['x-applied-count'] ?? 0)
  const skipped = Number(response.headers?.['x-skipped-count'] ?? 0)
  const fixedIndexes = parseFixedIndexes(response.headers?.['x-fixed-indexes'])
  const writebackSkipCount = Number(response.headers?.['x-writeback-skip-count'] ?? 0)
  const writebackSkips = parseWritebackSkips(response.headers?.['x-writeback-warnings'])
  return {
    blob: response.data,
    applied,
    skipped,
    fixedIndexes,
    writebackSkipCount,
    writebackSkips,
  }
}

// 调 /api/fix 并触发浏览器下载。
// 返回值除统计外还带上 blob：编排层要用修复后的文件当作新基线去重检。
export async function fixAndDownload(file: PaperFile, collegeId: string, issues: Issue[], conversationId?: string): Promise<FixResult> {
  const { blob, applied, skipped, fixedIndexes, writebackSkipCount, writebackSkips } =
    await fixPaper(file, collegeId, issues, conversationId)
  downloadBlob(blob, 'fixed_paper.docx')
  return { blob, applied, skipped, fixedIndexes, writebackSkipCount, writebackSkips }
}

// 触发浏览器下载（blob URL 必须用完即撤，否则整页生命周期内一直占内存）。
export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

async function extractError(error: unknown): Promise<string> {
  const responseData: unknown = axios.isAxiosError<unknown>(error) ? error.response?.data : undefined
  const data = responseData
  if (typeof Blob !== 'undefined' && data instanceof Blob) {
    try {
      const parsed = JSON.parse(await data.text())
      const detail = parsed?.detail
      return typeof detail === 'string' ? detail : '写回失败，请检查文件和修复项'
    } catch {
      /* 非 JSON blob，走兜底 */
    }
  }
  if (responseData && typeof responseData === 'object' && 'detail' in responseData
    && typeof responseData.detail === 'string') return responseData.detail
  return error instanceof Error ? error.message : '写回失败，请稍后重试'
}
