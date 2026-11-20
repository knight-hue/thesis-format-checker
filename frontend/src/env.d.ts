/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_COLLEGE_ID?: string
  readonly VITE_USE_MOCK?: string
  readonly VITE_PROXY_TARGET?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
