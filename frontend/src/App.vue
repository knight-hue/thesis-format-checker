<script setup lang="ts">
import { watch } from 'vue'
import { store } from './store'
import Sidebar from './components/Sidebar.vue'
import ChatPanel from './components/ChatPanel.vue'
import { applyTheme, saveTheme } from './theme'

// 主题色变化即应用并持久化
watch(() => store.themeColor, (c) => {
  applyTheme(c)
  saveTheme(c)
})
</script>

<template>
  <div class="app-shell">
    <Sidebar />
    <main class="content">
      <div class="deco deco-main"></div>
      <div class="content-inner">
        <router-view />
      </div>
    </main>
    <ChatPanel />
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  min-height: 100vh;
  min-height: 100dvh;
}
.content {
  flex: 1;
  min-width: 0;
  position: relative;
  background:
    radial-gradient(900px 420px at 100% -12%, var(--accent-soft) 0%, transparent 62%),
    radial-gradient(720px 360px at -8% 100%, color-mix(in srgb, var(--accent-2) 5%, transparent) 0%, transparent 60%),
    var(--content-bg);
  overflow-x: clip;
}
.content-inner {
  position: relative;
  z-index: 1;
  max-width: 1040px;
  margin: 0 auto;
  padding: 0 32px 96px;
}
/* 主区域右上角同色强调点缀：只用静态色块，避免大面积渐变重绘 */
.deco-main {
  position: absolute;
  top: -120px;
  right: -120px;
  width: 300px;
  height: 300px;
  border-radius: 50%;
  background: radial-gradient(circle, var(--accent-soft) 0%, transparent 70%);
  opacity: 0.7;
  pointer-events: none;
  z-index: 0;
}
@media (max-width: 767.98px) {
  .app-shell { flex-direction: column; }
  .content-inner { padding: 0 16px 104px; }
  .deco-main { display: none; }
}
</style>
