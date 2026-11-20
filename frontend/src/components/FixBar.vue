<script setup lang="ts">
import { asApiError } from '../api/request'
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import { store, selectedIssues, selectAllFixable, clearSelection } from '../store'
import { isFixable } from '../utils/issue'
import { runFixFlow, describeFixOutcome } from '../utils/fixflow'

const count = computed(() => store.selectedIds.length)
const fixableCount = computed(() => store.errorList.filter(isFixable).length)
const allSelected = computed(() => fixableCount.value > 0 && count.value >= fixableCount.value)
// 不因「内存里没有文件」禁用按钮：刷新后文件已丢，点击时先去浏览器本地库捞回来，捞不到再提示重新上传。
const canFix = computed(() => count.value > 0 && !store.isFixing)
const progress = computed(() =>
  fixableCount.value ? Math.round((count.value / fixableCount.value) * 100) : 0
)

function onToggleAll() {
  if (allSelected.value) clearSelection()
  else selectAllFixable()
}

async function onFix() {
  const issues = selectedIssues()
  if (!issues.length) return
  store.isFixing = true
  try {
    // 修复 → 下载 → 把修复稿当新基线 → 自动重检，列表/结论/快照都会换成重检后的结果
    const outcome = await runFixFlow(issues)
    const { type, text } = describeFixOutcome(outcome, '修复')
    ElMessage[type](text)
  } catch (e) {
    ElMessage.error('修复失败：' + asApiError(e).message)
  } finally {
    store.isFixing = false
  }
}
</script>

<template>
  <div v-if="store.errorList.length" class="fix-bar">
    <!-- 左：勾选状态 -->
    <span class="state" :class="{ ready: count > 0 }" :title="`共 ${fixableCount} 项可修复，已勾选 ${count} 项`">
      <span class="dot"></span>
      已选 <b>{{ count }}</b><span class="total">/ {{ fixableCount }} 项可修复</span>
    </span>

    <!-- 中：全选开关 -->
    <el-button
      v-if="fixableCount"
      class="toggle"
      type="primary"
      link
      size="small"
      :disabled="store.isFixing"
      @click="onToggleAll"
    >
      {{ allSelected ? '取消全选' : '全选可修复项' }}
    </el-button>

    <!-- 右：主操作，未勾选时用提示代替常驻文案，避免换行/位移 -->
    <el-tooltip content="请先勾选要修改的问题" placement="top" :disabled="count > 0">
      <span class="action">
        <el-button
          type="primary"
          round
          :disabled="!canFix"
          :loading="store.isFixing"
          @click="onFix"
        >
          修复并下载<span class="num">{{ count }}</span>
        </el-button>
      </span>
    </el-tooltip>

    <!-- 底部进度：已选 / 可修复 -->
    <span class="progress"><i :style="{ width: progress + '%' }"></i></span>
  </div>
</template>

<style scoped>
.fix-bar {
  position: fixed;
  left: calc(var(--sidebar-w) + (100% - var(--sidebar-w)) / 2);
  bottom: 24px;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 14px;
  min-width: 440px;
  max-width: calc(100vw - var(--sidebar-w) - 32px);
  padding: 10px 14px 10px 18px;
  background: rgba(255, 255, 255, 0.98);
  border: 1px solid var(--border, #e5e6eb);
  border-radius: 16px;
  box-shadow: 0 10px 30px rgba(31, 45, 61, 0.12), 0 2px 8px rgba(31, 45, 61, 0.06);
  overflow: hidden;
  z-index: 1000;
}
.state {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-right: auto;
  font-size: 14px;
  color: #606266;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #dcdfe6;
  transition: background 0.2s, box-shadow 0.2s;
}
.state.ready .dot {
  background: var(--el-color-primary, #409eff);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 16%, transparent);
}
.state b {
  margin: 0 4px;
  font-size: 15px;
  color: var(--el-color-primary, #409eff);
}
.total {
  color: #909399;
}
.toggle {
  font-weight: 500;
}
.action {
  display: inline-flex;
}
.num {
  display: inline-block;
  min-width: 20px;
  margin-left: 8px;
  padding: 0 7px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.14);
  font-size: 12px;
  line-height: 18px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}
.progress {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 3px;
  background: #f0f2f5;
}
.progress i {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, var(--accent-bright), var(--accent));
  transition: width 0.25s ease;
}

@media (max-width: 900px) {
  .fix-bar {
    min-width: 0;
    gap: 10px;
    padding: 10px 12px 10px 14px;
  }
  .total {
    display: none;
  }
}
@media (max-width: 767.98px) {
  .fix-bar { left: 50%; bottom: 12px; width: calc(100vw - 24px); max-width: none; flex-wrap: wrap; gap: 8px; box-shadow: 0 6px 18px rgba(31, 45, 61, 0.14); }
  .state { font-size: 12px; }
  .action :deep(.el-button) { padding: 8px 12px; }
}
</style>
