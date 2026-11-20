<script setup lang="ts">
import { asApiError } from '../api/request'
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import { Download } from '@element-plus/icons-vue'
import { store, selectedIssues } from '../store'
import { runFixFlow, describeFixOutcome } from '../utils/fixflow'

const count = computed(() => store.selectedIds.length)

// 报告导出 = 导出修改完成后的完整 docx（后端 /api/fix：改对数据块 → json_to_docx 重建）。
// 与底部「修复并下载」走同一条编排：改完顺手把修复稿作为新基线重检一次，列表随之刷新。
async function exportFixedDocx() {
  store.isFixing = true
  try {
    const outcome = await runFixFlow(selectedIssues())
    const { type, text } = describeFixOutcome(outcome, '导出')
    ElMessage[type](text)
  } catch (e) {
    ElMessage.error('导出失败：' + asApiError(e).message)
  } finally {
    store.isFixing = false
  }
}
</script>

<template>
  <el-button
    type="success"
    class="export-btn"
    :loading="store.isFixing"
    @click="exportFixedDocx"
  >
    <el-icon class="icon"><Download /></el-icon>
    导出修改后的文档{{ count ? `（${count} 项）` : '' }}
  </el-button>
</template>

<style scoped>
.export-btn {
  width: 100%;
  border-radius: 8px;
}
.icon {
  margin-right: 6px;
}
</style>
