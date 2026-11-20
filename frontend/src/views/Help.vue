<script setup lang="ts">
const steps = [
  { title: '选择学院规范', desc: '在首页「学院规范」下拉框选择你所在学院（如软件学院），系统按该学院的格式规范进行检测与答疑。' },
  { title: '上传论文', desc: '将你的论文 .docx 文件拖入上传区，系统本地解析并执行规则检测与语义检查。' },
  { title: '查看分析结果', desc: '结果页展示综合评分、问题分布与分类明细，可点击每条问题查看基于规范的 AI 解释。' },
  { title: 'AI 对话答疑', desc: '点击右下角「AI 助手」，可询问规范问题（如“一级标题应该用什么字号”），或问“这篇论文哪里不对”。' },
  { title: '确认修改并下载', desc: '在结果页勾选需要修改的问题，点底部「修复并下载」，系统生成修复后的 docx 供你下载。' },
]

const faqs = [
  { q: '支持什么格式的论文文件？', a: '目前仅支持 .docx 格式的 Word 文档。' },
  { q: '论文文件会泄露吗？', a: '不会。论文解析、向量检索均在本地执行，文件不离开你的本机。' },
  { q: '为什么有些问题不能勾选「确认修改」？', a: '后端会为每条问题标注是否可自动修复（fixable）及原因；标记为「暂不支持自动修复」的问题当前没有对应修复通道（如参考文献著录项缺失需要按原文补全），需要你按规范手动调整。' },
  { q: '报告导出和修改模块在哪里？', a: '它们都在「分析结果」页内：报告导出在问题分布右侧，修改模块是下方的分类明细表。' },
]
</script>

<template>
  <div class="help">
    <el-page-header content="帮助" @back="$router.push('/')" />

    <el-card class="card">
      <h2 class="title">使用说明</h2>
      <el-steps direction="vertical" :active="steps.length" finish-status="success" class="steps">
        <el-step
          v-for="(s, i) in steps"
          :key="i"
          :title="`${i + 1}. ${s.title}`"
          :description="s.desc"
        />
      </el-steps>
    </el-card>

    <el-card class="card">
      <h2 class="title">常见问题</h2>
      <el-collapse>
        <el-collapse-item v-for="(f, i) in faqs" :key="i" :title="f.q">
          <p class="faq-a">{{ f.a }}</p>
        </el-collapse-item>
      </el-collapse>
    </el-card>
  </div>
</template>

<style scoped>
.help {
  padding: 16px 0 40px;
}
.card {
  margin-top: 16px;
}
.title {
  margin: 0 0 16px;
  font-size: 16px;
  font-weight: 600;
  color: #1f2d3d;
}
.steps {
  max-width: 720px;
}
.faq-a {
  margin: 0;
  font-size: 13px;
  color: #606266;
  line-height: 1.7;
}
</style>
