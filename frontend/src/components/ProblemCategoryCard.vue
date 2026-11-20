<script setup lang="ts">
defineProps({
  name: { type: String, required: true },
  count: { type: Number, default: 0 },
  desc: { type: String, default: '' },
})
</script>

<template>
  <div class="cat-card">
    <div class="head">
      <span class="name">{{ name }}</span>
      <span class="count" :class="{ zero: count === 0 }">{{ count }}项</span>
    </div>
    <div class="desc">{{ desc }}</div>
  </div>
</template>

<style scoped>
.cat-card {
  background: #fff;
  border: 1px solid #e5e6eb;
  border-radius: 10px;
  padding: 14px 16px;
  min-width: 0;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}
.name {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}
.count {
  font-size: 14px;
  font-weight: 600;
  color: #e6a23c;
}
.count.zero {
  color: #67c23a;
}
.desc {
  font-size: 12px;
  color: #909399;
  line-height: 1.5;
}
</style>
