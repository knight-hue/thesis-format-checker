<script setup lang="ts">
import type { ThemeOption } from '../theme'
import { THEME_COLORS } from '../theme'
import { store } from '../store'

defineProps({
  small: { type: Boolean, default: false },
})

function pick(c: ThemeOption) {
  store.themeColor = c.value
}
</script>

<template>
  <div class="swatches" :class="{ small }">
    <button
      v-for="c in THEME_COLORS"
      :key="c.value"
      type="button"
      class="swatch"
      :class="{ active: store.themeColor === c.value }"
      :style="{ background: c.value }"
      :title="c.name"
      :aria-label="`主题色：${c.name}`"
      :aria-pressed="store.themeColor === c.value"
      @click="pick(c)"
    />
  </div>
</template>

<style scoped>
.swatches {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.swatch {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  border: 2px solid transparent;
  cursor: pointer;
  padding: 0;
  box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.12);
  transition: transform 0.15s ease, border-color 0.15s ease;
}
.swatch:hover {
  transform: scale(1.08);
}
.swatch.active {
  border-color: #fff;
  box-shadow: 0 0 0 2px var(--accent);
}
.swatches.small .swatch {
  width: 16px;
  height: 16px;
}

@media (prefers-reduced-motion: reduce) {
  .swatch { transition: none; }
  .swatch:hover { transform: none; }
}
</style>
