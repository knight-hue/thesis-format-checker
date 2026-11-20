<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { store } from '../store'
import ColorSwatches from './ColorSwatches.vue'

const router = useRouter()
const route = useRoute()

const drawerOpen = ref(false)
const isMobile = ref(false)
const menuBtn = ref<HTMLButtonElement | null>(null)
const closeBtn = ref<HTMLButtonElement | null>(null)
let mobileQuery: MediaQueryList | null = null

interface NavItem { key: string; label: string; icon: string; to: string; section?: string }

const NAV: NavItem[] = [
  { key: 'home', label: '论文上传', icon: 'UploadFilled', to: 'home' },
  { key: 'result', label: '分析结果', icon: 'DataAnalysis', to: 'result' },
  { key: 'report', label: '报告导出', icon: 'Document', to: 'result', section: 'report-export' },
  { key: 'modify', label: '修改模块', icon: 'EditPen', to: 'result', section: 'modify' },
  { key: 'help', label: '帮助', icon: 'QuestionFilled', to: 'help' },
  { key: 'settings', label: '设置', icon: 'Setting', to: 'settings' },
]

function isActive(item: NavItem) {
  if (item.section) return false
  if (item.to === 'result') return route.name === 'result' || route.name === 'detail'
  return route.name === item.to
}

function onNav(item: NavItem) {
  drawerOpen.value = false
  if (item.section) {
    if (route.name === 'result') {
      document.getElementById(item.section)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    } else {
      router.push({ name: 'result', query: { section: item.section } })
    }
  } else {
    router.push({ name: item.to })
  }
}

function applyMobile(matches: boolean) {
  isMobile.value = matches
  if (!matches) drawerOpen.value = false
}

const onMediaChange = (event: MediaQueryListEvent) => applyMobile(event.matches)

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && drawerOpen.value) drawerOpen.value = false
}

watch(drawerOpen, async (open) => {
  document.body.style.overflow = open && isMobile.value ? 'hidden' : ''
  if (open) {
    await nextTick()
    closeBtn.value?.focus()
  } else if (isMobile.value) {
    menuBtn.value?.focus()
  }
})

watch(() => route.fullPath, () => { drawerOpen.value = false })

onMounted(() => {
  mobileQuery = window.matchMedia('(max-width: 767.98px)')
  applyMobile(mobileQuery.matches)
  mobileQuery.addEventListener('change', onMediaChange)
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  mobileQuery?.removeEventListener('change', onMediaChange)
  window.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = ''
})
</script>

<template>
  <!-- 手机端顶栏：菜单 + 标题；桌面/平板隐藏 -->
  <header class="mobilebar">
    <button ref="menuBtn" class="menu-btn" type="button" aria-label="打开导航菜单" aria-controls="app-sidebar" :aria-expanded="drawerOpen" @click="drawerOpen = true">
      <span class="menu-lines" aria-hidden="true"><i></i><i></i><i></i></span>
    </button>
    <div class="mobile-title">论文格式检查系统</div>
  </header>

  <div v-if="drawerOpen" class="scrim" @click="drawerOpen = false"></div>

  <aside
    id="app-sidebar"
    class="sidebar"
    :class="{ open: drawerOpen }"
    :inert="isMobile && !drawerOpen"
  >
    <div class="glow" aria-hidden="true"></div>
    <div class="beam beam-a" aria-hidden="true"></div>
    <div class="beam beam-b" aria-hidden="true"></div>
    <div class="dot-grid" aria-hidden="true"></div>
    <button
      ref="closeBtn"
      class="close-btn"
      type="button"
      aria-label="关闭导航菜单"
      @click="drawerOpen = false"
    >
      <el-icon><Close /></el-icon>
    </button>

    <!-- 品牌：只用文字与渐变色条 -->
    <div class="brand">
      <div class="brand-name">
        <div class="brand-title">论文格式检查系统</div>
        <div class="brand-sub">智能格式校验</div>
      </div>
    </div>

    <nav class="nav" aria-label="主导航">
      <template v-for="(item, index) in NAV" :key="item.key">
        <div v-if="index === 0 || index === 4" class="nav-caption" :class="{ secondary: index === 4 }">
          {{ index === 0 ? '工作台' : '系统' }}
        </div>
        <button
          type="button"
          class="nav-item"
          :class="{ active: isActive(item) }"
          :title="item.label"
          @click="onNav(item)"
        >
          <span class="nav-active-bar" aria-hidden="true"></span>
          <span class="nav-icon-wrap"><el-icon class="nav-icon"><component :is="item.icon" /></el-icon></span>
          <span class="nav-label">{{ item.label }}</span>
        </button>
      </template>
    </nav>

    <div class="foot">
      <div v-if="store.fileName" class="file" :title="store.fileName">
        <el-icon class="file-icon"><Document /></el-icon>
        <span class="file-name">当前文件：{{ store.fileName }}</span>
      </div>
      <div class="theme-row">
        <span class="theme-label">强调色</span>
        <ColorSwatches small />
      </div>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  position: sticky;
  top: 0;
  height: 100vh;
  height: 100dvh;
  width: var(--sidebar-w);
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background:
    radial-gradient(120% 80% at 100% -10%, color-mix(in srgb, var(--accent-bright) 26%, transparent) 0%, transparent 58%),
    radial-gradient(90% 70% at -10% 110%, color-mix(in srgb, var(--accent-2) 18%, transparent) 0%, transparent 60%),
    linear-gradient(165deg, #172a45 0%, #101f34 48%, #0b1422 100%);
  color: var(--sidebar-text);
  overflow: hidden;
  overflow: clip;
  z-index: 100;
}
/* 右侧不用一条死板的实线，改成从蓝到紫再淡出的细分隔线 */
.sidebar::after {
  content: '';
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: 1px;
  background: linear-gradient(180deg, transparent, color-mix(in srgb, var(--accent-bright) 38%, transparent) 18%, color-mix(in srgb, var(--accent-2) 28%, transparent) 58%, transparent);
  pointer-events: none;
  z-index: 2;
}
/* 一个低透明度强调色光晕，替代原来的三个大色块 */
.glow {
  position: absolute;
  top: -140px;
  right: -120px;
  width: 340px;
  height: 280px;
  border-radius: 58% 42% 55% 45% / 48% 55% 45% 52%;
  background:
    radial-gradient(circle at 65% 30%, color-mix(in srgb, var(--accent-bright) 90%, transparent), transparent 62%),
    radial-gradient(circle at 30% 80%, color-mix(in srgb, var(--accent-2) 65%, transparent), transparent 60%);
  opacity: 0.18;
  pointer-events: none;
  z-index: 0;
}
/* 两条斜向光带，避免侧栏只剩一个圆形光晕 */
.beam {
  position: absolute;
  z-index: 0;
  pointer-events: none;
  opacity: 0.32;
}
.beam-a {
  top: 120px;
  right: -110px;
  width: 170px;
  height: 430px;
  transform: rotate(24deg);
  background: linear-gradient(180deg, transparent, color-mix(in srgb, var(--accent-bright) 30%, transparent) 42%, transparent);
}
.beam-b {
  left: -130px;
  bottom: -90px;
  width: 190px;
  height: 360px;
  transform: rotate(-28deg);
  background: linear-gradient(180deg, transparent, color-mix(in srgb, var(--accent-2) 22%, transparent) 48%, transparent);
}
.dot-grid {
  position: absolute;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  background-image: radial-gradient(rgba(255, 255, 255, 0.18) 1px, transparent 1px);
  background-size: 18px 18px;
  opacity: 0.12;
  -webkit-mask-image: linear-gradient(180deg, transparent, #000 18%, #000 72%, transparent);
  mask-image: linear-gradient(180deg, transparent, #000 18%, #000 72%, transparent);
}
.close-btn { display: none; }

.brand {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: 26px 18px 18px;
}
/* 一条蓝紫渐变短线作为品牌标记 */
.brand::before {
  content: '';
  display: block;
  width: 30px;
  height: 3px;
  margin-bottom: 10px;
  border-radius: 2px;
  background: linear-gradient(90deg, var(--accent-bright), var(--accent-2) 62%, var(--accent-3));
}
.brand-name { min-width: 0; }
.brand-title {
  font-size: 16px;
  font-weight: 700;
  line-height: 1.35;
  color: #fff;
  letter-spacing: 0.2px;
}
.brand-sub {
  margin-top: 3px;
  font-size: 11px;
  color: var(--sidebar-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.nav {
  position: relative;
  z-index: 1;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-height: 0;
  padding: 6px 10px 12px;
  overflow-y: auto;
}
.nav-caption {
  padding: 6px 12px 2px;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 1.5px;
  color: var(--sidebar-muted);
  user-select: none;
}
.nav-caption.secondary { margin-top: 8px; }
.nav-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 9px 12px 9px 14px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: transparent;
  color: var(--sidebar-text);
  font-size: 14px;
  cursor: pointer;
  text-align: left;
  transition: background 0.16s ease, color 0.16s ease;
}
.nav-icon-wrap {
  flex: 0 0 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.05);
  transition: background 0.16s ease, border-color 0.16s ease;
}
.nav-item:hover .nav-icon-wrap {
  background: rgba(255, 255, 255, 0.1);
  border-color: rgba(255, 255, 255, 0.12);
}
.nav-item.active .nav-icon-wrap {
  border-color: transparent;
  background: linear-gradient(135deg, var(--accent), var(--accent-2));
  box-shadow: 0 4px 12px color-mix(in srgb, var(--accent) 30%, transparent);
  color: #fff;
}
.nav-item:hover {
  background: rgba(255, 255, 255, 0.06);
  color: #fff;
}
.nav-item.active {
  background: linear-gradient(90deg, color-mix(in srgb, var(--accent-bright) 26%, transparent), color-mix(in srgb, var(--accent-bright) 7%, transparent) 72%, color-mix(in srgb, var(--accent-2) 6%, transparent));
  border: 1px solid color-mix(in srgb, var(--accent-bright) 18%, transparent);
  box-shadow: 0 6px 18px color-mix(in srgb, var(--accent) 22%, transparent);
  color: var(--sidebar-active-text);
  font-weight: 600;
}
.nav-active-bar {
  position: absolute;
  left: 0;
  top: 50%;
  width: 3px;
  height: 18px;
  border-radius: 0 3px 3px 0;
  background: linear-gradient(180deg, var(--accent-3), var(--accent-bright) 32%, var(--accent) 68%, var(--accent-2));
  transform: translateY(-50%) scaleY(0);
  transition: transform 0.16s ease;
}
.nav-item.active .nav-active-bar { transform: translateY(-50%) scaleY(1); }
.nav-icon { flex: 0 0 auto; font-size: 17px; }
.nav-label { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.foot {
  position: relative;
  z-index: 1;
  padding: 14px 16px 18px;
  border-top: 1px solid var(--sidebar-border);
}
.file {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-bottom: 10px;
  font-size: 11px;
  line-height: 1.5;
  color: var(--sidebar-muted);
}
.file-icon { flex: 0 0 auto; margin-top: 1px; font-size: 13px; }
.file-name {
  display: -webkit-box;
  overflow: hidden;
  word-break: break-all;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.theme-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 0;
}
.theme-label { font-size: 11px; color: var(--sidebar-muted); white-space: nowrap; }

.mobilebar { display: none; }
.scrim { display: none; }

/* 平板：收成 78px 图标栏，标签隐藏，靠 title 提示 */
@media (max-width: 1199.98px) and (min-width: 768px) {
  .sidebar { align-items: center; }
  .brand { display: none; }
  .brand-name,
  .nav-label,
  .nav-caption,
  .file,
  .theme-row { display: none; }
  .nav { width: 100%; padding: 14px 8px 6px; }
  .nav-item { justify-content: center; gap: 0; padding: 10px 0; }
  .nav-icon-wrap { width: 32px; height: 32px; flex-basis: 32px; }
  .nav-icon { font-size: 20px; }
  .foot { display: none; }
}

/* 手机：顶部栏 + 左侧抽屉，不再把 6 个入口挤成 3×2 */
@media (max-width: 767.98px) {
  .mobilebar {
    position: sticky;
    top: 0;
    z-index: 900;
    display: flex;
    align-items: center;
    gap: 10px;
    height: 56px;
    padding: 0 14px;
    background:
      radial-gradient(120% 180% at 100% 0%, color-mix(in srgb, var(--accent-bright) 38%, transparent) 0%, transparent 58%),
      radial-gradient(80% 120% at 0% 100%, color-mix(in srgb, var(--accent-2) 18%, transparent) 0%, transparent 60%),
      linear-gradient(135deg, #172a45 0%, #0e1a2b 58%, #0b1422 100%);
    color: #fff;
    box-shadow: 0 6px 18px rgba(8, 15, 28, 0.18);
  }
  .mobilebar::after {
    content: '';
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    height: 2px;
    background: linear-gradient(90deg, var(--accent-bright), var(--accent-2) 55%, var(--accent-3) 82%, transparent);
  }
  .menu-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 36px;
    height: 36px;
    padding: 0;
    border: 1px solid color-mix(in srgb, var(--accent-bright) 24%, transparent);
    border-radius: 10px;
    background: linear-gradient(135deg, color-mix(in srgb, var(--accent-bright) 28%, transparent), rgba(255, 255, 255, 0.06));
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.18), 0 4px 12px color-mix(in srgb, var(--accent) 20%, transparent);
    color: #fff;
    cursor: pointer;
  }
  .menu-btn:active { transform: translateY(1px); }
  .menu-lines {
    display: flex;
    flex-direction: column;
    gap: 4px;
    width: 16px;
  }
  .menu-lines i {
    display: block;
    height: 2px;
    border-radius: 2px;
    background: linear-gradient(90deg, #ffffff, #a9c8ff);
  }
  .menu-lines i:nth-child(2) { width: 11px; }
  .menu-lines i:nth-child(3) { width: 7px; }
  .mobile-title {
    min-width: 0;
    overflow: hidden;
    font-size: 14px;
    font-weight: 600;
    white-space: nowrap;
    text-overflow: ellipsis;
  }

  .scrim {
    position: fixed;
    inset: 0;
    z-index: 1000;
    display: block;
    background: rgba(15, 23, 42, 0.38);
    animation: scrim-in 0.18s ease;
  }
  .sidebar {
    position: fixed;
    top: 0;
    left: 0;
    bottom: 0;
    width: min(288px, 86vw);
    height: 100vh;
    height: 100dvh;
    transform: translateX(-100%);
    border-right: 0;
    border-radius: 0 20px 20px 0;
    background:
      linear-gradient(180deg, #ffffff 0%, color-mix(in srgb, var(--accent-soft) 55%, #f8fbff) 52%, color-mix(in srgb, var(--accent-soft) 85%, #eef5ff) 100%);
    color: var(--text);
    box-shadow: 24px 0 60px rgba(8, 15, 28, 0.24);
    transition: transform 0.22s ease;
    z-index: 1001;
  }
  .sidebar::after { display: none; }
  .sidebar.open { transform: none; }
  .glow { opacity: 0.14; }
  .beam { opacity: 0.18; }
  .dot-grid {
    opacity: 0.1;
    background-image: radial-gradient(rgba(15, 23, 42, 0.22) 1px, transparent 1px);
  }
  .close-btn {
    position: absolute;
    top: 14px;
    right: 14px;
    z-index: 2;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    padding: 0;
    border: none;
    border-radius: 50%;
    background: rgba(15, 23, 42, 0.06);
    color: #475569;
    cursor: pointer;
  }
  .brand { padding: 18px 18px 14px; }
  .brand-name { display: block; }
  .brand-title { color: var(--text); }
  .brand-sub { color: var(--muted); }
  .nav { width: auto; padding: 6px 10px 12px; overscroll-behavior: contain; }
  .nav-caption { color: var(--muted); }
  .nav-item { justify-content: flex-start; gap: 12px; padding: 10px 12px 10px 14px; color: #475569; }
  .nav-item:hover { background: color-mix(in srgb, var(--accent) 8%, transparent); color: var(--accent-strong); }
  .nav-icon-wrap { border-color: rgba(15, 23, 42, 0.06); background: rgba(15, 23, 42, 0.04); }
  .nav-item:hover .nav-icon-wrap { background: color-mix(in srgb, var(--accent) 10%, transparent); border-color: color-mix(in srgb, var(--accent) 16%, transparent); }
  .nav-item.active {
    border-color: color-mix(in srgb, var(--accent) 22%, transparent);
    background: linear-gradient(90deg, color-mix(in srgb, var(--accent) 16%, transparent), color-mix(in srgb, var(--accent-2) 5%, transparent));
    box-shadow: 0 6px 18px color-mix(in srgb, var(--accent) 14%, transparent);
    color: var(--accent-strong);
  }
  .nav-icon { font-size: 18px; }
  .nav-label { display: inline; }
  .foot { width: auto; padding: 14px 18px 18px; border-top-color: rgba(15, 23, 42, 0.08); }
  .file { display: flex; color: var(--muted); }
  .theme-row { display: flex; }
  .theme-label { color: var(--muted); }
}

@keyframes scrim-in {
  from { opacity: 0; }
  to { opacity: 1; }
}

@media (prefers-reduced-motion: reduce) {
  .sidebar { transition: none; }
  .scrim { animation: none; }
  .nav-item,
  .nav-active-bar { transition: none; }
}
</style>
