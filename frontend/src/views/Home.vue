<script setup lang="ts">
import type { CollegeInfo, CheckResponse, PaperFile, CheckJobStatus } from '../types/store'
import type { UploadFile } from 'element-plus'
import { asApiError } from '../api/request'
import { computed, ref, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  parseDocument,
  getPendingCheckJob,
  resumePendingDocumentCheck,
} from '../api/parser'
import { listColleges } from '../api/rag'
import {
  store,
  ensureConversationId,
  applyCheckResult,
  markCheckStarted,
  markCheckFailed,
  markFixedRound,
  rememberPendingFile,
  resolvePendingFile,
  discardPendingFile,
} from '../store'

const router = useRouter()
const step = ref(0)
const uploading = ref(false)
const collegesLoading = ref(false)
const checkProgress = ref(0)
const checkMessage = ref('')
const canResume = ref(false)
const schoolId = ref('')

function schoolKey(item: CollegeInfo) {
  return item.school || item.school_name || ''
}

// 学校列表从学院数据的坐标字段聚合，不维护任何写死的学校清单。
const schoolOptions = computed(() => {
  const map = new Map<string, string>()
  for (const item of store.colleges) {
    const key = schoolKey(item)
    if (key && !map.has(key)) map.set(key, item.school_name || key)
  }
  return Array.from(map, ([value, label]) => ({ value, label }))
})

const collegeOptions = computed(() => (
  store.colleges.filter((item) => schoolKey(item) === schoolId.value)
))

function syncSelection() {
  const current = store.colleges.find((item) => item.college_id === store.collegeId)
  if (current) {
    schoolId.value = schoolKey(current)
    return
  }
  schoolId.value = schoolOptions.value[0]?.value || ''
  store.collegeId = collegeOptions.value[0]?.college_id || ''
}

function onSchoolChange(value: string) {
  const first = store.colleges.find((item) => schoolKey(item) === value)
  store.collegeId = first?.college_id || ''
}

// 快照/外部恢复 collegeId 时，让学校选择器跟着同步。
watch(() => store.collegeId, (id) => {
  const current = store.colleges.find((item) => item.college_id === id)
  if (current) schoolId.value = schoolKey(current)
})

function updateProgress(status: CheckJobStatus) {
  checkProgress.value = status.progress || 0
  checkMessage.value = status.message || '正在检测论文'
}

async function finishCheck(data: CheckResponse, file: PaperFile | null, fileName: string, startedAt: number, recheck = false) {
  if (!recheck) store.fixedRounds = 0
  await applyCheckResult(data, file, {
    fileName,
    elapsed: Number(((Date.now() - startedAt) / 1000).toFixed(1)),
  })
  if (recheck) markFixedRound()
  step.value = 2
  canResume.value = false
  ElMessage.success('检测完成')
  router.push('/result')
}

async function resumeCheck() {
  const pending = getPendingCheckJob()
  if (!pending || uploading.value) return
  markCheckStarted(pending.fileName)
  uploading.value = true
  canResume.value = false
  step.value = 1
  checkMessage.value = '正在恢复检测任务…'
  try {
    const recovered = await resumePendingDocumentCheck(updateProgress)
    if (!recovered) return
    const file = await resolvePendingFile()
    await finishCheck(
      recovered.result,
      file,
      recovered.pending.fileName,
      recovered.pending.startedAt || Date.now(),
      recovered.pending.recheck === true,
    )
  } catch (caught) {
    const error = asApiError(caught)
    if (error.recoverable) {
      canResume.value = true
      checkMessage.value = error.message
      ElMessage.warning(error.message)
    } else {
      markCheckFailed()
      await discardPendingFile()
      checkMessage.value = error.message
      ElMessage.error(error.message)
    }
  } finally {
    uploading.value = false
  }
}

onMounted(async () => {
  collegesLoading.value = true
  try {
    const colleges = await listColleges()
    if (Array.isArray(colleges) && colleges.length) {
      store.colleges = colleges
      syncSelection()
    }
  } catch {
    // 学院列表获取失败（如后端未就绪）：collegeId 保持为空，上传前会被后端拦下，
    // 不用一个写死的学院顶替——多学校时那个默认值一定是错的。
  } finally {
    collegesLoading.value = false
  }
  if (getPendingCheckJob()) await resumeCheck()
})

async function onFileChange(uploadFile: UploadFile) {
  const file = uploadFile.raw
  if (!file) return

  if (!file.name.toLowerCase().endsWith('.docx')) {
    ElMessage.warning('请选择 .docx 文件')
    return
  }

  markCheckStarted(file.name)
  await rememberPendingFile(file)
  uploading.value = true
  canResume.value = false
  step.value = 1
  checkProgress.value = 0
  checkMessage.value = '正在上传并校验论文…'
  const startedAt = Date.now()
  try {
    const data = await parseDocument(
      file,
      store.collegeId,
      store.includeSemantic,
      ensureConversationId(),
      false,
      updateProgress,
    )
    await finishCheck(data, file, file.name, startedAt)
  } catch (caught) {
    const e = asApiError(caught)
    if (e.recoverable) {
      canResume.value = true
      checkMessage.value = e.message
      ElMessage.warning(e.message)
    } else {
      markCheckFailed()
      await discardPendingFile()
      checkMessage.value = e.message
      ElMessage.error('检测失败：' + e.message)
    }
  } finally {
    uploading.value = false
  }
}
</script>

<template>
  <div class="home">
    <el-card class="card">
      <h2 class="title">上传论文</h2>

      <div class="selector">
        <div class="select-field">
          <span class="label">学校</span>
          <el-select
            v-model="schoolId"
            class="school-select"
            placeholder="选择学校"
            :loading="collegesLoading"
            @change="onSchoolChange"
          >
            <el-option
              v-for="school in schoolOptions"
              :key="school.value"
              :label="school.label"
              :value="school.value"
            />
          </el-select>
        </div>
        <div class="select-field">
          <span class="label">学院</span>
          <el-select
            v-model="store.collegeId"
            class="college-select"
            placeholder="选择学院"
            :loading="collegesLoading"
            :disabled="!schoolId"
          >
            <el-option
              v-for="c in collegeOptions"
              :key="c.college_id"
              :label="c.college_name || c.display_name"
              :value="c.college_id"
            />
          </el-select>
        </div>
      </div>

      <div class="semantic-row">
        <span class="label">RAG 语义检查</span>
        <el-switch v-model="store.includeSemantic" />
        <span class="hint">需 AI 服务；AI 不可用时自动降级，仍检查目录/致谢/参考文献/列表序号/单位/数字</span>
      </div>

      <el-steps :active="step" align-center finish-status="success">
        <el-step title="上传论文" />
        <el-step title="检测格式" />
        <el-step title="查看结果" />
      </el-steps>

      <el-upload
        class="uploader"
        drag
        accept=".docx"
        :auto-upload="false"
        :show-file-list="false"
        :disabled="uploading"
        :on-change="onFileChange"
      >
        <el-icon class="el-icon--upload"><upload-filled /></el-icon>
        <div class="el-upload__text">将 docx 文件拖到此处，或<em>点击选择</em></div>
        <template #tip>
          <div class="el-upload__tip">仅支持 .docx 格式文件</div>
        </template>
      </el-upload>

      <div v-if="uploading" class="status">
        <el-progress :percentage="checkProgress" :status="checkProgress === 100 ? 'success' : ''" />
        <span>{{ checkMessage || '正在检测论文…' }}</span>
      </div>
      <div v-else-if="canResume" class="status recoverable">
        <span>{{ checkMessage }}</span>
        <el-button type="primary" plain @click="resumeCheck">继续查询</el-button>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.home {
  display: flex;
  justify-content: center;
  padding-top: 48px;
}
.card {
  width: 640px;
  max-width: 100%;
}
.title {
  margin: 0 0 24px;
  text-align: center;
}
.selector {
  display: flex;
  flex-wrap: wrap;
  gap: 14px 22px;
  margin-bottom: 24px;
}
.select-field {
  display: flex;
  align-items: center;
  gap: 10px;
}
.school-select { width: 190px; }
.college-select { width: 250px; }
.semantic-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 24px;
}
.hint {
  font-size: 12px;
  color: #909399;
}
.label {
  font-size: 14px;
  color: #606266;
}
.uploader {
  margin-top: 28px;
}
.status {
  margin-top: 16px;
  text-align: center;
  color: #909399;
  display: grid;
  gap: 10px;
}
.recoverable {
  justify-items: center;
}
@media (max-width: 767.98px) {
  .home { padding-top: 20px; }
  .selector { gap: 12px; }
  .select-field { width: 100%; }
  .school-select, .college-select { flex: 1; width: auto; }
  .semantic-row { flex-wrap: wrap; }
}
</style>
