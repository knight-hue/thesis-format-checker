<script setup lang="ts">
import { asApiError } from '../api/request'
import type { IssuePreview } from '../types/issue'
import type { AnswerSection, ChatResult } from '../types/chat'
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { explainRule } from '../api/rag'
import { retryWhenRagReady } from '../utils/ragWarmup'
import {
  issueDecisionLabel,
  issueDecisionTagType,
  issueEvidenceText,
  issueLocationText,
} from '../utils/issue'
import { buildAnswerSections } from '../utils/ragAnswer'

const props = withDefaults(defineProps<{
  modelValue?: boolean
  collegeId?: string
  ruleId?: string
  ruleName?: string
  issue?: IssuePreview
}>(), { modelValue: false, collegeId: '', ruleId: '', ruleName: '', issue: () => ({}) })
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

const loading = ref(false)
const sections = ref<AnswerSection[]>([])
const errorMessage = ref('')
const sources = ref<ChatResult['sources']>([])

// actual / expected 可能是字符串、数组或对象，统一压成一行可读文本。
function readValue(value: unknown): string {
  if (value === undefined || value === null || value === '') return ''
  if (Array.isArray(value)) return value.map(readValue).filter(Boolean).join('、')
  if (typeof value === 'object') {
    return Object.entries(value)
      .filter(([, v]) => v !== undefined && v !== null && v !== '')
      .map(([k, v]) => `${k}=${readValue(v)}`)
      .join('，')
  }
  return String(value)
}

// 只把 rule_id 当问题发给模型时，模型会判定问题与论文格式无关而套用拒答模板，
// 因此把系统生成的描述/模板要求/定位拼进问题。
//
// 隐私边界（铁律）：这里拼出的文本会发给生成模型，**不能带论文内容**——
// 不带 location.text_preview（原文片段），也不带 actual（实测值里可能出现论文原文，
// 如 “Keywords:”）。与后端 rag/session_manager.build_paper_context 的口径一致。
function buildQuestion() {
  const issue = props.issue || {}
  const title = issue.message || props.ruleName || ''
  const parts = []
  if (title) parts.push(`问题：${title}`)
  const expected = readValue(issue.expected)
  if (expected) parts.push(`规范要求：${expected}`)
  const position = issueLocationText(issue)
  if (position && position !== '—') parts.push(`出现位置：${position}`)
  return `请解释这条论文格式规范，并给出在 Word 中的具体修改步骤。${parts.join('；')}`
}

watch(
  () => props.modelValue,
  async (open) => {
    if (!open) return
    sections.value = []
    errorMessage.value = ''
    sources.value = []
    if (!props.ruleId) {
      ElMessage.warning('缺少规则编号')
      return
    }
    loading.value = true
    try {
      const data = await retryWhenRagReady(
        () => explainRule(props.collegeId, props.ruleId, buildQuestion()),
        () => ElMessage.info('知识库正在初始化，稍等片刻…'),
      )
      // 正常 spec_only 不会返回 clarify；真返回了就把它的问题展示出来，
      // 不要落进“暂无解释结果”这种看起来像失败的空态。
      sections.value = buildAnswerSections(
        data.clarify?.question ? { conclusion: data.clarify.question } : data,
      )
      sources.value = data.sources || []
    } catch (e) {
      errorMessage.value = asApiError(e).message || '服务暂时不可用，请稍后重试'
      ElMessage.error('获取解释失败：' + errorMessage.value)
      sections.value = []
    } finally {
      loading.value = false
    }
  }
)
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="`规范解释${ruleName ? '：' + ruleName : ''}`"
    width="640px"
    top="8vh"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div v-loading="loading" class="dialog-body">
      <div v-if="issue && (issue.decision || issue.evidence)" class="state-line">
        <el-tag size="small" :type="issueDecisionTagType(issue)" effect="plain">
          {{ issueDecisionLabel(issue) }}
        </el-tag>
        <span v-if="issueEvidenceText(issue)" class="state-evidence">
          {{ issueEvidenceText(issue) }}
        </span>
      </div>
      <template v-if="sections.length">
        <section v-for="(section, si) in sections" :key="si" class="section">
          <div v-if="section.title" class="section-title">{{ section.title }}</div>
          <p
            v-for="(block, bi) in section.blocks"
            :key="bi"
            class="line"
            :class="{ indent: block.indent }"
            v-html="block.html"
          ></p>
        </section>
      </template>
      <div v-else-if="!loading && errorMessage" class="error">{{ errorMessage }}</div>
      <div v-else-if="!loading" class="empty">暂无解释结果。</div>

      <el-collapse v-if="!loading && sources.length" class="sources">
        <el-collapse-item :title="`规范来源（${sources.length}）`" name="sources">
          <div v-for="(s, i) in sources" :key="i" class="source-item">
            {{ s.preview || s.content || s.full_path }}
          </div>
        </el-collapse-item>
      </el-collapse>
    </div>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">关闭</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.dialog-body {
  min-height: 120px;
  max-height: 62vh;
  overflow-y: auto;
}
.state-line {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 12px;
  padding: 8px 10px;
  border-radius: 6px;
  background: #f5f7fa;
  font-size: 12px;
  color: #606266;
  line-height: 1.6;
}
.state-evidence {
  flex: 1;
  word-break: break-word;
}
.section + .section {
  margin-top: 18px;
}
.section-title {
  margin-bottom: 8px;
  padding-left: 8px;
  border-left: 3px solid var(--accent);
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}
.line {
  margin: 0 0 6px;
  line-height: 1.75;
  font-size: 14px;
  color: #303133;
  word-break: break-word;
}
.line.indent {
  padding-left: 1.4em;
  text-indent: -1.4em;
}
.line:last-child {
  margin-bottom: 0;
}
.empty {
  padding: 32px 0;
  text-align: center;
  color: #909399;
}
.error {
  padding: 24px 0;
  color: #f56c6c;
  line-height: 1.7;
}
.sources {
  margin-top: 18px;
  border-top: 1px solid #ebeef5;
  border-bottom: none;
}
.source-item {
  margin-bottom: 8px;
  padding: 8px 10px;
  border-radius: 4px;
  background: #f5f7fa;
  font-size: 13px;
  line-height: 1.6;
  color: #606266;
  word-break: break-word;
}
</style>
