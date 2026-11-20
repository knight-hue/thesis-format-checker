<script setup lang="ts">
import type { Issue, IssuePreview } from '../types/issue'
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import ErrorCard from '../components/ErrorCard.vue'
import RagDialog from '../components/RagDialog.vue'
import FixBar from '../components/FixBar.vue'
import { store, missingResultMessage } from '../store'
import { issueTitle } from '../utils/issue'

const router = useRouter()

const errorList = computed(() => store.errorList)
const suggestedCount = computed(() => store.advisoryList.length)
const pendingCount = computed(() => store.pendingReview.length)
const dialogVisible = ref(false)
const currentError = ref<IssuePreview>({})

onMounted(() => {
  // 与结果页同一判据：没有 passed 就不是一份可展示的结果
  if (!store.fileName || store.passed === null) {
    ElMessage.warning(missingResultMessage())
    router.push('/')
  }
})

function openExplain(err: Issue) {
  currentError.value = err
  dialogVisible.value = true
}
</script>

<template>
  <div class="detail">
    <el-page-header content="定位明细" @back="router.push('/result')" />

    <el-alert
      v-if="suggestedCount || pendingCount"
      class="state-note"
      type="info"
      show-icon
      :closable="false"
      :title="`另有 AI 建议 ${suggestedCount} 条、待人工 ${pendingCount} 条`"
    >
      本页只列出「确定问题」的定位；AI 建议与待人工条目请在结果页查看三态清单。
      <el-button text type="primary" @click="router.push('/result')">返回结果页</el-button>
    </el-alert>

    <div v-if="!errorList.length" class="empty">暂无格式错误。</div>

    <div v-else class="grid">
      <ErrorCard
        v-for="err in errorList"
        :key="err._id || err.rule_id"
        :error="err"
        @explain="openExplain"
      />
    </div>

    <RagDialog
      v-model="dialogVisible"
      :college-id="store.collegeId"
      :rule-id="currentError.rule_id || ''"
      :rule-name="issueTitle(currentError)"
    />

    <FixBar />
  </div>
</template>

<style scoped>
.detail {
  padding-top: 16px;
  padding-bottom: 96px;
}
.empty {
  padding: 48px 0;
  text-align: center;
  color: #909399;
}
.state-note {
  margin-top: 16px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
  margin-top: 16px;
}
@media (max-width: 767.98px) {
  .grid {
    grid-template-columns: 1fr;
  }
}
</style>
