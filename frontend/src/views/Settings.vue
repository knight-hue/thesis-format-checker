<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { store, clearResult, resetCheckStatus, discardPendingFile } from '../store'
import { clearOriginalFile } from '../utils/idb'
import { clearPendingCheckJob } from '../api/parser'
import ColorSwatches from '../components/ColorSwatches.vue'
import { DEFAULT_THEME } from '../theme'

function chooseLanguage(lang: 'zh' | 'en') {
  if (lang === 'en') {
    ElMessage.info('英文界面即将上线，敬请期待')
    return
  }
  store.language = 'zh'
}

function resetTheme() {
  store.themeColor = DEFAULT_THEME
  ElMessage.success('已恢复默认强调色（学院蓝）')
}

function clearSession() {
  store.highlightRuleIds = []
  store.highlightTargets = []
  store.errorList = []
  store.totalIssues = 0
  store.groupedIssues = 0
  store.returnedIssues = 0
  store.issuesTruncated = false
  store.issueSummary = []
  store.fileName = ''
  store.selectedIds = []
  store.breakdown = null
  store.unchecked = []
  store.advisoryList = []
  store.pendingReview = []
  store.passed = null
  store.elapsed = null
  store.fixedRounds = 0
  // 快照与浏览器本地库里留存的基线文档一起清掉，否则刷新后结果页又会被还原回来
  clearResult()
  resetCheckStatus()
  clearOriginalFile()
  discardPendingFile()
  clearPendingCheckJob()
  ElMessage.success('已清空当前检测会话')
}
</script>

<template>
  <div class="settings">
    <el-page-header content="设置" @back="$router.push('/')" />

    <el-card class="card">
      <h3 class="title">界面强调色</h3>
      <p class="desc">选择后侧栏光效、顶栏、按钮、弹窗描边和 AI 助手都会跟随变化。默认学院蓝。</p>
      <ColorSwatches />
      <el-button class="reset" size="small" @click="resetTheme">恢复默认（学院蓝）</el-button>
    </el-card>

    <el-card class="card">
      <h3 class="title">语言 Language</h3>
      <p class="desc">界面显示语言（英文暂未开放）。</p>
      <el-button-group>
        <el-button :type="store.language === 'zh' ? 'primary' : ''" @click="chooseLanguage('zh')">
          中文
        </el-button>
        <el-button @click="chooseLanguage('en')">English</el-button>
      </el-button-group>
    </el-card>

    <el-card class="card">
      <h3 class="title">其他</h3>
      <el-button type="danger" plain @click="clearSession">清空当前检测会话</el-button>
    </el-card>
  </div>
</template>

<style scoped>
.settings {
  padding: 16px 0 40px;
  max-width: 720px;
}
.card {
  margin-top: 16px;
}
.title {
  margin: 0 0 8px;
  font-size: 15px;
  font-weight: 600;
  color: #1f2d3d;
}
.desc {
  margin: 0 0 16px;
  font-size: 13px;
  color: #909399;
}
.reset {
  margin-top: 16px;
}
</style>
