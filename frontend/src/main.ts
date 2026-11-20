import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'

import App from './App.vue'
import router from './router'
import { applyTheme, loadTheme } from './theme'
import './style.css'

// 首屏前先应用主题，避免闪白
applyTheme(loadTheme())

const app = createApp(App)

// 注册全部图标，模板里可直接用 <el-icon><UploadFilled /></el-icon>
for (const [name, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(name, component)
}

app.use(ElementPlus)
app.use(router)
app.mount('#app')
