// 「修复 → 下载 → 基线前移 → 自动重检」的编排。
//
// 为什么修复之后必须重检：修复器补表题/图题时是往文档里 *插入* 段落块，插入点之后所有段的
// location.index 全部漂移（实测只补一条表题，就有 5 条规则的下标错位一格）。若拿旧下标再去修，
// 轻则漏修（勾了 5 条只改到 4 条），重则改到没勾选的那个段落。所以每轮修复后都用修复稿重跑一次
// 检测，让屏幕上的清单与下标重新对齐到当前这份文档。
//
// 基线前移：修复稿同时成为下一次修复的输入（内存 + 浏览器本地库都换成它），
// 于是多轮修复自然叠加，用户不必每次重新上传。
import {
  store,
  applyCheckResult,
  applyFixedIssues,
  fixedIssueIdSet,
  markFixedRound,
  rememberPendingFile,
  discardPendingFile,
  resolveOriginalFile,
} from '../store'
import { asApiError } from '../api/request'
import type { Issue } from '../types/issue'
import type { PaperFile } from '../types/store'
import { fixAndDownload } from '../api/fix'
import { parseDocument } from '../api/parser'

interface WritebackOutcome {
  writebackSkipCount: number
  writebackSkips: string[]
}
type AppliedOutcome = WritebackOutcome & { applied: number; skipped: number; removed: number }
export type FixOutcome =
  | { kind: 'no-file' }
  | (AppliedOutcome & { kind: 'no-change'; left: number })
  | (AppliedOutcome & { kind: 'rechecked'; remaining: number; fresh: number; passed: boolean })
  | (AppliedOutcome & { kind: 'recheck-pending'; reason: string })
  | (AppliedOutcome & { kind: 'recheck-failed'; reason: string; left: number })
type FixMessage = { type: 'success' | 'info' | 'warning'; text: string }

const DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'

// 把修复后的文件流包成新基线 File：FormData 上传需要文件名，沿用原文件名以便识别是同一篇论文。
function toBaselineFile(blob: Blob): PaperFile {
  const name = store.fileName || 'fixed_paper.docx'
  if (typeof File !== 'function') return blob
  return new File([blob], name, {
    type: blob.type || DOCX_MIME,
    lastModified: Date.now(),
  })
}

// 修复并下载，随后把修复稿作为新基线并自动重检。
// issues：提交给 /api/fix 的 issue 数组（store.selectedIssues()）。
// 返回结果对象，交给 describeFixOutcome 转成提示文案；网络/后端错误直接抛给调用方。
export async function runFixFlow(issues: Issue[]): Promise<FixOutcome> {
  const file = await resolveOriginalFile()
  if (!file) return { kind: 'no-file' }

  const {
    blob,
    applied,
    skipped,
    fixedIndexes,
    writebackSkipCount,
    writebackSkips,
  } = await fixAndDownload(file, store.collegeId, issues, store.conversationId)
  // 写回跳过明细被每个分支共用：挂到 outcome 上，由 describeFixOutcome 统一呈现
  const writeback = { writebackSkipCount, writebackSkips }
  const removed = fixedIssueIdSet(issues, fixedIndexes).size

  // 一条都没改到（都不支持自动修复）：文档原样返回，基线与列表都不用动
  if (applied === 0) {
    return { kind: 'no-change', applied, skipped, removed, left: issues.length, ...writeback }
  }

  const baseline = toBaselineFile(blob)
  // 本轮修复前屏幕上的问题总数：重检后若条数没按「旧总数 - 已修条数」下降，多出来的就是新问题
  const prevTotal = store.errorList.length
  const startedAt = performance.now()
  await rememberPendingFile(baseline)
  try {
    const data = await parseDocument(
      baseline,
      store.collegeId,
      store.includeSemantic,
      store.conversationId,
      true, // recheck：保留对话历史，只换问题列表
    )
    const remaining = await applyCheckResult(data, baseline, {
      elapsed: Number(((performance.now() - startedAt) / 1000).toFixed(1)),
    })
    markFixedRound()
    // 修复后新冒出来的问题（例如文字改写引入的语义问题）：
    // 预期剩余 = 旧总数 - 真正修掉的条数，实际剩余比它多出来的就是新问题
    const expectedLeft = Math.max(0, prevTotal - removed)
    return {
      kind: 'rechecked',
      applied,
      skipped,
      removed,
      remaining,
      fresh: Math.max(0, remaining - expectedLeft),
      passed: store.passed === true,
      ...writeback,
    }
  } catch (caught) {
    const e = asApiError(caught)
    if (e.recoverable) {
      return {
        kind: 'recheck-pending',
        applied,
        skipped,
        removed,
        reason: e.message,
        ...writeback,
      }
    }
    await discardPendingFile()
    // 重检失败：不换基线（旧下标对旧文件仍然有效，换了反而会错位），只按本次修复结果更新列表。
    // 代价是下一轮修复会退回最初上传的那份文件，所以提示用户重新上传这份修复稿。
    applyFixedIssues(issues, fixedIndexes)
    return {
      kind: 'recheck-failed',
      applied,
      skipped,
      removed,
      left: Math.max(0, prevTotal - removed),
      reason: e.message || '未知错误',
      ...writeback,
    }
  }
}

// 写回阶段被跳过的改动（段落含域代码改不动、多节文档不支持结构性原位写回等）附在提示末尾。
// 「已修复 N 项」说的是后端接受了这些修复项，不等于它们都落进了文档，不额外说一句用户会以为全改到了。
function describeWritebackSkips(outcome: FixOutcome) {
  if (outcome.kind === 'no-file') return ''
  const count = outcome.writebackSkipCount || 0
  if (count === 0) return ''
  const detail = outcome.writebackSkips?.length
    ? `：${outcome.writebackSkips.slice(0, 3).join('；')}${
        outcome.writebackSkips.length > 3 ? ' 等' : ''
      }`
    : ''
  return `；另有 ${count} 项在写回阶段被跳过${detail}`
}

// 把 runFixFlow 的结果翻成提示：{ type, text }，type 对应 ElMessage.success/info/warning。
// verb 为「修复」或「导出」，只影响文案措辞。
export function describeFixOutcome(outcome: FixOutcome, verb = '修复'): FixMessage {
  const described = describeFixOutcomeText(outcome, verb)
  return { ...described, text: described.text + describeWritebackSkips(outcome) }
}

function describeFixOutcomeText(outcome: FixOutcome, verb: string): FixMessage {
  switch (outcome.kind) {
    case 'no-file':
      return {
        type: 'warning',
        text: '原始论文文件已丢失（本机未留存或被浏览器清理），请重新上传论文',
      }
    case 'no-change':
      return {
        type: 'warning',
        text: outcome.left === 0
          ? `${verb}完成：未勾选任何修复项，文档原样输出`
          : `${verb}完成：本次勾选的 ${outcome.left} 项均不支持自动修复，文档未改动`,
      }
    case 'rechecked':
      if (outcome.remaining === 0) {
        return {
          type: 'success',
          text: `${verb}完成：已修复 ${outcome.removed} 项（${outcome.applied} 处），重检后问题已全部解决`,
        }
      }
      if (outcome.fresh > 0) {
        return {
          type: 'warning',
          text: `${verb}完成：已修复 ${outcome.removed} 项（${outcome.applied} 处）；重检后还剩 ${outcome.remaining} 项，其中 ${outcome.fresh} 项是修复后新出现的，请检查后处理`,
        }
      }
      return {
        type: 'info',
        text: `${verb}完成：已修复 ${outcome.removed} 项（${outcome.applied} 处）；重检后还剩 ${outcome.remaining} 项，列表已按重检结果刷新`,
      }
    case 'recheck-failed':
      if (outcome.removed === 0) {
        return {
          type: 'warning',
          text: `${verb}完成：文档已下载，但自动重检失败（${outcome.reason}），列表未更新；请重新上传这份新文档核对`,
        }
      }
      return {
        type: 'warning',
        text: `${verb}完成：已修复 ${outcome.removed} 项，但自动重检失败（${outcome.reason}）；列表已按本次修复结果更新，再次修复前请重新上传这份新文档，否则可能定位错位`,
      }
    case 'recheck-pending':
      return {
        type: 'warning',
        text: `${verb}完成并已下载，但自动重检仍在后台执行；请返回上传页继续查询检测结果`,
      }
    default:
      return { type: 'warning', text: '修复流程出现未知状态，请重新上传论文' }
  }
}
