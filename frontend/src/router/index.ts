import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import Home from '../views/Home.vue'
import Result from '../views/Result.vue'
import Detail from '../views/Detail.vue'
import Help from '../views/Help.vue'
import Settings from '../views/Settings.vue'

const routes: RouteRecordRaw[] = [
  { path: '/', name: 'home', component: Home },
  { path: '/result', name: 'result', component: Result },
  { path: '/detail', name: 'detail', component: Detail },
  { path: '/help', name: 'help', component: Help },
  { path: '/settings', name: 'settings', component: Settings },
  // 未知路径不渲染空白页：直接回首页（404 不是本应用的使用场景）
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
