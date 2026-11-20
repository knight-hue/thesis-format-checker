import axios from 'axios'
import type { AxiosRequestConfig } from 'axios'
import { isMockEnabled, mockAdapter } from '../mock'

export class ApiError extends Error {
  status?: number
  code?: string
  recoverable?: boolean
}

export function asApiError(error: unknown): ApiError {
  return error instanceof Error ? error : new ApiError(String(error || '网络请求失败'))
}

const client = axios.create({ baseURL: '/api', timeout: 30000 })
if (isMockEnabled()) client.defaults.adapter = mockAdapter

client.interceptors.response.use(
  (response) => {
    const body: unknown = response.data
    if (body && typeof body === 'object' && 'code' in body) {
      if (body.code === 0 || body.code === 200) {
        response.data = 'data' in body ? body.data : undefined
      } else {
        return Promise.reject(new ApiError(
          'msg' in body && typeof body.msg === 'string' && body.msg
            ? body.msg : `请求失败（code=${String(body.code)}）`,
        ))
      }
    }
    return response
  },
  (error: unknown) => {
    const body: unknown = axios.isAxiosError<unknown>(error) ? error.response?.data : undefined
    const detail = body && typeof body === 'object'
      ? ('msg' in body && body.msg) || ('detail' in body && body.detail) : undefined
    const normalized = new ApiError(typeof detail === 'string' ? detail : asApiError(error).message)
    if (axios.isAxiosError(error)) {
      normalized.code = error.code
      normalized.status = error.response?.status
    }
    return Promise.reject(normalized)
  },
)

// 此层统一解包，业务调用始终获得 Promise<T>。
const request = {
  async get<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    return (await client.get<T>(url, config)).data
  },
  async post<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return (await client.post<T>(url, data, config)).data
  },
}

export default request
