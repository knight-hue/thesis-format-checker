import type { components } from './api.gen'
import type { Issue } from './issue'
import type { ThemeColor } from '../theme'

export type CheckStatus = 'idle' | 'running' | 'failed' | 'succeeded'
export type CollegeInfo = components['schemas']['CollegeInfo']
export type IssueCategorySummary = components['schemas']['IssueCategorySummaryResponse']
export type CheckBreakdown = components['schemas']['CheckBreakdownResponse']
export type UncheckedItem = components['schemas']['UncheckedItemResponse']
export type CheckResponse = components['schemas']['CheckResponse']
export type CheckJobStatus = components['schemas']['CheckJobStatusResponse']

export interface StoreState {
  collegeId: string
  colleges: CollegeInfo[]
  fileName: string
  conversationId: string
  checkStatus: CheckStatus
  errorList: Issue[]
  totalIssues: number
  groupedIssues: number
  returnedIssues: number
  issuesTruncated: boolean
  issueSummary: IssueCategorySummary[]
  passed: boolean | null
  elapsed: number | null
  breakdown: CheckBreakdown | null
  unchecked: UncheckedItem[]
  // AI 建议（decision=suggested），不计入 totalIssues。
  advisoryList: Issue[]
  // 规则与模型都无法判定的条目（decision=pending_review），不计入 totalIssues / passed。
  pendingReview: Issue[]
  selectedIds: string[]
  isFixing: boolean
  includeSemantic: boolean
  fixedRounds: number
  themeColor: ThemeColor
  language: 'zh' | 'en'
  highlightRuleIds: string[]
  // 精确高亮：规则号 + 定位（后端 action.targets）；为空时退回按规则号高亮
  highlightTargets: { rule_id: string; location: string }[]
}

export type ResultSnapshot = Pick<StoreState,
  'fileName' | 'collegeId' | 'passed' | 'elapsed' | 'breakdown' | 'unchecked'
  | 'advisoryList' | 'pendingReview' | 'errorList' | 'totalIssues' | 'groupedIssues' | 'returnedIssues'
  | 'issuesTruncated' | 'issueSummary' | 'selectedIds' | 'includeSemantic' | 'fixedRounds'
> & { snapshotVersion: 1 }

// 无 File 构造器的旧浏览器仍保留原有 Blob 降级。
export type PaperFile = Blob & { name?: string; lastModified?: number }

export interface PendingJob {
  jobId: string
  fileName: string
  collegeId: string
  includeSemantic: boolean
  conversationId: string
  recheck: boolean
  startedAt: number
}

export type ProgressCallback = ((status: CheckJobStatus) => void) | null
