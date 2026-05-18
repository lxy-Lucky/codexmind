import { http } from './client'
import type { SystemStatus } from '@/types'

export const statusApi = {
  get: () => http.get<SystemStatus>('/api/status').then(r => r.data),
}
