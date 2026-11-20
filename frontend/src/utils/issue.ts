import type { Issue, IssueInput, IssuePreview, IssueVariant } from '../types/issue'

// 检测结果 issue 的展示辅助函数。
// 契约来源（只读，不改后端）：
//   - detection/rule_engine.py        规则引擎 issue：{rule_id, category, severity, location, actual, expected, message, suggestion, source}
//   - detection/semantic_checker.py   语义 issue：多 basis/conclusion/blocks，source="rag"（实现在 detection/semantic/）
//   - fix/fixability.py               后端已为每条 issue 标注 fixable / fix_reason（可修复性口径的唯一真源）

// 给 issue 列表注入稳定 _id，用于勾选 / 渲染 key。
export function attachIds(issues?: IssueInput[] | null): Issue[] {
  return (issues || []).map((it, i) => ({ ...it, _id: it?._id ?? `issue-${i}` }))
}

// 人类可读标题：优先 message（如“正文字号不符合要求”），退化为 rule_id。
export function issueTitle(issue?: IssuePreview | null) {
  return issue?.message || issue?.rule_id || '未知问题'
}

// 由 location 生成可读定位。
export function issuePosition(issue?: IssuePreview | null) {
  const loc: Partial<NonNullable<IssuePreview['location']>> = issue?.location || {}
  const preview = loc.text_preview ? `：${loc.text_preview}` : ''
  const index = loc.index ?? ''
  switch (loc.type) {
    case 'section':
      return index !== '' ? `第 ${index} 节${preview}` : '页面设置'
    case 'table_cell':
      return `表格第 ${index} 块（${loc.row ?? '?'} 行, ${loc.col ?? '?'} 列）`
    case 'table':
      return `表格第 ${index} 块${preview}`
    case 'image':
      return `图片第 ${index} 块${preview}`
    case 'paragraph':
      return index !== '' ? `第 ${index} 段${preview}` : (preview || '—')
    case 'document':
      return '全文'
    default:
      return preview || (index !== '' ? `第 ${index} 块` : '—')
  }
}

export function issueLevel(issue?: IssuePreview | null) {
  return issue?.severity === 'error' ? '错误' : '提示'
}

export type IssueDecision = NonNullable<IssuePreview['decision']>

const DECISION_LABELS: Record<IssueDecision, string> = {
  confirmed: '确定',
  suggested: '建议',
  pending_review: '待人工',
}

const DECISION_TAG_TYPES: Record<IssueDecision, 'success' | 'warning' | 'info'> = {
  confirmed: 'success',
  suggested: 'warning',
  pending_review: 'info',
}

const SCOPE_LABELS: Record<string, string> = {
  body: '正文',
  zh_abstract: '中文摘要',
  en_abstract: '英文摘要',
  acknowledgement: '致谢',
}

const REASON_LABELS: Record<string, string> = {
  exempt_by_level: '词表/策略豁免',
  not_first_in_scope: '本范围更早位置已出现',
  first_occurrence_no_gloss: '本范围首次出现且无释义',
  context_unavailable: '无逐段解析，无法验证首次出现',
  model_guess_unlisted_term: '词表未收录，模型建议',
}

/** 三态：旧结果没有 decision 时按 confirmed（与后端默认值一致）。 */
export function issueDecision(issue?: IssuePreview | null): IssueDecision {
  return issue?.decision || 'confirmed'
}

export function issueDecisionLabel(issue?: IssuePreview | null) {
  return DECISION_LABELS[issueDecision(issue)]
}

export function issueDecisionTagType(issue?: IssuePreview | null) {
  return DECISION_TAG_TYPES[issueDecision(issue)]
}

/** 判定依据：范围、首次出现、原因码、命中的子规则。 */
export function issueEvidenceText(issue?: IssuePreview | null) {
  const evidence = issue?.evidence
  if (!evidence) return ''
  const parts: string[] = []
  if (evidence.scope) {
    parts.push(`范围：${SCOPE_LABELS[evidence.scope] || evidence.scope}`)
  }
  if (evidence.first_occurrence !== undefined && evidence.first_occurrence !== null) {
    parts.push(`首次出现：${evidence.first_occurrence ? '是' : '否'}`)
  }
  if (evidence.reason_code) {
    parts.push(`原因：${REASON_LABELS[evidence.reason_code] || evidence.reason_code}`)
  }
  if (evidence.rule_hits?.length) {
    parts.push(`子规则：${evidence.rule_hits.join('、')}`)
  }
  if (evidence.earlier_occurrences?.length) {
    parts.push(`更早出现：${evidence.earlier_occurrences.length} 处`)
  }
  return parts.join('；')
}

// 只给“要发出去的文本”用的定位：**不含 text_preview**。
// 规则解释会把定位拼进问题发给生成模型，preview 是论文原文，
// 一旦带上就等于把论文内容发往外部服务；这里与后端 format_location 保持同一套模板。
export function issueLocationText(issue?: IssuePreview | null) {
  const loc: Partial<NonNullable<IssuePreview['location']>> = issue?.location || {}
  const index = loc.index ?? ''
  switch (loc.type) {
    case 'section':
      return index !== '' ? `第 ${index} 节` : ''
    case 'table_cell':
      return `表格第 ${index} 块（${loc.row ?? '?'} 行 ${loc.col ?? '?'} 列）`
    case 'table':
      return `表格第 ${index} 块`
    case 'image':
      return `图片第 ${index} 块`
    case 'paragraph':
      return index !== '' ? `第 ${index} 段` : ''
    case 'document':
      return '全文'
    default:
      return index !== '' ? `第 ${index} 块` : ''
  }
}

// 修改建议：优先 suggestion，语义 issue 兜底 conclusion。
export function issueSuggestion(issue?: IssuePreview | null) {
  return issue?.suggestion || issue?.conclusion || ''
}

// 后端 fixability 模块已为每条 issue 标注 fixable；前端不再自建白名单。
export function isFixable(issue?: IssuePreview | null) {
  return issue?.fixable === true
}

// 不可修复时后端给出的原因，用于前端展示。
export function fixReason(issue?: IssuePreview | null) {
  return issue?.fix_reason || ''
}

// 同一规则名下除代表值以外的其它实测值：后端聚合时把不同的「实测值/要求值/结论/
// 位置」放进 variants，代表值仍是 issue.actual / issue.expected，已经在“实际”一行
// 里显示过，不重复。
// 同一条规则下各处“缺什么”可能不同（如参考文献条目缺页码或缺出版地），所以把该处
// 的结论一并缀在实测值后面。
export function issueOtherVariantTexts(issue?: IssuePreview | null) {
  const representative = issue?.actual == null ? '' : String(issue.actual)
  const variants = Array.isArray(issue?.variants) ? issue.variants : []
  return variants
    .filter((variant) => (variant?.actual == null ? '' : String(variant.actual))
      !== representative)
    .map(variantText)
    .filter(Boolean)
}

function variantText(variant: IssueVariant) {
  const actual = variant?.actual == null ? '' : String(variant.actual)
  if (!actual) return ''
  const conclusion = variant?.conclusion == null ? '' : String(variant.conclusion)
  return conclusion ? `${actual}（${conclusion}）` : actual
}

// 模板依据：这条要求出自学院模板里的哪句话（后端 rules/<college>_basis.json）。
// 语义层还会给出自己的判定说明，后端放在 basis_detail 里，作为补充展示。
export function issueBasis(issue?: IssuePreview | null) {
  return issue?.basis || ''
}

export function issueBasisDetail(issue?: IssuePreview | null) {
  const detail = issue?.basis_detail || ''
  return detail && detail !== issueBasis(issue) ? detail : ''
}
