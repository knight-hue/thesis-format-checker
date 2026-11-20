<script setup lang="ts">
import { computed, ref, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { store, ensureConversationId } from '../store'
import { askRag, resetSession, submitFeedback } from '../api/rag'
import { asApiError } from '../api/request'
import { retryWhenRagReady } from '../utils/ragWarmup'
import { buildAnswerSections } from '../utils/ragAnswer'
import type { ChatAction, ChatFeedbackRating, ChatIssueCard, ChatMessage, ChatResult } from '../types/chat'

const router = useRouter()
const visible = ref(false)
const input = ref('')
const loading = ref(false)
const loadingText = ref('正在思考…')
const messages = ref<ChatMessage[]>([])
const listEl = ref<HTMLElement | null>(null)
const QUICK_PROMPTS = ['这篇论文哪里不对？', '第 1 条怎么改？', '一级标题应该用什么字号？']
const SPEC_PROMPTS = ['一级标题应该用什么字号？', '正文行距要求是多少？', '参考文献怎么排？']
const INTENT_LABELS: Record<string, string> = {
  paper_overview: '论文检测结果',
  paper_detail: '问题详情',
  paper_fix: '修改建议',
  paper_basis: '模板依据',
  paper_coverage: '检查范围',
  spec_qa: '规范问答',
  out_of_scope: '超出范围',
  clarify: '追问',
}
const SOURCE_LABELS: Record<ChatResult['answerSource'], string> = {
  paper_facts: '依据本次检测结果',
  spec_kb: '依据规范知识库',
  out_of_scope: '超出论文格式范围',
  clarify: '需要补充信息',
  agent: '由检测结果自动编排',
  none: '未找到答案',
}

// 有检测结果才给“这篇论文哪里不对”这类快捷问法，避免用户还没检测就问论文。
const quickPrompts = computed(() => (store.checkStatus === 'succeeded' ? QUICK_PROMPTS : SPEC_PROMPTS))
const clarifying = computed(() => {
  const last = messages.value[messages.value.length - 1]
  return last?.role === 'assistant' && Boolean(last.result?.clarify)
})

// 最新一次回答携带的检测结果指纹；更早的消息指纹不同，说明它们基于旧检测结果。
const latestPaperVersion = computed(() => {
  for (let i = messages.value.length - 1; i >= 0; i -= 1) {
    const version = messages.value[i].result?.paperVersion
    if (version) return version
  }
  return ''
})

function isStale(message: ChatMessage) {
  const version = message.result?.paperVersion
  return Boolean(version && latestPaperVersion.value && version !== latestPaperVersion.value)
}

function intentLabel(intent: string) {
  return INTENT_LABELS[intent] ?? intent
}

function sourceLabel(source: ChatResult['answerSource']) {
  return SOURCE_LABELS[source] ?? source
}

function push(role: ChatMessage['role'], text: string) {
  messages.value.push({ role, text })
}

function scrollToBottom() {
  nextTick(() => {
    if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
  })
}

async function newChat() {
  if (loading.value) return
  loading.value = true
  try {
    await resetSession(ensureConversationId())
    messages.value = []
    input.value = ''
    push('assistant', '已开启新对话。论文检测结果仍然保留，你可以继续针对它提问。')
  } catch (error) {
    ElMessage.error('开启新对话失败：' + asApiError(error).message)
  } finally {
    loading.value = false
    scrollToBottom()
  }
}

// 所有问题统一交给后端；前端仅负责展示答案与用户主动点击的动作。
async function send(raw?: string) {
  const question = (raw ?? input.value).trim()
  if (!question || loading.value) return
  input.value = ''
  push('user', question)
  loading.value = true
  loadingText.value = '正在思考…'
  scrollToBottom()
  try {
    const result = await retryWhenRagReady(
      () => askRag(store.collegeId, question, ensureConversationId()),
      () => { loadingText.value = '知识库正在初始化，稍等片刻…' },
    )
    messages.value.push({
      role: 'assistant',
      text: result.conclusion || '未检索到相关规范，请换个问法。',
      result,
      sections: buildAnswerSections(result),
    })
  } catch (error) {
    push('assistant', '回答失败：' + asApiError(error).message)
  } finally {
    loading.value = false
    scrollToBottom()
  }
}

async function runAction(action: ChatAction) {
  if (!store.fileName) {
    ElMessage.info('请先上传论文并完成检测，再查看结果。')
    return
  }
  if (action.type !== 'open-result' && action.type !== 'highlight-issues') return
  store.highlightRuleIds = action.type === 'highlight-issues' ? action.rule_ids ?? [] : []
  store.highlightTargets = action.type === 'highlight-issues' ? action.targets ?? [] : []
  await router.push('/result')
  visible.value = false
}

async function highlightIssue(issue: ChatIssueCard) {
  if (!store.fileName) {
    ElMessage.info('请先上传论文并完成检测，再查看结果。')
    return
  }
  // 规则号 + 定位一起带过去，同一条规则有多处时只亮当前这一条
  store.highlightRuleIds = issue.rule_id ? [issue.rule_id] : []
  store.highlightTargets = issue.rule_id && issue.location
    ? [{ rule_id: issue.rule_id, location: issue.location }]
    : []
  await router.push('/result')
  visible.value = false
}

function askIssue(issue: ChatIssueCard) {
  if (issue.rule_id) {
    send(`规则号 ${issue.rule_id} 怎么改？`)
    return
  }
  if (!issue.index) return
  send(`第 ${issue.index} 条怎么改？`)
}

function askBasis(issue: ChatIssueCard) {
  if (issue.index) {
    send(`第 ${issue.index} 条依据哪句模板？`)
    return
  }
  if (issue.rule_id) send(`规则号 ${issue.rule_id} 依据哪句模板？`)
}

function askRule(issue: ChatIssueCard) {
  if (!issue.rule_id || loading.value) return
  send(`规则号 ${issue.rule_id} 对应哪条规则？`)
}

function actualText(value: unknown): string {
  if (typeof value === 'string') return value
  if (typeof value === 'number') return String(value)
  return ''
}

function severityLabel(severity: string) {
  if (severity === 'error') return '错误'
  if (severity === 'warning') return '警告'
  if (severity === 'info') return '提示'
  return severity || '问题'
}

function severityType(severity: string) {
  if (severity === 'error') return 'danger'
  if (severity === 'warning') return 'warning'
  return 'info'
}

const FEEDBACK_LABELS: Record<ChatFeedbackRating, string> = {
  helpful: '有用',
  not_helpful: '没用',
  off_topic: '答非所问',
}

async function feedback(message: ChatMessage, rating: ChatFeedbackRating) {
  const result = message.result
  if (!result?.responseId || message.feedback === 'sending') return
  message.feedback = 'sending'
  try {
    await submitFeedback(
      result.responseId,
      rating,
      ensureConversationId(),
      result.intent,
      result.answerSource,
    )
    message.feedback = rating
  } catch (error) {
    message.feedback = 'failed'
    ElMessage.error('反馈提交失败：' + asApiError(error).message)
  }
}

function open() {
  visible.value = true
  ensureConversationId()
  if (!messages.value.length) {
    // 不写具体条数：换论文后这句会变成与当前文件不符的旧数字。
    push('assistant', store.checkStatus === 'succeeded'
      ? '你好，我是论文格式助手。已载入当前论文的检测结果，可以直接问它哪里不对，也可以问格式规范。'
      : '你好，我是论文格式助手。上传论文后可问我论文哪里不对；现在也可以直接问格式规范问题。')
  }
  scrollToBottom()
}
</script>

<template>
  <button v-if="!visible" class="fab" type="button" @click="open">
    <el-icon><chat-dot-round /></el-icon><span>AI 助手</span>
  </button>
  <el-drawer v-model="visible" title="论文格式 AI 助手" direction="rtl" size="min(420px, 100vw)" :with-header="true">
    <div class="chat">
      <div ref="listEl" class="msg-list" role="log" aria-live="polite" :aria-busy="loading">
        <div v-for="(message, i) in messages" :key="i" class="msg" :class="message.role">
          <div class="bubble" :class="{ 'bubble-wide': Boolean(message.result?.blocks?.length) }">
            <div v-if="message.result" class="answer-meta">
              <el-tag v-if="message.result.intent.startsWith('paper_')" size="small" type="success" effect="plain">{{ intentLabel(message.result.intent) }}</el-tag>
              <el-tag size="small" effect="plain">{{ sourceLabel(message.result.answerSource) }}</el-tag>
              <el-tag v-if="message.result.fromCache" size="small" type="info" effect="plain">缓存</el-tag>
              <el-tag v-if="isStale(message)" size="small" type="warning" effect="plain">基于旧检测结果</el-tag>
            </div>
            <template v-if="message.result?.clarify">
              <div class="clarify-label">这是追问，不是结论</div>
              <div>{{ message.result.clarify.question }}</div>
              <div v-if="message.result.clarify.options?.length" class="clarify-options">
                <el-button v-for="option in message.result.clarify.options" :key="option" size="small" :disabled="loading || i !== messages.length - 1" @click="send(option)">{{ option }}</el-button>
              </div>
            </template>
            <template v-else-if="message.result?.blocks?.length">
              <div class="block-list">
                <section v-for="(block, j) in message.result.blocks" :key="j" class="answer-block">
                  <div v-if="block.title" class="answer-title">{{ block.title }}</div>
                  <p v-if="block.type === 'summary' && block.text" class="summary-text">{{ block.text }}</p>
                  <div v-else-if="block.type === 'issues' && (block.issues?.length ?? 0) > 0" class="issue-cards">
                    <article v-for="issue in (block.issues ?? [])" :key="`${issue.rule_id}-${issue.index}`" class="issue-card">
                      <div class="issue-head">
                        <span v-if="issue.index" class="issue-index">#{{ issue.index }}</span>
                        <el-tag size="small" :type="severityType(issue.severity)" effect="plain">{{ severityLabel(issue.severity) }}</el-tag>
                        <el-tag v-if="issue.category" size="small" effect="plain">{{ issue.category }}</el-tag>
                        <span v-if="issue.rule_id" class="issue-rule">{{ issue.rule_id }}</span>
                      </div>
                      <div v-if="issue.location" class="issue-loc">{{ issue.location }}</div>
                      <div v-if="issue.message" class="issue-message">{{ issue.message }}</div>
                      <div v-if="issue.expected" class="issue-line"><span>要求</span>{{ issue.expected }}</div>
                      <div v-if="actualText(issue.actual)" class="issue-line"><span>实测</span>{{ actualText(issue.actual) }}</div>
                      <div v-if="issue.suggestion" class="issue-line"><span>建议</span>{{ issue.suggestion }}</div>
                      <div v-if="issue.basis" class="issue-line"><span>依据</span>{{ issue.basis }}</div>
                      <div class="issue-actions">
                        <el-button size="small" :disabled="!store.fileName" @click="highlightIssue(issue)">高亮</el-button>
                        <el-button size="small" type="primary" plain :disabled="(!issue.index && !issue.rule_id) || loading" @click="askIssue(issue)">怎么改</el-button>
                        <el-button size="small" text :disabled="(!issue.index && !issue.rule_id) || loading" @click="askBasis(issue)">依据</el-button>
                        <el-button size="small" text :disabled="!issue.rule_id || loading" @click="askRule(issue)">规则号</el-button>
                      </div>
                    </article>
                  </div>
                  <p v-else-if="block.type === 'text' && block.text" class="summary-text">{{ block.text }}</p>
                </section>
              </div>
            </template>
            <template v-else-if="message.sections?.length">
              <section v-for="(section, j) in message.sections" :key="j" class="answer-section">
                <div v-if="section.title" class="answer-title">{{ section.title }}</div>
                <!-- 只渲染 buildAnswerSections 转义后的片段。 -->
                <div v-for="(block, k) in section.blocks" :key="k" :class="{ 'answer-indent': block.indent }" v-html="block.html" />
              </section>
            </template>
            <template v-else>{{ message.text }}</template>
          </div>
          <div v-if="message.result?.actions.length" class="actions">
            <template v-for="(action, j) in message.result.actions" :key="j">
              <el-button v-if="action.type === 'open-result' || action.type === 'highlight-issues'" size="small" :title="action.hint" @click="runAction(action)">
                {{ action.type === 'highlight-issues' ? '高亮对应问题' : '查看检测结果' }}
              </el-button>
            </template>
          </div>
          <div v-if="message.result?.suggestions.length" class="suggestions">
            <button v-for="suggestion in message.result.suggestions" :key="suggestion.query" type="button" class="suggestion-chip" :disabled="loading" @click="send(suggestion.query)">{{ suggestion.label }}</button>
          </div>
          <div v-if="message.result?.responseId" class="feedback-row">
            <span class="feedback-title">{{ message.feedback === 'failed' ? '提交失败，可重试' : message.feedback === 'sending' ? '提交中…' : message.feedback ? '感谢反馈' : '这条回答有用吗？' }}</span>
            <button v-for="(label, rating) in FEEDBACK_LABELS" :key="rating" type="button" class="feedback-btn" :class="{ active: message.feedback === rating }" :disabled="message.feedback === 'sending'" @click="feedback(message, rating)">{{ label }}</button>
          </div>
          <div v-if="message.result?.sources.length" class="sources">
            <el-collapse>
              <el-collapse-item title="参考规范片段">
                <div v-for="(source, j) in message.result.sources" :key="j" class="src-item">
                  <div class="src-path">{{ source.full_path }}</div>
                  <div class="src-preview">{{ source.preview }}</div>
                </div>
              </el-collapse-item>
            </el-collapse>
          </div>
        </div>
        <div v-if="loading" class="msg assistant"><div class="bubble typing">{{ loadingText }}</div></div>
      </div>
      <div class="chat-toolbar"><el-button size="small" text :disabled="loading" @click="newChat">新对话</el-button></div>
      <div v-if="!clarifying" class="quick">
        <el-button v-for="prompt in quickPrompts" :key="prompt" size="small" round :disabled="loading" @click="send(prompt)">{{ prompt }}</el-button>
      </div>
      <div class="input-row">
        <el-input v-model="input" :placeholder="clarifying ? '请补充信息，例如“第 2 条”' : '输入问题，回车发送'" :disabled="loading" @keyup.enter="send()" />
        <el-button type="primary" :disabled="loading || !input.trim()" @click="send()">发送</el-button>
      </div>
    </div>
  </el-drawer>
</template>

<style scoped>
.fab {
  position: fixed;
  right: 24px;
  bottom: 24px;
  z-index: 999;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 12px 16px;
  border: none;
  border-radius: 999px;
  cursor: pointer;
  color: #fff;
  background: linear-gradient(135deg, var(--accent-bright) 0%, var(--accent) 52%, var(--accent-2) 100%);
  box-shadow: 0 8px 24px color-mix(in srgb, var(--accent) 35%, transparent);
  font-size: 14px;
}
.fab:hover {
  background: linear-gradient(135deg, var(--accent-bright) 0%, var(--accent) 45%, var(--accent-2) 100%);
  filter: brightness(1.04);
}
.chat {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.msg-list {
  flex: 1;
  overflow-y: auto;
  padding: 4px 2px 12px;
}
/* 必须是纵向排列：横向排列时 .sources 会被挤成右侧一条窄缝，
   折叠头在几十像素宽里换行叠字，看起来就是文字重叠。 */
.msg {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  margin-bottom: 12px;
}
.msg.user {
  align-items: flex-end;
}
.bubble {
  max-width: 82%;
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}
.msg.user .bubble {
  background: linear-gradient(135deg, var(--accent), var(--accent-strong));
  color: var(--accent-contrast);
}
.msg.assistant .bubble {
  background: #f4f4f5;
  color: #303133;
}
.bubble.typing {
  color: #909399;
}
.sources {
  align-self: stretch;
  margin-top: 6px;
  max-width: 82%;
}
.bubble-wide {
  max-width: 100%;
  width: 100%;
}
.block-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.answer-block + .answer-block {
  margin-top: 2px;
}
.summary-text {
  margin: 0;
  white-space: pre-wrap;
}
.issue-cards {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.issue-card {
  padding: 10px;
  border: 1px solid #e4e7ed;
  border-radius: 10px;
  background: #fff;
}
.issue-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.issue-index {
  font-size: 12px;
  font-weight: 700;
  color: var(--accent);
}
.issue-rule {
  margin-left: auto;
  color: #a8abb2;
  font-size: 11px;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
}
.issue-loc {
  margin-top: 6px;
  color: #909399;
  font-size: 12px;
}
.issue-message {
  margin-top: 3px;
  font-size: 13px;
  color: #303133;
}
.issue-line {
  display: grid;
  grid-template-columns: 2.2em 1fr;
  gap: 6px;
  margin-top: 3px;
  color: #606266;
  font-size: 12px;
}
.issue-line span {
  color: #909399;
}
.issue-actions {
  display: flex;
  gap: 6px;
  margin-top: 8px;
}
.issue-actions :deep(.el-button) {
  margin-left: 0;
}
.suggestions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
  max-width: 100%;
}
.suggestion-chip,
.feedback-btn {
  padding: 4px 10px;
  border: 1px solid color-mix(in srgb, var(--accent) 26%, #dcdfe6);
  border-radius: 999px;
  background: #fff;
  color: #606266;
  font-size: 12px;
  line-height: 1.5;
  cursor: pointer;
}
.suggestion-chip:hover,
.feedback-btn:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}
.suggestion-chip:disabled,
.feedback-btn:disabled {
  opacity: 0.55;
  cursor: default;
}
.feedback-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
}
.feedback-title {
  color: #a8abb2;
  font-size: 11px;
}
.feedback-btn.active {
  border-color: var(--accent);
  background: color-mix(in srgb, var(--accent) 10%, #fff);
  color: var(--accent);
}
.src-item {
  padding: 6px 0;
  border-top: 1px dashed #e5e6eb;
}
.src-path {
  font-size: 12px;
  color: #909399;
}
.src-preview {
  font-size: 12px;
  color: #606266;
  margin-top: 2px;
}
.chat-toolbar {
  display: flex;
  justify-content: flex-end;
  padding: 2px 0;
}
.quick {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 0;
}
.input-row {
  display: flex;
  gap: 8px;
}
.answer-section + .answer-section { margin-top: 12px; }
.answer-meta { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
.answer-title { font-weight: 600; margin-bottom: 4px; }
.answer-indent { padding-left: 12px; }
.clarify-label { color: #a66b05; font-size: 12px; margin-bottom: 6px; }
.clarify-options, .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.clarify-options :deep(.el-button), .actions :deep(.el-button), .quick :deep(.el-button) { margin-left: 0; white-space: normal; height: auto; min-height: 24px; }
.msg-list { min-height: 0; }
@media (max-width: 480px) {
  .fab { right: 12px; bottom: 96px; }
  .bubble, .sources { max-width: 100%; }
}
</style>
