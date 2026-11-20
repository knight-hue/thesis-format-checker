import assert from 'node:assert/strict'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

class MemoryStorage {
  values = new Map()
  getItem(key) { return this.values.get(key) ?? null }
  setItem(key, value) { this.values.set(key, String(value)) }
  removeItem(key) { this.values.delete(key) }
}

// 使用项目现有 Vite 转换 TS，无需另加测试框架或改动业务构建。
async function withModules(run, snapshot) {
  globalThis.sessionStorage = new MemoryStorage()
  globalThis.localStorage = new MemoryStorage()
  if (snapshot) sessionStorage.setItem('thesis_result_snapshot', JSON.stringify(snapshot))
  const server = await createServer({
    root: fileURLToPath(new URL('../', import.meta.url)),
    server: { middlewareMode: true, hmr: false, watch: null },
    logLevel: 'error',
  })
  try { await run((name) => server.ssrLoadModule(`/src/${name}.ts`)) }
  finally { await server.close() }
}

test('旧问答响应提供兼容默认值，新动作与澄清完整保留', async () => {
  await withModules(async (load) => {
    const { normalizeChatResult } = await load('api/rag')
    const legacy = normalizeChatResult({ conclusion: '旧回答' })
    assert.equal(legacy.intent, 'spec_qa')
    assert.deepEqual(legacy.actions, [])
    assert.equal(legacy.clarify, null)
    assert.equal(legacy.answerSource, 'spec_kb')
    assert.equal(legacy.fromCache, false)
    const clarify = { kind: 'paper_index', question: '哪条？', options: ['第 1 条'] }
    const actions = [{ type: 'highlight-issues', rule_ids: ['body.font_size'] }]
    const modern = normalizeChatResult({ intent: 'clarify', clarify, actions, answer_source: 'clarify', from_cache: true })
    assert.deepEqual(modern.clarify, clarify)
    assert.deepEqual(modern.actions, actions)
    assert.equal(modern.intent, 'clarify')
    assert.equal(modern.fromCache, true)
  })
})

test('回答 HTML 被转义，分段标记去重且安全加粗仍可用', async () => {
  await withModules(async (load) => {
    const { buildAnswerSections } = await load('utils/ragAnswer')
    const sections = buildAnswerSections({
      conclusion: '<img src=x onerror=alert(1)> **标题**。【规范依据】模板原句',
      basis: '模板原句', suggestion: '修改步骤',
    })
    const html = sections.flatMap((section) => section.blocks.map((block) => block.html)).join('\n')
    assert.ok(html.includes('&lt;img'))
    assert.ok(!html.includes('<img'))
    assert.ok(html.includes('<strong>标题</strong>'))
    assert.equal(html.match(/模板原句/g)?.length, 1)
  })
})

test('issue 稳定 ID 幂等，实际值与扩展字段不被转换', async () => {
  await withModules(async (load) => {
    const { attachIds } = await load('utils/issue')
    const raw = [{ _id: 'stable', actual: { size: 12 }, expected: ['宋体', '黑体'], extension: 'keep' }]
    const once = attachIds(raw)
    assert.deepEqual(attachIds(once), once)
    assert.deepEqual(once[0].actual, raw[0].actual)
    assert.deepEqual(once[0].expected, raw[0].expected)
    assert.equal(once[0].extension, 'keep')
  })
})

test('快照恢复保留勾选与 fixable，File 保持在 reactive 状态之外', async () => {
  const snapshot = {
    snapshotVersion: 1, fileName: 'synthetic.docx', collegeId: 'jlu_cs',
    errorList: [{ _id: 'a', fixable: true }, { _id: 'b', fixable: false }],
    selectedIds: ['a', 'b'], advisoryList: [{ rule_id: 'advisory' }],
    unchecked: [{ rule_id: 'unchecked', reason: '人工核对' }],
    totalIssues: 20, groupedIssues: 8, returnedIssues: 2, issuesTruncated: true,
  }
  await withModules(async (load) => {
    const { store, rawSession, fixedIssueIdSet } = await load('store')
    assert.equal(store.totalIssues, 20)
    assert.equal(store.groupedIssues, 8)
    assert.equal(store.returnedIssues, 2)
    assert.deepEqual([...store.selectedIds], ['a'])
    assert.equal(store.advisoryList.length, 1)
    assert.equal(store.unchecked.length, 1)
    assert.deepEqual([...store.highlightRuleIds], [])
    assert.ok(!('originalFile' in store))
    assert.equal(rawSession.originalFile, null)
    assert.deepEqual([...fixedIssueIdSet(store.errorList, [0, 0, 99])], ['a'])
  }, snapshot)
})

test('三态快照恢复 pending_review，并生成标签与判定依据', async () => {
  const snapshot = {
    snapshotVersion: 1, fileName: 'three-state.docx', collegeId: 'jlu_cs',
    errorList: [{
      _id: 'confirmed', fixable: true, decision: 'confirmed',
      message: '确定问题', occurrence_count: 1, location_samples: [],
    }],
    advisoryList: [{ _id: 'suggested', rule_id: 'terminology', decision: 'suggested' }],
    pendingReview: [{
      _id: 'pending', rule_id: 'terminology', decision: 'pending_review',
      fixable: false, occurrence_count: 1, location_samples: [],
      evidence: {
        scope: 'body', first_occurrence: null, earlier_occurrences: [],
        rule_hits: [], reason_code: 'context_unavailable',
      },
    }],
    unchecked: [], totalIssues: 1, groupedIssues: 1, returnedIssues: 1,
    issuesTruncated: false, selectedIds: [],
  }
  await withModules(async (load) => {
    const { store } = await load('store')
    const { issueDecisionLabel, issueDecisionTagType, issueEvidenceText } = await load('utils/issue')
    assert.equal(store.pendingReview.length, 1)
    assert.equal(issueDecisionLabel(store.pendingReview[0]), '待人工')
    assert.equal(issueDecisionTagType(store.pendingReview[0]), 'info')
    assert.ok(issueEvidenceText(store.pendingReview[0]).includes('无逐段解析'))
    assert.equal(issueDecisionLabel(store.errorList[0]), '确定')
    assert.ok('pendingReview' in store)
  }, snapshot)
})

test('没有 fixable 的旧快照必须失效，不能恢复出错误的勾选状态', async () => {
  await withModules(async (load) => {
    const { store } = await load('store')
    assert.equal(store.fileName, '')
    assert.equal(store.errorList.length, 0)
    assert.equal(sessionStorage.getItem('thesis_result_snapshot'), null)
  }, { snapshotVersion: 1, fileName: 'old.docx', errorList: [{ _id: 'old' }] })
})

test('预热候选按 HTTP 状态判断，不依赖中文报错', async () => {
  await withModules(async (load) => {
    const { isRagWarming } = await load('utils/ragWarmup')
    assert.equal(isRagWarming(Object.assign(new Error('service warming'), { status: 503 })), true)
    assert.equal(isRagWarming(Object.assign(new Error('初始化失败'), { status: 500 })), false)
    assert.equal(isRagWarming(new Error('初始化中')), false)
  })
})
