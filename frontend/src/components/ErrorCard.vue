<script setup lang="ts">
import type { Issue } from '../types/issue'
import { computed } from 'vue'
import { toggleIssue, isSelected } from '../store'
import {
  issueTitle,
  issuePosition,
  issueLevel,
  isFixable,
  fixReason,
  issueOtherVariantTexts,
  issueBasisDetail,
} from '../utils/issue'

const props = defineProps<{ error: Issue }>()
defineEmits<{ explain: [issue: Issue] }>()

// 同一规则名下除代表值以外的其它实测值（后端 output 的 variants）。
const otherVariants = computed(() => issueOtherVariantTexts(props.error))

// 语义层自己的判定说明（后端 basis_detail）：模板依据之外的补充，没有就不显示。
const basisDetail = computed(() => issueBasisDetail(props.error))
</script>

<template>
  <div class="error-card" :class="{ unfixable: !isFixable(error) }">
    <div class="row1">
      <div class="left">
        <el-icon class="warn-icon"><warning-filled /></el-icon>
        <span class="title">{{ issueTitle(error) }}</span>
        <el-tag
          v-if="error.occurrence_count > 1"
          size="small"
          type="info"
          effect="plain"
        >{{ error.occurrence_count }} 处</el-tag>
        <el-tooltip
          v-if="!isFixable(error)"
          :content="fixReason(error)"
          placement="top"
        >
          <el-tag
            size="small"
            type="info"
            effect="plain"
            class="manual-tag"
          >暂不支持自动修复</el-tag>
        </el-tooltip>
      </div>
      <div class="actions">
        <el-button size="small" type="warning" plain round @click="$emit('explain', error)">
          提示
        </el-button>
        <el-checkbox
          :model-value="isSelected(error._id)"
          :disabled="!isFixable(error)"
          @change="toggleIssue(error._id)"
        >确认修改</el-checkbox>
      </div>
    </div>

    <div class="row2">
      <span class="level" :class="error.severity">{{ issueLevel(error) }}</span>
      {{ issuePosition(error) }}
    </div>

    <div class="row3">
      <div class="line">实际：{{ error.actual ?? '—' }}</div>
      <div class="line">要求：{{ error.expected ?? '—' }}</div>
    </div>

    <div v-if="otherVariants.length" class="row4">
      另有 {{ otherVariants.length }} 种实测值：{{ otherVariants.join('、') }}
    </div>

    <div v-if="error.basis" class="row-basis">
      <span class="basis-label">模板依据</span>
      {{ error.basis }}
      <div v-if="basisDetail" class="basis-detail">判定说明：{{ basisDetail }}</div>
    </div>
  </div>
</template>

<style scoped>
.error-card {
  background: #fff;
  border: 1px solid #e5e6eb;
  border-radius: 10px;
  padding: 14px 16px;
  /* 网格项默认 min-width:auto，长标题/长依据会把卡片撑出容器（移动端 375 实测 507px，
     右侧按钮被裁掉且无法滚动）。这里允许收缩，文字用换行处理。 */
  min-width: 0;
}
.error-card.unfixable {
  background: #fafafa;
}
.row1 {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
  flex-wrap: wrap;
}
.left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.warn-icon {
  color: #e6a23c;
  font-size: 18px;
  flex-shrink: 0;
}
.title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  /* 允许在窄屏被压缩，而不是把整张卡片撑宽 */
  min-width: 0;
}
.manual-tag {
  flex-shrink: 0;
}
.actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
.row2 {
  margin-top: 10px;
  font-size: 13px;
  color: #909399;
  display: flex;
  align-items: center;
  gap: 6px;
}
.level {
  font-size: 12px;
  padding: 1px 6px;
  border-radius: 4px;
}
.level.error {
  color: #f56c6c;
  background: #fef0f0;
}
.level.warning {
  color: #e6a23c;
  background: #fdf6ec;
}
.row3 {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 13px;
  color: #606266;
}
.row4 {
  margin-top: 6px;
  font-size: 13px;
  color: #909399;
}
.row-basis {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed #ebeef5;
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
}
.basis-label {
  display: inline-block;
  margin-right: 6px;
  padding: 0 6px;
  border-radius: 4px;
  background: #f4f4f5;
  color: #606266;
}
.basis-detail {
  margin-top: 4px;
  color: #a8abb2;
}
</style>
