import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

// 接口地址统一封装、不写死：
//   - 开发默认走本地 Mock（src/mock，VITE_USE_MOCK=true）
//   - 联调时把 VITE_USE_MOCK 设为 false，/api 请求会代理转发到 VITE_PROXY_TARGET
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const proxyTarget = env.VITE_PROXY_TARGET || 'http://localhost:8000'

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      port: 5173,
      proxy: {
        // axios baseURL 为 /api，这里统一把 /api 前缀转发到后端
        '/api': {
          target: proxyTarget,
          changeOrigin: true,
        },
      },
    },
  }
})
