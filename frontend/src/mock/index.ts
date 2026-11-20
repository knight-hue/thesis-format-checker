import type { AxiosAdapter, InternalAxiosRequestConfig } from 'axios'
import type { Issue } from '../types/issue'
import type { CheckResponse, CollegeInfo, IssueCategorySummary } from '../types/store'
import type { RawChatResponse } from '../types/chat'

type MockConfig = InternalAxiosRequestConfig<unknown>
type MockIssue = Omit<Issue, 'occurrence_count' | 'location_samples' | 'variants'
  | 'decision' | 'confidence' | 'evidence'>
  & Partial<Pick<Issue, 'occurrence_count' | 'location_samples' | 'variants'
  | 'decision' | 'confidence' | 'evidence'>>
type MockEnvelope = { code: number; msg: string; data: unknown }
type RawResponse = { __raw: true; data: Blob; headers: Record<string, string> }

function formValue(config: MockConfig, key: string): FormDataEntryValue | null {
  return config.data instanceof FormData ? config.data.get(key) : null
}

// 本地 Mock：VITE_USE_MOCK=true 时启用。
// 用 axios 自定义 adapter 拦截真实 API 路径，便于前端脱离后端开发。
// issue 结构与真实后端一致（detection/rule_engine.py / detection/semantic_checker.py）。

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

// 统一响应包裹 { code, msg, data }
function ok<T>(data: T, msg = ''): MockEnvelope {
  return { code: 0, msg, data }
}

// 响应头只能是 ASCII：真实后端用 ensure_ascii=True 把中文转成 \uXXXX，mock 要对齐，
// 否则浏览器会把非 ASCII 头值按 ISO-8859-1 编码搞乱。
function asciiJson(value: unknown) {
  return JSON.stringify(value).replace(
    /[\u0080-\uFFFF]/g,
    (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`,
  )
}

// —— 示例错误列表（真实后端 issue 结构）——
// 每个 issue 带稳定 _id：前端 attachIds 认这个字段，mock 下修复/重检会过滤列表，
// 若靠下标兜底 id 会随列表变化而变，重检就对不上了。
const errorList: MockIssue[] = [
  {
    _id: 'mock-1',
    rule_id: 'body.font_size',
    category: '正文格式',
    severity: 'error',
    location: { type: 'paragraph', index: 3, text_preview: '基于深度学习的图像分类方法研究' },
    actual: '14 pt',
    expected: '12 pt',
    message: '正文字号不符合要求',
    suggestion: '请将字号修改为 12 磅',
    source: 'rule_engine',
    fixable: true,
  },
  {
    _id: 'mock-2',
    rule_id: 'body.line_spacing',
    category: '正文格式',
    severity: 'error',
    location: { type: 'paragraph', index: 10, text_preview: '随着深度学习的快速发展…' },
    actual: '1 倍',
    expected: '1.5 倍',
    message: '正文行距不符合要求',
    suggestion: '请将行距修改为 1.5 倍',
    source: 'rule_engine',
    fixable: true,
  },
  {
    _id: 'mock-3',
    rule_id: 'body.east_asia_font',
    category: '正文格式',
    severity: 'error',
    location: { type: 'paragraph', index: 15, text_preview: '卷积神经网络…', run: 0 },
    actual: '宋体',
    expected: '仿宋',
    message: '正文中文字体不符合要求',
    suggestion: '请将中文字体修改为 仿宋',
    source: 'rule_engine',
    fixable: true,
  },
  {
    _id: 'mock-4',
    rule_id: 'body.fullwidth_punct',
    category: '正文格式',
    severity: 'warning',
    location: { type: 'paragraph', index: 20, text_preview: '首先,我们要…' },
    actual: ',',
    expected: '全角标点',
    message: '中文语境下应使用全角标点',
    suggestion: '将半角标点改为全角标点',
    source: 'rule_engine',
    fixable: false,
    fix_reason: '暂无自动修复映射',
  },
  {
    _id: 'mock-5',
    rule_id: 'figure.caption_position',
    category: '图',
    severity: 'error',
    location: { type: 'paragraph', index: 30, text_preview: '图 3-1 系统架构图' },
    actual: '图题位于图片上方',
    expected: '图题位于图片下方',
    message: '图题应位于图片下方',
    suggestion: '将图题移动到图片下方',
    source: 'rule_engine',
    fixable: true,
  },
  {
    _id: 'mock-6',
    rule_id: 'terminology',
    category: '名词术语',
    severity: 'warning',
    location: { type: 'document', kinds: ['body', 'abstract_body'], text_preview: 'CNN 是一种…' },
    blocks: [{ index: 8, text: 'CNN 是一种常用的网络结构。' }],
    actual: 'CNN 首次出现未注明含义',
    expected: '外文缩写首次出现时应在括号内注明其含义',
    message: '名词术语或外文缩写使用不符合要求',
    suggestion: '首次出现时改为「卷积神经网络（CNN）」',
    basis: '规范要求外文缩写首次出现时注明中文含义',
    conclusion: '外文缩写 CNN 首次出现未注明含义',
    source: 'rag',
    fixable: true,
  },
  {
    _id: 'mock-7',
    rule_id: 'reference.format',
    category: '参考文献',
    severity: 'warning',
    location: { type: 'document', kinds: ['reference_entry'], text_preview: '［3］张三…' },
    blocks: [{ index: 31, text: '［3］张三. 某研究[J]. 某学报, 2020.' }],
    actual: '［3］张三. 某研究[J]. 某学报, 2020.',
    expected: '连续出版物著录格式：……卷号(期号)：起止页码．',
    message: '参考文献著录格式不符合要求',
    suggestion: '按模板该类条目的样例著录',
    basis: '文献类型标识：专著[M]、论文集[C]、单篇论文[A]、报纸文章[N]、期刊文章[J]…',
    conclusion: '参考文献条目的著录项与模板样例不符：缺卷(期)号后的起止页码',
    source: 'rag',
    fixable: false,
    fix_reason: '参考文献的著录项要按原文补全，自动改写会编造出处，请人工补',
  },
]

const answer =
  '【结论】一级标题应使用黑体 16 号、加粗、居中对齐。当前检测到一级标题字号为 14 号，不符合要求。\n\n' +
  '【规范依据】《吉林大学本科毕业论文（设计）格式规范》：一级标题采用黑体三号（16pt）、加粗、居中排。\n\n' +
  '【修改建议】在 Word 中选中该标题，将字体改为黑体、加粗、字号改为三号（16pt），并设置居中对齐。'

const colleges: CollegeInfo[] = [
  {
    college_id: 'jlu_cs',
    college_name: '计算机科学与技术学院',
    display_name: '吉林大学·计算机科学与技术学院（Z-3）',
    school: 'jlu',
    school_name: '吉林大学',
    college: 'cs',
    major: null,
    major_name: null,
    template_version: 'Z-3',
    effective_year: null,
  },
  {
    college_id: 'jlu_se',
    college_name: '软件学院',
    display_name: '吉林大学·软件学院（Z-3）',
    school: 'jlu',
    school_name: '吉林大学',
    college: 'se',
    major: null,
    major_name: null,
    template_version: 'Z-3',
    effective_year: null,
  },
]

// 本次会话里已被 /fix 修掉的 issue._id。真实后端会重建 docx 并重新检测，
// mock 没法真的改文档，就用这份集合模拟「修复后再检测」的结果。
let fixedIssueIds = new Set<string>()
const checkJobs = new Map<string, { jobId: string; createdAt: number; result: CheckResponse }>()

function requestJson(config: MockConfig): Record<string, unknown> {
  try {
    const data: unknown = typeof config.data === 'string' ? JSON.parse(config.data) : config.data
    return data && typeof data === 'object' && !Array.isArray(data) ? data as Record<string, unknown> : {}
  } catch { return {} }
}

// 按当前剩余条目重算汇总，保证 passed / total_issues / breakdown 与 issues 自洽
function checkPayload(issues: MockIssue[]): CheckResponse {
  const normalized = issues.map((issue) => ({
    ...issue,
    decision: issue.decision || 'confirmed',
    confidence: issue.confidence ?? null,
    evidence: issue.evidence ?? null,
    occurrence_count: issue.occurrence_count || 1,
    location_samples: issue.location_samples || (issue.location ? [issue.location] : []),
    variants: issue.variants || [
      { actual: issue.actual, expected: issue.expected, location: issue.location || {} },
    ],
  })) as CheckResponse['issues']
  const ruleEngine = normalized.filter((it) => it.source !== 'rag').length
  const semantic = normalized.length - ruleEngine
  const passed = normalized.length === 0
  const issueSummary = Object.values(normalized.reduce<Record<string, IssueCategorySummary>>((summary, issue) => {
    const category = issue.category || '其他'
    const row = summary[category] || { category, occurrence_count: 0, grouped_count: 0 }
    row.occurrence_count += issue.occurrence_count
    row.grouped_count += 1
    summary[category] = row
    return summary
  }, {}))
  const totalIssues = normalized.reduce((count, issue) => count + issue.occurrence_count, 0)
  return {
    schema_version: '1.0',
    college_id: 'jlu_cs',
    passed,
    issues: normalized,
    total_issues: totalIssues,
    grouped_issues: normalized.length,
    returned_issues: normalized.length,
    issues_truncated: false,
    issue_summary: issueSummary,
    advisory_issues: [{
      rule_id: 'mock.advisory', category: '名词术语', severity: 'info',
      location: { type: 'document' }, actual: '开发示例表述', expected: '请人工复核术语是否准确',
      message: '开发示例：术语表述需人工复核', suggestion: '', source: 'rag', fixable: false,
      occurrence_count: 1, location_samples: [], variants: [],
      decision: 'suggested', confidence: null,
      evidence: {
        scope: 'body', first_occurrence: true, earlier_occurrences: [],
        rule_hits: [], reason_code: 'model_guess_unlisted_term',
      },
    }],
    pending_review: [{
      rule_id: 'terminology', category: '名词术语', severity: 'warning',
      location: { type: 'document', index: 42, kinds: ['body'] },
      actual: 'PDF 来源，无逐段解析，无法验证首次出现',
      expected: '外文缩写首次出现时在括号内注明含义',
      message: '需要人工确认术语首次出现',
      suggestion: '请人工核对原文', source: 'rule_engine', fixable: false,
      occurrence_count: 1,
      location_samples: [{ type: 'document', index: 42, kinds: ['body'] }],
      variants: [], decision: 'pending_review', confidence: null,
      evidence: {
        scope: 'body', first_occurrence: null, earlier_occurrences: [],
        rule_hits: [], reason_code: 'context_unavailable',
      },
    }],
    // 与真实后端一致：模板里写着、但当前能力做不到的条款（能力缺口）。真实后端对
    // 位图插图的「图内字体字号」不做判定，只说明原因并请人工核对——这里照抄其文案，
    // 让结果页那条提示在 mock 下也能走通。
    unchecked: [
      {
        rule_id: 'figure.inner_font_size',
        // 必须与 detection/figure_image/service.py 的 _REASON 一字不差
        // （tests/test_figure_content.py 有条用例逐字比对，改一边就会红）。
        reason: '本文插图以位图（截图/照片/导出的图片）形式插入，图内文字在图片上只是像素：字体名无处可读，字号也无法从像素可靠反推，程序不做判定。模板要求「图内字体字号：宋体五号字」，请人工核对。',
      },
    ],
    breakdown: {
      rule_engine: { passed: ruleEngine === 0, total_issues: ruleEngine },
      semantic: {
        enabled: true,
        llm_enabled: true,
        reason: '',
        passed: semantic === 0,
        total_issues: semantic,
        llm_calls: 4,
        cache_hits: 0,
        prefilter_skips: 0,
        confirmed_count: semantic,
        pending_review_count: 1,
      },
    },
  }
}

// 开发场景由 sessionStorage 显式选择；关键词分支仅在 Mock 内模拟后端。
// 生产聊天组件从不根据问题文本判断意图。
function mockChat(body: Record<string, unknown>): RawChatResponse {
  const question = String(body.question || '')
  let scenario = 'spec'
  try { scenario = sessionStorage.getItem('thesis_mock_chat_case') || 'spec' } catch { /* 隐私模式 */ }
  const remaining = errorList.filter((issue) => !fixedIssueIds.has(issue._id))
  const report = checkPayload(remaining)
  const detailIssue = remaining.find((issue) => issue.rule_id === 'figure.caption_position') || remaining[0]
  const common: RawChatResponse = {
    college: String(body.college_id || 'jlu_cs'), question, basis: '', suggestion: '', sources: [],
    found: true, confidence: 0.92, total_time: 0, from_cache: false,
    intent: 'spec_qa', actions: [], clarify: null, answer_source: 'spec_kb',
  }
  if (scenario === 'legacy') {
    // 现有后端尚无新字段，专门验证兼容默认值。
    const { intent, actions, clarify, answer_source, ...legacy } = common
    return { ...legacy, conclusion: answer }
  }
  if (scenario === 'clarify' && !/^第 [12] 条$/.test(question)) {
    return { ...common, conclusion: '', intent: 'clarify', answer_source: 'clarify',
      clarify: { kind: 'paper_index', question: '你指的是哪一条问题？', options: ['第 1 条', '第 2 条'] } }
  }
  if (scenario === 'detail' || scenario === 'clarify') {
    return { ...common, intent: 'paper_detail', answer_source: 'paper_facts',
      conclusion: detailIssue ? `示例问题：${detailIssue.message}。` : '当前示例论文未检测到问题。',
      suggestion: detailIssue?.suggestion || '',
      actions: detailIssue ? [{ type: 'highlight-issues', rule_ids: [detailIssue.rule_id] }] : [{ type: 'open-result' }] }
  }
  if (scenario === 'overview' || scenario === 'fix') {
    return { ...common, intent: scenario === 'fix' ? 'paper_fix' : 'paper_overview', answer_source: 'paper_facts',
      conclusion: `示例论文共检测出 ${report.total_issues} 次问题，合并为 ${report.grouped_issues} 类。`,
      suggestion: '请到检测结果页逐项核对，勾选需要修复的问题后再修复下载。',
      actions: [{ type: 'open-result' }] }
  }
  if (scenario === 'out_of_scope') {
    return { ...common, intent: 'out_of_scope', answer_source: 'out_of_scope',
      conclusion: '我可以帮助解答论文格式规范问题，请换一个相关问题。' }
  }
  return { ...common, conclusion: '示例：一级标题采用三号黑体。', basis: '开发示例规范 §4.1 标题（仅用于联调）。',
    suggestion: '在 Word 中选中标题，按对应学院模板设置字体和字号。',
    sources: [{ chapter: '开发示例', section: '标题', full_path: '开发示例 / 标题', content: '一级标题采用三号黑体。', preview: '开发示例：一级标题采用三号黑体。' }] }
}

// 请求「method url」 -> 响应函数
const handlers: Record<string, (config: MockConfig) => MockEnvelope | RawResponse> = {
  'POST /check-jobs/create': (config) => {
    const jobId = String(formValue(config, 'job_id') || crypto.randomUUID())
    const recheck = String(formValue(config, 'recheck') || '') === 'true'
    if (!recheck) fixedIssueIds = new Set()
    const now = Date.now()
    checkJobs.set(jobId, {
      jobId,
      createdAt: now,
      result: checkPayload(errorList.filter((it) => !fixedIssueIds.has(it._id))),
    })
    return ok({
      job_id: jobId,
      status: 'queued',
      progress: 0,
      stage: 'queued',
      message: '任务已进入检测队列',
      created_at: new Date(now).toISOString(),
      expires_at: new Date(now + 86400000).toISOString(),
    })
  },
  'POST /check-jobs/status': (config) => {
    const job = checkJobs.get(String(requestJson(config).job_id))
    if (!job) throw new Error('Mock 检测任务不存在')
    const complete = Date.now() - job.createdAt > 800
    return ok({
      job_id: job.jobId,
      status: complete ? 'succeeded' : 'running',
      progress: complete ? 100 : 55,
      stage: complete ? 'completed' : 'checking',
      message: complete ? '检测完成' : '正在执行格式检测',
      result_available: complete,
      created_at: new Date(job.createdAt).toISOString(),
      updated_at: new Date().toISOString(),
      expires_at: new Date(job.createdAt + 86400000).toISOString(),
    })
  },
  'POST /check-jobs/result': (config) => {
    const job = checkJobs.get(String(requestJson(config).job_id))
    if (!job) throw new Error('Mock 检测任务不存在')
    return ok({ job_id: job.jobId, status: 'succeeded', result: job.result })
  },
  'POST /check-file': (config) => {
    // recheck=true：同一篇论文修复后的重检，保留已修集合；
    // 否则视为新上传，清空历史修复记录
    const recheck = String(formValue(config, 'recheck') || '') === 'true'
    if (!recheck) fixedIssueIds = new Set()
    const issues = errorList.filter((it) => !fixedIssueIds.has(it._id))
    return ok(checkPayload(issues), '检测完成')
  },
  'POST /rag/answer': (config) => ok(mockChat(requestJson(config))),
  'GET /health': () => ok({
    status: 'healthy', llm_initialized: true, llm_status: 'ready', llm_error: null,
    rag_initialized: true, rag_status: 'ready', rag_error: null,
    features: { hybrid_search: true, rerank: true, query_rewrite: true, self_check: true, cache: true },
  }),
  'GET /colleges': () => ok(colleges, ''),
  'POST /session/reset': () =>
    ok({ status: 'success', message: '对话历史已清空', conversation_id: '' }, ''),
  'POST /fix': (config) => {
    let issues: Issue[] = []
    try {
      const parsed = JSON.parse(String(formValue(config, 'issues_json') || '[]'))
      issues = Array.isArray(parsed) ? parsed : []
    } catch {
      /* ignore */
    }
    // 与真实写回模块一致：只处理 fixable 的条目，其余计入 skipped 并原样留在列表里
    const todoIndexes: number[] = []
    issues.forEach((it, i) => {
      if (it && it.fixable !== false && it._id !== 'mock-6') todoIndexes.push(i)
    })
    // 记下这次修掉的条目，供后续 recheck 过滤（模拟「修复稿重新检测后这些问题不再出现」）
    todoIndexes.forEach((i) => {
      if (issues[i]._id) fixedIssueIds.add(issues[i]._id)
    })
    const blob = new Blob(['mock-fixed-docx'], {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    })
    // 模拟写回阶段的跳过：RAG 文本改写落到含域代码/图形的段落时，后端只登记跳过、不改文字
    // （PatchReport.skip）。mock 识别不了域代码，就用 mock-6 的块代表这种情况，
    // 文案格式与后端一致（`第N块：原因`），让前端提示逻辑在 mock 下也能走通。
    const writebackSkips = issues.some((issue) => issue._id === 'mock-6' && issue.fixable)
      ? ['第9块：改写区间含字段/图形等不可安全改写的 run，已跳过']
      : []
    return {
      __raw: true,
      data: blob,
      headers: {
        'x-applied-count': String(todoIndexes.length),
        'x-skipped-count': String(issues.length - todoIndexes.length),
        'x-llm-calls': '0',
        // 提交数组里的下标，前端据此把真正修好的条目从列表里摘掉
        'x-fixed-indexes': todoIndexes.join(','),
        // 写回跳过条数与明细：明细超过后端 2048 字符上限时只会回条数，前端需能容忍
        'x-writeback-skip-count': String(writebackSkips.length),
        ...(writebackSkips.length
          ? { 'x-writeback-warnings': asciiJson(writebackSkips) }
          : {}),
      },
    }
  },
}

export function isMockEnabled() {
  return import.meta.env.VITE_USE_MOCK === 'true'
}

export const mockAdapter: AxiosAdapter = async (config: MockConfig) => {
  await delay(400)
  const method = (config.method || 'get').toUpperCase()
  const url = (config.url || '').replace(/\/+$/, '')
  const key = `${method} ${url}`

  const handler = handlers[key]
  if (!handler) {
    return Promise.reject(new Error(`Mock 未定义该接口：${method} ${url}`))
  }

  const result = handler(config)
  if ('__raw' in result) {
    // 文件流类响应：透传 data + headers（如 /api/fix）
    return {
      data: result.data,
      status: 200,
      statusText: 'OK',
      headers: result.headers || {},
      config,
    }
  }
  return { data: result, status: 200, statusText: 'OK', headers: {}, config }
}
