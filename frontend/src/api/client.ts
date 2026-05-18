import axios from 'axios'

export const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE ?? 'http://localhost:8000',
  timeout: 30_000,
  headers: { 'Content-Type': 'application/json' },
})

http.interceptors.response.use(
  res => res,
  err => {
    const msg = err.response?.data?.detail ?? err.message ?? '请求失败'
    return Promise.reject(new Error(typeof msg === 'string' ? msg : JSON.stringify(msg)))
  },
)
