<script setup lang="ts">
defineProps({
  title: { type: String, required: true },
  value: { type: [String, Number], default: '—' },
  unit: { type: String, default: '' },
  sub: { type: String, default: '' },
  color: { type: String, default: '#303133' },
})
</script>

<template>
  <div class="score-card">
    <div class="title">{{ title }}</div>
    <div class="value">
      <span class="num" :style="{ color }">{{ value }}</span>
      <span v-if="unit" class="unit">{{ unit }}</span>
    </div>
    <div v-if="sub" class="sub">{{ sub }}</div>
  </div>
</template>

<style scoped>
.score-card {
  background: #fff;
  border: 1px solid #e5e6eb;
  border-radius: 12px;
  padding: 16px 20px;
  min-width: 0;
}
.title {
  font-size: 13px;
  color: #909399;
  margin-bottom: 8px;
}
.value {
  display: flex;
  align-items: baseline;
  gap: 4px;
}
.num {
  font-size: 30px;
  font-weight: 700;
  line-height: 1;
}
.unit {
  font-size: 14px;
  color: #909399;
}
.sub {
  margin-top: 8px;
  font-size: 13px;
  color: #909399;
}
</style>
