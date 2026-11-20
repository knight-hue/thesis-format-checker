<script setup lang="ts">
import type { Issue, IssuePreview } from '../types/issue'
import { ref, onMounted, computed, nextTick, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import ScoreCard from '../components/ScoreCard.vue'
import ProblemCategoryCard from '../components/ProblemCategoryCard.vue'
import ExportButton from '../components/ExportButton.vue'
import RagDialog from '../components/RagDialog.vue'
import FixBar from '../components/FixBar.vue'
import { store, toggleIssue, isSelected, missingResultMessage } from '../store'
import {
  issueTitle,
  issuePosition,
  issueLevel,
  isFixable,
  fixReason,
  issueDecisionLabel,
  issueDecisionTagType,
  issueEvidenceText,
} from '../utils/issue'

const router = useRouter()
const route = useRoute()

// 固定 6 大问题分类（展示桶）
const CATEGORIES = [
  { key: '结构完整性', name: '结构完整性', desc: '摘要、目录、参考文献等结构检查' },
  { key: '正文格式', name: '正文格式', desc: '正文标题、字体、段落格式检查' },
  { key: '图表规范', name: '图表规范', desc: '图片、表格、题注检查' },
  { key: '公式规范', name: '公式规范', desc: '公式编号、位置检查' },
  { key: '参考文献', name: '参考文献', desc: '参考文献格式检查' },
  { key: '页面设置', name: '页面设置', desc: '页边距、纸张、页眉页脚检查' },
]

// 后端 category -> 展示桶（与 detection/rule_engine.py、detection/semantic/rules.py 实际 category 对齐）
const CATEGORY_MAP: Record<string, string> = {
  正文格式: '正文格式', 列表格式: '正文格式', 名词术语: '正文格式',
  量和单位: '正文格式', 数字用法: '正文格式',
  标题: '正文格式', 中文题目: '正文格式', 英文题目: '正文格式', 标题编号: '正文格式',
  表: '图表规范', 图: '图表规范', 表题: '图表规范', 图题: '图表规范',
  图表位置: '图表规范', 图表格式: '图表规范',
  公式: '公式规范',
  参考文献: '参考文献',
  页面设置: '页面设置',
  目录: '结构完整性', 致谢: '结构完整性', 注释: '结构完整性',
  英文摘要: '结构完整性', 摘要: '结构完整性',
  摘要标题: '结构完整性', 摘要正文: '结构完整性',
  关键词标题: '结构完整性', 关键词内容: '结构完整性', 关键词: '结构完整性',
  Abstract标题: '结构完整性', 英文摘要正文: '结构完整性',
  Keywords标题: '结构完整性', 英文关键词内容: '结构完整性', 英文关键词: '结构完整性',
}

const activeModules = ref<string[]>([])
const dialogVisible = ref(false)
const currentError = ref<IssuePreview>({})

// 聚合问题（如“表内中文字体 9 处”）逐处位置弹窗。优先用后端给的完整 locations，
// 旧结果没有 locations 时退回 location_samples（最多 10 处）。
type LocationItem = NonNullable<Issue['locations']>[number]
const locationDialogVisible = ref(false)
const locationDialogIssue = ref<IssuePreview>({})
const locationItems = computed<LocationItem[]>(() => {
  const issue = locationDialogIssue.value
  if (issue?.locations?.length) return issue.locations
  if (issue?.location_samples?.length) return issue.location_samples
  return issue?.location ? [issue.location as LocationItem] : []
})

function openLocations(err: IssuePreview) {
  locationDialogIssue.value = err
  locationDialogVisible.value = true
}

function locationText(loc: LocationItem) {
  return issuePosition({ location: loc } as IssuePreview)
}

const errorList = computed(() => store.errorList)
const passed = computed(() => store.passed === true)
const elapsed = computed(() => store.elapsed ?? '—')

const volumeControlled = computed(
  () => store.issuesTruncated || store.totalIssues > store.groupedIssues
)
const volumeDescription = computed(() => {
  const base = `原始问题 ${store.totalIssues} 次，合并为 ${store.groupedIssues} 类`
  return store.issuesTruncated
    ? `${base}；当前展示前 ${store.returnedIssues} 类，完整分类统计仍已保留。`
    : `${base}，已全部展示。`
})

// AI 服务不可用时后端只跑了零 LLM 的确定性语义条款，需在结果页明示，
// 避免用户把"没报语义问题"误读成"语义检查通过"。
const semanticDegraded = computed(
  () => store.breakdown?.semantic?.enabled === true
    && store.breakdown?.semantic?.llm_enabled === false
)
// 降级原因由后端给出：privacy（端点不是本机，按隐私规则未发论文内容）/ unavailable /
// initializing / disabled。不能都写成“服务不可用，稍后重试”——隐私拦截重试也没用。
const semanticSkipReason = computed(() => store.breakdown?.semantic?.reason || '')
const semanticDegradedTitle = computed(() => {
  if (semanticSkipReason.value === 'privacy') {
    return '隐私保护：语义定位条款未发送到外部模型'
  }
  if (semanticSkipReason.value === 'disabled') return '语义检查已关闭'
  return 'AI 服务不可用，语义检查已降级'
})
const semanticDegradedDescription = computed(() => {
  const base = '已执行零 LLM 的确定性条款（目录、致谢、参考文献、列表序号、量和单位、数字用法）；'
    + '术语、坐标轴说明等定位类条款本次未检查。'
  if (semanticSkipReason.value === 'privacy') {
    return base + '当前生成模型端点不是本机，按隐私规则没有把论文内容发往外部服务；'
      + '改用本机模型端点后重新检测即可补齐。'
  }
  if (semanticSkipReason.value === 'disabled') {
    return '本次检测关闭了语义检查，只执行规则引擎；需要语义条款时请打开「RAG 语义检查」后重新检测。'
  }
  return base + '若后端刚启动，AI 服务可能仍在后台初始化，稍后重新检测即可补齐。'
})

// 本次没有执行的条款（后端 unchecked）：模板里写着、但当前能力做不到，例如位图插图的
// 「图内字体字号」判不了。只看问题列表会把"没查"误读成"通过"，所以必须列出来。
const uncheckedItems = computed(
  () => (Array.isArray(store.unchecked) ? store.unchecked : [])
)

// “AI 建议”的问题（后端 advisory_issues）：不计入问题总数，但必须能看见，
// 否则学生会把“AI 没判出问题”当成“模板合规”。
const advisoryItems = computed(
  () => (Array.isArray(store.advisoryList) ? store.advisoryList : [])
)

// 规则与模型都无法判定的条目（后端 pending_review）：进人工队列，不计入结论。
const pendingItems = computed(
  () => (Array.isArray(store.pendingReview) ? store.pendingReview : [])
)

const stateCounts = computed(() => ({
  confirmed: errorList.value.length,
  suggested: advisoryItems.value.length,
  pending: pendingItems.value.length,
  unchecked: uncheckedItems.value.length,
}))

const hasThreeState = computed(() => (
  stateCounts.value.suggested > 0 || stateCounts.value.pending > 0
))

// 规则所属分类：优先 category 字段映射，缺失时按 rule_id 关键词兜底
function categoryOf(err: IssuePreview) {
  if (err.category && CATEGORY_MAP[err.category]) return CATEGORY_MAP[err.category]
  const n = err.rule_id || err.message || ''
  if (/摘要|目录|结构|封面|声明|致谢|注释|关键词|abstract|en_/.test(n)) return '结构完整性'
  if (/图|表|题注|caption/.test(n)) return '图表规范'
  if (/公式|formula/.test(n)) return '公式规范'
  if (/参考|reference/.test(n)) return '参考文献'
  if (/页|边距|纸张|页眉|页脚|page/.test(n)) return '页面设置'
  return '正文格式'
}

function categoryCount(key: string) {
  if (store.issueSummary.length) {
    return store.issueSummary.reduce((count, item) => {
      const mapped = CATEGORY_MAP[item.category] || categoryOf({ category: item.category })
      return count + (mapped === key ? (item.grouped_count || 0) : 0)
    }, 0)
  }
  return 0
}

function errorsOf(key: string) {
  return errorList.value.filter((e) => categoryOf(e) === key)
}

const highlightedIssues = computed(() => {
  const targets = store.highlightTargets ?? []
  if (targets.length) {
    const keys = new Set(targets.map((item) => `${item.rule_id}|${item.location}`))
    return errorList.value.filter((issue) => keys.has(`${issue.rule_id}|${locationKey(issue.location)}`))
  }
  const rules = new Set(store.highlightRuleIds)
  return errorList.value.filter((issue) => rules.has(issue.rule_id))
})

// 与后端 session_manager.format_location 保持同一模板（不含 text_preview），
// 这样对话给的“规则号 + 定位”才能精确对上表格里的那一行。
function locationKey(location?: IssuePreview['location']) {
  const loc: Partial<NonNullable<IssuePreview['location']>> = location || {}
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
      return ''
  }
}

function highlightRow({ row }: { row: Issue }): string {
  const targets = store.highlightTargets ?? []
  if (targets.length) {
    const key = `${row.rule_id}|${locationKey(row.location)}`
    return targets.some((item) => `${item.rule_id}|${item.location}` === key) ? 'chat-highlight' : ''
  }
  return store.highlightRuleIds.includes(row.rule_id) ? 'chat-highlight' : ''
}

watch(highlightedIssues, async (issues) => {
  if (!issues.length) return
  const categories = issues.map(categoryOf)
  activeModules.value = [...new Set([...activeModules.value, ...categories])]
  await nextTick()
  document.querySelector('.chat-highlight')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}, { immediate: true })

onMounted(() => {
  // passed 才是“本次真的有检测结果”的判据：上传失败/中途失败的会话里 fileName 可能残留，
  // 只按 fileName 判断会渲染出“未通过 / 0 条”的假结果页。
  if (!store.fileName || store.passed === null) {
    ElMessage.warning(missingResultMessage())
    router.push('/')
    return
  }
  // 侧边栏「报告导出 / 修改模块」点击后滚动到对应区块
  const section = route.query.section
  if (typeof section === 'string') {
    nextTick(() => {
      document.getElementById(section)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }
})

function openExplain(err: IssuePreview) {
  currentError.value = err
  dialogVisible.value = true
}
</script>

<template>
  <div class="overview">
    <!-- 标题 -->
    <div class="page-head">
      <span class="doc-title">检测结果：{{ store.fileName || '未命名文档.docx' }}</span>
      <el-tag v-if="store.fixedRounds > 0" type="success" effect="plain" size="small" class="baseline-tag">
        已基于修复稿重检 {{ store.fixedRounds }} 轮
      </el-tag>
    </div>

    <!-- 无 AI 服务时的降级提示：确定性语义条款已检查，定位类条款未检查 -->
    <el-alert
      v-if="semanticDegraded"
      class="degraded-tip"
      type="info"
      show-icon
      :closable="false"
      :title="semanticDegradedTitle"
      :description="semanticDegradedDescription"
    />

    <!-- 三态总览：确定 / 建议 / 待人工 / 未检查 -->
    <el-alert
      v-if="hasThreeState"
      class="three-state-tip"
      type="info"
      show-icon
      :closable="false"
      :title="`三态结果：确定 ${stateCounts.confirmed} · AI 建议 ${stateCounts.suggested} · 待人工 ${stateCounts.pending} · 未检查 ${stateCounts.unchecked}`"
      description="AI 建议仅供参考；待人工与未检查不计入问题总数，结论只覆盖已自动检查的部分。"
    />

    <!-- 没有执行的条款（后端 unchecked）：模板里写着但程序做不到，不能静默 -->
    <el-alert
      v-if="uncheckedItems.length"
      class="unchecked-tip"
      type="warning"
      show-icon
      :closable="false"
      :title="`以下 ${uncheckedItems.length} 项条款本次未能自动检查，请人工核对`"
    >
      <div v-for="(item, i) in uncheckedItems" :key="i" class="unchecked-item">{{ item.reason }}</div>
    </el-alert>

    <!-- AI 建议：只作参考，不计入总数 -->
    <div v-if="advisoryItems.length" class="state-section">
      <h3 class="section-title">
        AI 建议
        <el-tag type="warning" effect="plain" size="small">{{ advisoryItems.length }}</el-tag>
        <span class="state-sub">仅供参考，不计入问题总数</span>
      </h3>
      <el-table :data="advisoryItems" size="small" border class="state-table">
        <el-table-column label="结论" width="80" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="issueDecisionTagType(row)" effect="plain">
              {{ issueDecisionLabel(row) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="问题" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">{{ issueTitle(row) }}</template>
        </el-table-column>
        <el-table-column label="位置" min-width="130" show-overflow-tooltip>
          <template #default="{ row }">{{ issuePosition(row) }}</template>
        </el-table-column>
        <el-table-column label="判定依据" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">{{ issueEvidenceText(row) || '—' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90" align="center">
          <template #default="{ row }">
            <el-button size="small" text type="primary" @click="openExplain(row)">解释</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 待人工：规则与模型都无法判定，单独成列 -->
    <div v-if="pendingItems.length" class="state-section">
      <h3 class="section-title">
        待人工确认
        <el-tag type="info" effect="plain" size="small">{{ pendingItems.length }}</el-tag>
        <span class="state-sub">不计入问题总数；请人工核对原文</span>
      </h3>
      <el-table :data="pendingItems" size="small" border class="state-table">
        <el-table-column label="结论" width="80" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="issueDecisionTagType(row)" effect="plain">
              {{ issueDecisionLabel(row) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="问题" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">{{ issueTitle(row) }}</template>
        </el-table-column>
        <el-table-column label="位置" min-width="130" show-overflow-tooltip>
          <template #default="{ row }">{{ issuePosition(row) }}</template>
        </el-table-column>
        <el-table-column label="原文" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ row.actual || '—' }}</template>
        </el-table-column>
        <el-table-column label="判定依据" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">{{ issueEvidenceText(row) || '—' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90" align="center">
          <template #default="{ row }">
            <el-button size="small" text type="primary" @click="openExplain(row)">解释</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-alert
      v-if="volumeControlled"
      class="volume-tip"
      :type="store.issuesTruncated ? 'warning' : 'info'"
      show-icon
      :closable="false"
      :title="store.issuesTruncated ? '检测结果已截断' : '重复问题已合并'"
      :description="volumeDescription"
    />

    <el-alert v-if="store.highlightRuleIds.length || store.highlightTargets.length" class="highlight-tip" type="info" :closable="false">
      <span v-if="highlightedIssues.length">正在高亮 {{ highlightedIssues.length }} 条（来自对话，限当前返回列表）</span>
      <span v-else>当前返回列表中没有对应问题；结果截断时，部分问题可能未返回。</span>
      <el-button text type="primary" @click="store.highlightRuleIds = []; store.highlightTargets = []">清除高亮</el-button>
    </el-alert>

    <!-- 核心统计 -->
    <div class="stats">
      <ScoreCard title="检测结论" :value="passed ? '通过' : '未通过'" :sub="`确定问题：${stateCounts.confirmed}`" :color="passed ? '#67c23a' : '#f56c6c'" />
      <ScoreCard title="确定问题" sub="计入总数" :value="stateCounts.confirmed" unit="项" :color="stateCounts.confirmed ? '#f56c6c' : '#67c23a'" />
      <ScoreCard title="AI 建议" sub="不计入总数" :value="stateCounts.suggested" unit="项" :color="stateCounts.suggested ? '#e6a23c' : '#67c23a'" />
      <ScoreCard title="待人工" sub="不计入总数" :value="stateCounts.pending" unit="项" :color="stateCounts.pending ? '#909399' : '#67c23a'" />
      <ScoreCard title="未检查" sub="能力缺口" :value="stateCounts.unchecked" unit="项" :color="stateCounts.unchecked ? '#909399' : '#67c23a'" />
      <ScoreCard title="检测耗时" :value="elapsed" unit="秒" />
    </div>

    <!-- 评分状态条 -->
    <div class="score-bar" :class="passed ? 'bar-pass' : 'bar-fail'">
      <div class="bar-left">
        <div class="bar-status">{{ passed ? '检测通过' : '检测未通过' }}</div>
        <div class="bar-desc">
          勾选下方「确定问题」中的可修复项；AI 建议 {{ stateCounts.suggested }} 条、待人工
          {{ stateCounts.pending }} 条另列，不计入结论。
        </div>
      </div>
      <div class="bar-score">{{ passed ? '通过' : '未通过' }}</div>
    </div>

    <!-- 问题分布 + 报告导出 -->
    <div class="dist-row">
      <div class="dist-left">
        <h3 class="section-title">问题分布</h3>
        <div class="cat-grid">
          <ProblemCategoryCard
            v-for="c in CATEGORIES"
            :key="c.key"
            :name="c.name"
            :count="categoryCount(c.key)"
            :desc="c.desc"
          />
        </div>
      </div>
      <div id="report-export" class="dist-right">
        <h3 class="section-title">报告导出</h3>
        <div class="export-card">
          <p class="export-desc">导出修改完成后的完整 Word 文档：应用下方勾选的修复项，由写回模块重建生成。</p>
          <ExportButton />
        </div>
      </div>
    </div>

    <!-- 模块明细（修改模块） -->
    <div id="modify">
    <h3 class="section-title">模块明细</h3>
    <el-collapse v-model="activeModules" class="modules">
      <el-collapse-item v-for="c in CATEGORIES" :key="c.key" :name="c.key">
        <template #title>
          <span class="mod-name">{{ c.name }}</span>
          <span class="mod-count" :class="{ zero: categoryCount(c.key) === 0 }">
            （{{ categoryCount(c.key) }}项）
          </span>
        </template>

        <div v-if="!errorsOf(c.key).length" class="mod-empty">该模块暂未发现问题。</div>
        <el-table
          v-else
          :data="errorsOf(c.key)"
          :row-class-name="highlightRow"
          row-key="_id"
          size="small"
          border
          class="mod-table"
          @row-click="openExplain"
        >
          <el-table-column label="状态" width="70" align="center">
            <template #default="{ row }">
              <el-tag
                size="small"
                :type="(row.severity || 'warning') === 'error' ? 'danger' : 'warning'"
                effect="plain"
              >{{ issueLevel(row) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="结论" width="76" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="issueDecisionTagType(row)" effect="plain">
                {{ issueDecisionLabel(row) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="问题" min-width="160" show-overflow-tooltip>
            <template #default="{ row }">
              {{ issueTitle(row) }}
              <el-tag v-if="row.occurrence_count > 1" size="small" type="info" effect="plain"
                      class="location-tag" @click.stop="openLocations(row)"
                      :title="`查看这 ${row.occurrence_count} 处的具体位置`">
                {{ row.occurrence_count }} 处
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="位置" min-width="160" show-overflow-tooltip>
            <template #default="{ row }">{{ issuePosition(row) }}</template>
          </el-table-column>
          <el-table-column label="实际" min-width="100" show-overflow-tooltip>
            <template #default="{ row }">{{ row.actual ?? '—' }}</template>
          </el-table-column>
          <el-table-column label="要求" min-width="100" show-overflow-tooltip>
            <template #default="{ row }">{{ row.expected ?? '—' }}</template>
          </el-table-column>
          <el-table-column label="操作" width="180" align="center">
            <template #default="{ row }">
              <el-button size="small" type="warning" plain round @click.stop="openExplain(row)">
                提示
              </el-button>
              <el-checkbox
                :model-value="isSelected(row._id)"
                :disabled="!isFixable(row)"
                :title="isFixable(row) ? '' : (fixReason(row) || '暂不支持自动修复')"
                @click.stop
                @change="toggleIssue(row._id)"
              >确认修改</el-checkbox>
            </template>
          </el-table-column>
        </el-table>
      </el-collapse-item>
    </el-collapse>
    </div>

    <div class="detail-link">
      <el-button type="primary" plain @click="router.push('/detail')">查看错误定位明细</el-button>
    </div>

    <!-- RAG 解释弹窗 -->
    <RagDialog
      v-model="dialogVisible"
      :college-id="store.collegeId"
      :rule-id="currentError.rule_id || ''"
      :rule-name="issueTitle(currentError)"
      :issue="currentError"
    />

    <!-- 聚合问题逐处位置 -->
    <el-dialog
      v-model="locationDialogVisible"
      :title="`${issueTitle(locationDialogIssue)} · 共 ${locationItems.length} 处`"
      width="620px"
    >
      <el-table :data="locationItems" max-height="420" size="small">
        <el-table-column type="index" label="#" width="52" />
        <el-table-column label="位置">
          <template #default="{ row }">{{ locationText(row) }}</template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="locationDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 修复悬浮条 -->
    <FixBar />
  </div>
</template>

<style scoped>
.overview {
  padding: 24px 0 96px;
}
.page-head {
  margin-bottom: 16px;
  display: flex;
  align-items: center;
  gap: 12px;
}
.degraded-tip,
.volume-tip,
.unchecked-tip {
  margin-bottom: 16px;
}
.unchecked-item {
  line-height: 1.6;
}
.unchecked-item + .unchecked-item {
  margin-top: 6px;
}
.doc-title {
  font-size: 20px;
  font-weight: 700;
  color: #1f2d3d;
}
.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.three-state-tip {
  margin-bottom: 16px;
}
.state-section {
  margin-bottom: 20px;
}
.state-sub {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 400;
  color: #909399;
}
.state-table {
  margin-top: 4px;
}
.score-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  /* 不写死高度：30px 的结论文字在 768px 断点边界（媒体查询是 max-width: 767.98px）
     会撑到 76.8px，固定 60px 会让「未通过」画到横幅外面、压住下一节。 */
  height: auto;
  min-height: 60px;
  padding: 10px 24px;
  margin-bottom: 24px;
  border-radius: 12px;
  color: #fff;
}
.bar-pass {
  background: linear-gradient(90deg, #67c23a 0%, #95d475 100%);
}
.bar-fail {
  background: linear-gradient(90deg, #f56c6c 0%, #f89898 100%);
}
.bar-status {
  font-size: 16px;
  font-weight: 700;
}
.bar-desc {
  font-size: 13px;
  opacity: 0.9;
}
.bar-score {
  font-size: 30px;
  font-weight: 700;
  line-height: 1.2;
}
.section-title {
  margin: 0 0 12px;
  font-size: 16px;
  font-weight: 600;
  color: #1f2d3d;
}
.dist-row {
  display: grid;
  grid-template-columns: 3fr 2fr;
  gap: 24px;
  margin-bottom: 24px;
}
.cat-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}
.export-card {
  background: #fff;
  border: 1px solid #e5e6eb;
  border-radius: 12px;
  padding: 20px;
}
.export-desc {
  margin: 0 0 16px;
  font-size: 13px;
  color: #606266;
  line-height: 1.7;
}
.modules {
  border-radius: 10px;
  overflow: hidden;
}
.mod-name {
  font-weight: 600;
  color: #303133;
}
.mod-count {
  color: #e6a23c;
  font-weight: 600;
}
.mod-count.zero {
  color: #67c23a;
}
.mod-empty {
  padding: 20px 0;
  text-align: center;
  color: #909399;
  font-size: 13px;
}
.mod-table {
  cursor: pointer;
}
.location-tag {
  cursor: pointer;
}
.detail-link {
  margin-top: 20px;
  text-align: center;
}
#report-export,
#modify {
  scroll-margin-top: 16px;
}

@media (max-width: 767.98px) {
  .page-head { flex-wrap: wrap; }
  .doc-title { overflow-wrap: anywhere; font-size: 18px; }
  .score-bar { height: auto; min-height: 60px; padding: 12px; gap: 12px; }
  .bar-score { font-size: 22px; white-space: nowrap; }
  .stats {
    grid-template-columns: repeat(2, 1fr);
  }
  .dist-row {
    grid-template-columns: 1fr;
  }
  .cat-grid {
    grid-template-columns: 1fr;
  }
}
.highlight-tip { margin-bottom: 16px; }
:deep(.el-table .chat-highlight) {
  --el-table-tr-bg-color: #fff4cc;
  --el-table-row-hover-bg-color: #ffecab;
}
</style>
