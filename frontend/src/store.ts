import type { StoreState, ResultSnapshot, CheckStatus, CheckResponse, PaperFile } from './types/store'
import type { Issue } from './types/issue'
import { reactive } from 'vue'
import { loadTheme } from './theme'
import {
  clearPendingFile,
  loadOriginalFile,
  loadPendingFile,
  saveOriginalFile,
  savePendingFile,
} from './utils/idb'
import { attachIds } from './utils/issue'

const CONVERSATION_KEY = 'thesis_conversation_id'
const CHECK_STATUS_KEY = 'thesis_check_status'
// 结果页快照：store 里全是内存态，刷新会丢，靠这份快照把结果页还原回来。
const RESULT_KEY = 'thesis_result_snapshot'
// fixable 是后端检测结果契约的一部分。旧版本快照没有该字段，继续恢复会让
// 结果页把所有「确认修改」复选框误判为不可修复。
const RESULT_SNAPSHOT_VERSION = 1

// 生成会话 ID：优先用 crypto.randomUUID，老浏览器降级。
function genId() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') {
    return globalThis.crypto.randomUUID()
  }
  return `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

// 读取持久化的会话 ID（刷新后仍能聊同一篇论文：后端会话库里存着检测结果）。
function loadConversationId() {
  try {
    return sessionStorage.getItem(CONVERSATION_KEY) || ''
  } catch (e) {
    return ''
  }
}

function loadCheckStatus(): CheckStatus {
  try {
    const status = sessionStorage.getItem(CHECK_STATUS_KEY)
    return status === 'running' || status === 'failed' || status === 'succeeded' ? status : 'idle'
  } catch (e) {
    return 'idle'
  }
}

function persistCheckStatus(status: CheckStatus) {
  store.checkStatus = status
  try {
    sessionStorage.setItem(CHECK_STATUS_KEY, status)
  } catch (e) {
    // 隐私模式下 sessionStorage 不可用，保留内存状态
  }
}

function discardResultSnapshot() {
  try {
    sessionStorage.removeItem(RESULT_KEY)
  } catch (e) {
    // 隐私模式下 sessionStorage 不可用，忽略
  }
}

// 极简全局状态：跨页面共享文件名、检测结果、勾选状态与界面偏好。
export const store = reactive<StoreState>({
  // 默认学院由后端 /api/colleges 返回的首项决定（VITE_COLLEGE_ID 可显式指定）；
  // 不写死某个学院，多学校/多学院时前端不需要跟着改。
  collegeId: import.meta.env.VITE_COLLEGE_ID || '',
  colleges: [],        // 可选学院列表（含 学校/学院/专业/模板版本 坐标字段）
  fileName: '',
  conversationId: loadConversationId(), // 会话 ID，上传检测时写入后端会话上下文
  checkStatus: loadCheckStatus(), // idle / running / failed / succeeded
  errorList: [],       // 真实后端 issue 列表（已注入 _id）
  totalIssues: 0,      // 聚合前的原始问题出现次数
  groupedIssues: 0,    // 聚合后、截断前的逻辑问题数
  returnedIssues: 0,   // 实际返回的问题数
  issuesTruncated: false,
  issueSummary: [],
  passed: null,        // 后端 check_document().passed（后端不返回分数，前端不再自造）
  elapsed: null,       // 前端实测解析耗时（秒）
  breakdown: null,
  unchecked: [],       // 本次没有执行的条款 [{rule_id, reason}]（后端能力缺口，结果页要列出）
  advisoryList: [],    // “AI 建议”的问题（后端 advisory_issues，不计入 totalIssues）
  pendingReview: [],   // “待人工”条目（后端 pending_review，不计入 totalIssues / passed）
  selectedIds: [],     // 已勾选“确认修改”的 issue._id
  isFixing: false,
  includeSemantic: true, // 是否执行 RAG 语义检查（首次检测与修复后重检共用同一开关）
  fixedRounds: 0,      // 已完成「修复 → 重检」的轮次，用于提示当前结果基于修复后的文档
  themeColor: loadTheme(), // 界面强调色（默认学院蓝）
  highlightRuleIds: [],
  highlightTargets: [],
  language: 'zh',      // 'zh' 中文；'en' 英文（暂未实现）
})

// ---------- 结果快照：刷新/重进页面后还原结果页 ----------
// 只放可 JSON 序列化的数据（issue 是纯对象）；原始 File 另走 IndexedDB（见 utils/idb.ts）。
function loadResultSnapshot(): ResultSnapshot | null {
  try {
    const raw = sessionStorage.getItem(RESULT_KEY)
    if (!raw) return null
    const data: Partial<ResultSnapshot> | null = JSON.parse(raw)
    if (!data || typeof data !== 'object') {
      discardResultSnapshot()
      return null
    }
    if (data.snapshotVersion !== RESULT_SNAPSHOT_VERSION) {
      discardResultSnapshot()
      return null
    }
    if (!Array.isArray(data.errorList)) {
      discardResultSnapshot()
      return null
    }
    if (!data.errorList.every((issue) => issue && typeof issue.fixable === 'boolean')) {
      discardResultSnapshot()
      return null
    }
    return data as ResultSnapshot
  } catch (e) {
    return null
  }
}

// 保存当前检测结果快照；写不进去（配额不足/隐私模式）只返回 false，不影响使用。
export function saveResult() {
  try {
    sessionStorage.setItem(RESULT_KEY, JSON.stringify({
      snapshotVersion: RESULT_SNAPSHOT_VERSION,
      fileName: store.fileName,
      collegeId: store.collegeId,
      passed: store.passed,
      elapsed: store.elapsed,
      breakdown: store.breakdown,
      unchecked: store.unchecked,
      advisoryList: store.advisoryList,
      pendingReview: store.pendingReview,
      errorList: store.errorList,
      totalIssues: store.totalIssues,
      groupedIssues: store.groupedIssues,
      returnedIssues: store.returnedIssues,
      issuesTruncated: store.issuesTruncated,
      issueSummary: store.issueSummary,
      selectedIds: store.selectedIds,
      includeSemantic: store.includeSemantic,
      fixedRounds: store.fixedRounds,
    }))
    return true
  } catch (e) {
    return false
  }
}

// 丢弃快照（换一篇论文时用，避免新旧结果串台）。
export function clearResult() {
  try {
    sessionStorage.removeItem(RESULT_KEY)
  } catch (e) {
    // 隐私模式下 sessionStorage 不可用，忽略
  }
}

// 模块加载即还原：没有这一步，刷新后的 Result/Detail 会因为 fileName 为空把用户踢回首页。
const resultSnapshot = loadResultSnapshot()
if (resultSnapshot) {
  store.fileName = resultSnapshot.fileName || ''
  if (resultSnapshot.collegeId) store.collegeId = resultSnapshot.collegeId
  store.passed = resultSnapshot.passed ?? null
  store.elapsed = resultSnapshot.elapsed ?? null
  store.breakdown = resultSnapshot.breakdown ?? null
  // 旧快照没有 unchecked 字段：取不到就是空数组（下次检测会补上），不影响其它字段恢复。
  store.unchecked = Array.isArray(resultSnapshot.unchecked) ? resultSnapshot.unchecked : []
  // 同理：旧快照没有 advisoryList / pendingReview 字段。
  store.advisoryList = attachIds(
    Array.isArray(resultSnapshot.advisoryList) ? resultSnapshot.advisoryList : [])
  store.pendingReview = attachIds(
    Array.isArray(resultSnapshot.pendingReview) ? resultSnapshot.pendingReview : [])
  store.errorList = attachIds(resultSnapshot.errorList)
  store.totalIssues = resultSnapshot.totalIssues ?? 0
  store.groupedIssues = resultSnapshot.groupedIssues ?? 0
  store.returnedIssues = resultSnapshot.returnedIssues ?? 0
  store.issuesTruncated = resultSnapshot.issuesTruncated === true
  store.issueSummary = Array.isArray(resultSnapshot.issueSummary) ? resultSnapshot.issueSummary : []
  persistCheckStatus('succeeded')
  const fixableIds = new Set(store.errorList.filter((issue) => issue.fixable).map((issue) => issue._id))
  store.selectedIds = Array.isArray(resultSnapshot.selectedIds)
    ? resultSnapshot.selectedIds.filter((id) => fixableIds.has(id))
    : []
  if (typeof resultSnapshot.includeSemantic === 'boolean') {
    store.includeSemantic = resultSnapshot.includeSemantic
  }
  store.fixedRounds = Number.isInteger(resultSnapshot.fixedRounds) ? resultSnapshot.fixedRounds : 0
}

// 保证存在会话 ID 并持久化，返回该 ID。
export function ensureConversationId() {
  if (!store.conversationId) {
    store.conversationId = genId()
    try {
      sessionStorage.setItem(CONVERSATION_KEY, store.conversationId)
    } catch (e) {
      // 隐私模式下 sessionStorage 不可用，退化为内存态会话
    }
  }
  return store.conversationId
}

// 原始 File 对象不放 reactive（Proxy 包裹 File 会破坏 FormData/Blob 序列化）。
export const rawSession = {
  originalFile: null as PaperFile | null,
  pendingFile: null as PaperFile | null,
}

export function markCheckStarted(fileName = '') {
  store.highlightRuleIds = []
  store.highlightTargets = []
  store.fileName = fileName
  store.errorList = []
  store.selectedIds = []
  store.totalIssues = 0
  store.groupedIssues = 0
  store.returnedIssues = 0
  store.issuesTruncated = false
  store.issueSummary = []
  store.passed = null
  store.elapsed = null
  store.breakdown = null
  store.unchecked = []
  store.advisoryList = []
  store.pendingReview = []
  clearResult()
  persistCheckStatus('running')
}

export async function rememberPendingFile(file: PaperFile | null) {
  if (!file) return false
  rawSession.pendingFile = file
  store.fileName = file.name || store.fileName
  return savePendingFile(file)
}

export async function resolvePendingFile() {
  if (rawSession.pendingFile) return rawSession.pendingFile
  const restored = await loadPendingFile()
  if (restored) rawSession.pendingFile = restored
  return restored
}

export async function discardPendingFile() {
  rawSession.pendingFile = null
  return clearPendingFile()
}

export function markCheckFailed() {
  // 上传失败时不能保留「当前文件」：侧栏会显示一个未检测成功的文件名，
  // 结果页也会因为 fileName 非空而被当成“有结果”，渲染出未通过/0 条的假页面。
  store.fileName = ''
  store.passed = null
  persistCheckStatus('failed')
}

export function resetCheckStatus() {
  persistCheckStatus('idle')
}

export function missingResultMessage() {
  if (store.checkStatus === 'running') return '论文仍在检测，请返回上传页等待'
  if (store.checkStatus === 'failed') return '检测未完成，请重新上传论文'
  if (store.checkStatus === 'succeeded') return '检测结果未成功保存，请重新上传论文'
  return '尚无检测结果，请先上传论文'
}

// 取原始论文文件：内存里没有（例如刷新过页面）就去浏览器本地库捞回来。
// 注意这里拿到的是「当前基线」——首次上传是用户选的原稿，修复并重检后是修复后的文档。
export async function resolveOriginalFile() {
  if (rawSession.originalFile) return rawSession.originalFile
  const restored = await loadOriginalFile()
  if (restored) rawSession.originalFile = restored
  return restored
}

// 把一次检测结果落地到 store（首次上传与修复后重检共用同一条路径）。
// file 为本次检测的 docx，它同时成为后续修复的新基线：内存与浏览器本地库都换成它。
// opts.fileName 覆盖 store.fileName（首次上传时写用户选的文件名）；
// opts.elapsed 为本次检测耗时（秒）。返回检测出的问题条数。
export async function applyCheckResult(data: CheckResponse, file: PaperFile | null, opts: { fileName?: string; elapsed?: number } = {}) {
  store.highlightRuleIds = []
  store.highlightTargets = []
  store.errorList = attachIds(data.issues)
  store.totalIssues = data.total_issues
  store.groupedIssues = data.grouped_issues
  store.returnedIssues = data.returned_issues
  store.issuesTruncated = data.issues_truncated === true
  store.issueSummary = Array.isArray(data.issue_summary) ? data.issue_summary : []
  store.breakdown = data.breakdown || null
  store.unchecked = Array.isArray(data.unchecked) ? data.unchecked : []
  // “AI 建议”与“待人工”单独成列：不进 errorList（否则与 totalIssues 对不上）。
  store.advisoryList = attachIds(
    Array.isArray(data.advisory_issues) ? data.advisory_issues : [])
  store.pendingReview = attachIds(
    Array.isArray(data.pending_review) ? data.pending_review : [])
  store.passed = data.passed === true
  if (opts.elapsed != null) store.elapsed = opts.elapsed
  if (opts.fileName) store.fileName = opts.fileName
  store.selectedIds = []
  if (file) {
    rawSession.originalFile = file
    await saveOriginalFile(file)
  }
  await discardPendingFile()
  persistCheckStatus('succeeded')
  saveResult() // 快照必须跟着换，否则刷新后又会回到重检前的结果
  return store.errorList.length
}

// 修复 → 重检成功一轮后打点，结果页据此提示「当前结果基于修复后的文档」。
export function markFixedRound() {
  store.fixedRounds += 1
  saveResult()
}

// 当前勾选的 issue 对象列表（用于提交 /api/fix）。
export function selectedIssues() {
  const ids = new Set(store.selectedIds)
  return store.errorList.filter((it) => ids.has(it._id))
}

export function toggleIssue(id: string) {
  const i = store.selectedIds.indexOf(id)
  if (i >= 0) {
    store.selectedIds.splice(i, 1)
  } else {
    store.selectedIds.push(id)
  }
  saveResult() // 勾选状态也要进快照，否则刷新后勾选全丢
}

export function isSelected(id: string) {
  return store.selectedIds.indexOf(id) >= 0
}

// 一键勾选所有可修复条目：不可修复的条目后端会忽略，不应混进提交列表。
export function selectAllFixable() {
  store.selectedIds = store.errorList
    .filter((issue) => issue.fixable === true)
    .map((issue) => issue._id)
  saveResult()
}

export function clearSelection() {
  store.selectedIds = []
  saveResult()
}

// 本次真正修复的条目 _id 集合：fixedIndexes 是 /api/fix 回传的、issues 数组里的下标。
// 用下标映射回 _id 而不是按 value 比对，重复下标也不会重复计数。
export function fixedIssueIdSet(submitted: Issue[], fixedIndexes: number[]) {
  const ids = new Set<string>()
  ;(fixedIndexes || []).forEach((index) => {
    const issue = (submitted || [])[index]
    if (issue && issue._id) ids.add(issue._id)
  })
  return ids
}

// 下载修复后的文档之后调用：把本次真正修复的项从屏幕列表里摘掉，并同步快照。
// submitted 是提交给 /api/fix 的 issue 数组，fixedIndexes 是后端回传的下标。
// 会话（对话上下文）由后端按同一批下标同步删除，这里只负责界面上这一份数据。
// 返回真正摘掉的条数。
export function applyFixedIssues(submitted: Issue[], fixedIndexes: number[]) {
  const ids = fixedIssueIdSet(submitted, fixedIndexes)
  if (!ids.size) return 0

  const before = store.errorList.length
  store.errorList = store.errorList.filter((it) => !ids.has(it._id))
  store.selectedIds = store.selectedIds.filter((id) => !ids.has(id))
  // 全部修完则结论翻成「通过」；还有剩余条目时保持后端原判，不凭前端猜分数
  if (!store.errorList.length) store.passed = true
  saveResult() // 必须刷新快照，否则刷新页面会把已修复的条目又还原回来
  return before - store.errorList.length
}
