import axios from 'axios'
import { i18n, t } from '../i18n'

export const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE ?? 'http://localhost:8000',
  timeout: 30_000,
  headers: { 'Content-Type': 'application/json' },
})

http.interceptors.request.use(cfg => {
  cfg.headers.set('X-Lang', i18n.global.locale.value)
  return cfg
})

http.interceptors.response.use(
  res => res,
  err => {
    const msg = err.response?.data?.detail ?? err.message ?? t('errors.requestFailed')
    return Promise.reject(new Error(typeof msg === 'string' ? msg : JSON.stringify(msg)))
  },
)
