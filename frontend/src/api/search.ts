import { http } from './client'
import type { QueryHistory, SearchRequest, SearchResponse } from '@/types'

export const searchApi = {
  search: (req: SearchRequest) =>
    http.post<SearchResponse>('/api/search', req).then(r => r.data),

  history: (repo_id: string, limit = 50) =>
    http.get<QueryHistory[]>('/api/search/history', { params: { repo_id, limit } })
      .then(r => r.data),

  deleteHistory: (id: number) =>
    http.delete(`/api/search/history/${id}`),
}
